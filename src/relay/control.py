"""制御・監視 HTTP サーバー (aiohttp) + WebRTC デーモン (aiortc)。

iOS ショートカット / ブラウザから叩く。docs/spec.md「P2/P3」に対応。

エンドポイント:
  GET  /         : web/index.html (iOS 用 WebRTC クライアント) を配信
  POST /offer    : WebRTC シグナリング。SDP offer を受け取り answer を返す。
  POST /start    : セッション開始の合図 (P4 でブラウザ起動を接続)。冪等。
  POST /stop     : 全 PeerConnection とパイプラインを閉じる。冪等。
  GET  /status   : セッション状態 (idle / connecting / active) と接続数。

モード (--mode):
  echo    : 受信音声をそのままエコーバック (P2。接続と I/O の確認用)。
  browser : 本経路 (P3)。
    - 上り: iOS の声 -> engine.push_user_audio -> CABLE-B -> ブラウザマイク (無変換)
    - 下り: CABLE-A -> engine.pull_ai_audio -> [beatrice 変換] -> iOS へ送出
    beatrice 未設定時は CABLE 橋渡しのみ (AI 原音を無変換で返す。段階検証用)。

デッドマンスイッチ (_watchdog): パイプライン稼働中、接続が猶予時間
(deadman_grace, 初回は deadman_initial_grace) を超えて失われたら、
ブラウザ taskkill 込みで全停止して idle に戻す。
"""
from __future__ import annotations

import asyncio
import logging
from enum import Enum
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
from aiohttp import web
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRelay
from aiortc.mediastreams import MediaStreamError, MediaStreamTrack

from .audio import BLOCK_SAMPLES, SAMPLE_RATE
from .beatrice import BeatriceHost, BeatriceParams
from .config import RelayConfig
from .engine import BrowserDeviceEngine, Engine

log = logging.getLogger("relay.control")

WEB_DIR = Path(__file__).resolve().parents[2] / "web"
INDEX_HTML = WEB_DIR / "index.html"


class SessionState(str, Enum):
    IDLE = "idle"
    CONNECTING = "connecting"
    ACTIVE = "active"


def _mono_frame(block: np.ndarray, sample_rate: int, pts: int) -> av.AudioFrame:
    """float32 mono ブロックを 48kHz s16 mono の av.AudioFrame にする (送出用)。"""
    pcm = np.clip(block, -1.0, 1.0)
    i16 = (pcm * 32767.0).astype(np.int16).reshape(1, -1)
    frame = av.AudioFrame.from_ndarray(i16, format="s16", layout="mono")
    frame.sample_rate = sample_rate
    frame.pts = pts
    frame.time_base = Fraction(1, sample_rate)
    return frame


class BeatriceTrack(MediaStreamTrack):
    """下り送出トラック: engine.pull_ai_audio -> [beatrice 変換] -> iOS。

    リアルタイム基準は壁時計 (10ms/480 サンプル)。取り出せない間は無音を送る。
    beatrice.process はブロッキングなのでスレッドプールへ逃がす。

    パイプラインは get_pipeline() で毎フレーム参照する。起動完了前 (engine が
    None) は無音を送るので、WebRTC 接続の確立とブラウザ/beatrice の起動を
    並列に進められる。
    """

    kind = "audio"

    def __init__(
        self,
        get_pipeline,  # callable() -> (Engine | None, BeatriceHost | None)
        samples: int = BLOCK_SAMPLES,
        sample_rate: int = SAMPLE_RATE,
        on_failure=None,  # async callable(reason: str)。処理不能が確定した時に呼ぶ
    ) -> None:
        super().__init__()
        self._get_pipeline = get_pipeline
        self._samples = samples
        self._rate = sample_rate
        self._on_failure = on_failure
        self._timestamp = 0
        self._start: float | None = None
        self._loop = asyncio.get_event_loop()

    async def recv(self) -> av.AudioFrame:
        now = self._loop.time()
        if self._start is None:
            self._start = now
        target = self._start + (self._timestamp + self._samples) / self._rate
        delay = target - now
        if delay > 0:
            await asyncio.sleep(delay)

        try:
            engine, beatrice = self._get_pipeline()
            block = engine.pull_ai_audio() if engine is not None else None
            if block is None:
                block = np.zeros(self._samples, dtype=np.float32)
            elif beatrice is not None:
                block = await self._loop.run_in_executor(
                    None, beatrice.process, block
                )
        except Exception as e:
            # beatrice プロセス死亡・デバイス喪失など。無音のまま接続と
            # ブラウザ (会話モード) を生かし続けないよう、全停止を仕掛ける。
            log.error("downlink processing failed: %s", e)
            if self._on_failure is not None:
                asyncio.ensure_future(self._on_failure(f"downlink failed: {e}"))
            self.stop()
            raise MediaStreamError("downlink processing failed") from e

        frame = _mono_frame(block, self._rate, self._timestamp)
        self._timestamp += self._samples
        return frame


class RelayServer:
    """WebRTC デーモン本体。aiohttp の Application を組み立てて保持する。"""

    def __init__(self, config: RelayConfig | None = None) -> None:
        self.config = config or RelayConfig()
        self._pcs: set[RTCPeerConnection] = set()
        self._relay = MediaRelay()
        self._state = SessionState.IDLE
        self._engine: Engine | None = None
        self._beatrice: BeatriceHost | None = None
        self._pipeline_lock = asyncio.Lock()
        self._watchdog_task: asyncio.Task | None = None
        self._last_error: str | None = None

        self.app = web.Application()
        self.app.router.add_get("/", self._index)
        self.app.router.add_post("/offer", self._offer)
        self.app.router.add_post("/start", self._start)
        self.app.router.add_post("/stop", self._stop)
        self.app.router.add_get("/status", self._status)
        self.app.on_shutdown.append(self._on_shutdown)

    # -- routes ----------------------------------------------------------

    async def _index(self, request: web.Request) -> web.StreamResponse:
        if not INDEX_HTML.exists():
            return web.Response(status=404, text="web/index.html not found")
        return web.FileResponse(INDEX_HTML)

    async def _offer(self, request: web.Request) -> web.Response:
        """SDP offer を受けて answer を返す。失敗は JSON {"error": ...} で返す
        (プレーンテキスト 500 を返すと iOS 側が SDP として解釈しようとして
        Safari の不可解なエラーになるため)。"""
        params = await request.json()
        offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

        # STUN を使わない (iceServers 空)。経路は常に Tailscale/LAN でホスト候補
        # だけで届くため、aiortc デフォルトの Google STUN への問い合わせ待ち
        # (全インターフェース分、環境により数秒) を丸ごと省いて接続を速くする。
        pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        self._pcs.add(pc)
        self._state = SessionState.CONNECTING
        log.info("offer received (mode=%s); peer connections=%d", self.config.mode, len(self._pcs))

        if self.config.mode == "browser":
            # パイプライン起動は待たない: answer を即返して WebRTC 接続と
            # ブラウザ/beatrice の立ち上げを並列に進める。準備完了までの下りは
            # BeatriceTrack が無音を送る。起動失敗はバックグラウンドで
            # _abort_session に流れ、接続が閉じられて iOS 側に伝わる。
            asyncio.ensure_future(self._ensure_pipeline_bg())
            pc.addTrack(
                BeatriceTrack(
                    lambda: (self._engine, self._beatrice),
                    on_failure=self._abort_session,
                )
            )

        @pc.on("connectionstatechange")
        async def on_connectionstatechange() -> None:
            log.info("connection state -> %s", pc.connectionState)
            if pc.connectionState == "connected":
                self._state = SessionState.ACTIVE
            elif pc.connectionState in ("failed", "closed", "disconnected"):
                # 死んだ PC の掃除のみ。猶予付きの全停止 (デッドマンスイッチ) は
                # _watchdog が状態ポーリングで担う。
                await self._discard(pc)

        @pc.on("track")
        def on_track(track: MediaStreamTrack) -> None:
            log.info("track received: kind=%s", track.kind)
            if track.kind == "audio":
                if self.config.mode == "echo":
                    pc.addTrack(self._relay.subscribe(track))
                else:  # browser: iOS の声を CABLE-B へ流す (無変換)
                    asyncio.ensure_future(self._pump_inbound(track))

            @track.on("ended")
            async def on_ended() -> None:
                log.info("track ended: kind=%s", track.kind)

        try:
            await pc.setRemoteDescription(offer)
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
        except Exception as e:
            # バックグラウンド起動が即失敗して _abort_session が SDP 交換中の
            # pc を閉じたケース等。原因 (あれば _last_error) を JSON で返す。
            log.exception("SDP negotiation failed")
            await self._discard(pc)
            return web.json_response(
                {"error": self._last_error or f"SDP negotiation failed: {e}"},
                status=500,
            )

        return web.json_response(
            {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}
        )

    async def _start(self, request: web.Request) -> web.Response:
        """パイプライン (ブラウザ起動 + クリック + beatrice) を立ち上げる。冪等。

        iOS ショートカットからの合図を想定。ブラウザ起動とモデル読み込みで
        数秒〜十数秒かかるので、完了してから応答を返す。
        """
        log.info("/start")
        if self.config.mode == "browser":
            try:
                await self._ensure_pipeline()
                self._last_error = None
            except Exception as e:
                self._last_error = f"パイプライン起動に失敗: {e}"
                log.exception("pipeline startup failed")
                return web.json_response({"error": self._last_error}, status=500)
        return web.json_response({"state": self._state.value})

    async def _stop(self, request: web.Request) -> web.Response:
        log.info("/stop; closing %d peer connection(s)", len(self._pcs))
        await self._close_all()
        await self._teardown_pipeline()
        self._state = SessionState.IDLE
        return web.json_response({"state": self._state.value})

    async def _status(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "state": self._state.value,
                "mode": self.config.mode,
                "connections": len(self._pcs),
                "beatrice": self._beatrice is not None,
                "error": self._last_error,
            }
        )

    # -- inbound (iOS -> CABLE-C) ----------------------------------------

    async def _pump_inbound(self, track: MediaStreamTrack) -> None:
        """受信音声を mono float32 48kHz に整えて engine へ流し続ける。

        パイプライン起動が並列で走るため、engine 未起動の間は捨てる
        (どうせブラウザ側もまだ聞いていない)。"""
        resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
        while True:
            try:
                frame = await track.recv()
            except MediaStreamError:
                break
            engine = self._engine
            if engine is None:
                continue
            out = resampler.resample(frame)
            for f in out if isinstance(out, list) else [out]:
                arr = f.to_ndarray()[0].astype(np.float32) / 32768.0
                engine.push_user_audio(arr)

    # -- pipeline (engine + beatrice) lifecycle --------------------------

    async def _ensure_pipeline_bg(self) -> None:
        """/offer からのバックグラウンド起動。失敗は全停止に変換して知らせる
        (接続だけ生きて無音、という状態を作らない)。"""
        try:
            await self._ensure_pipeline()
            self._last_error = None
        except Exception as e:
            self._last_error = f"パイプライン起動に失敗: {e}"
            log.exception("pipeline startup failed (background)")
            await self._abort_session(self._last_error)

    async def _ensure_pipeline(self) -> None:
        async with self._pipeline_lock:
            try:
                await self._ensure_pipeline_locked()
            except Exception:
                # 途中失敗 (例: ブラウザ起動後に beatrice が落ちた) を放置すると
                # 会話モードが走りっぱなしになるため、起動済み分を巻き戻す。
                await self._teardown_locked()
                raise

    async def _ensure_pipeline_locked(self) -> None:
        if self._engine is None:
            browser = None
            if self.config.browser is not None:
                from .browser import BrowserController

                browser = BrowserController(self.config.browser)
            engine = BrowserDeviceEngine(
                self.config.cable_a, self.config.cable_b, browser=browser
            )
            await engine.start()
            self._engine = engine
            log.info("engine started (CABLE-A=%r, CABLE-B=%r, browser=%s)",
                     self.config.cable_a, self.config.cable_b,
                     "auto" if browser else "manual")
        if self._beatrice is None and self.config.beatrice_configured:
            params = BeatriceParams(
                plugin=self.config.plugin,
                model=self.config.model,
                voice=self.config.voice,
                **self.config.beatrice_tuning,
            )
            host = BeatriceHost(self.config.beatrice_exe, params)
            latency = await asyncio.get_event_loop().run_in_executor(
                None, self._start_beatrice, host
            )
            self._beatrice = host
            log.info("beatrice ready (latency=%d samples)", latency)
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = asyncio.ensure_future(self._watchdog())

    @staticmethod
    def _start_beatrice(host: BeatriceHost) -> int:
        host.start()
        return host.wait_ready()

    async def _teardown_pipeline(self) -> None:
        task, self._watchdog_task = self._watchdog_task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        async with self._pipeline_lock:
            await self._teardown_locked()

    async def _teardown_locked(self) -> None:
        """_pipeline_lock 保持中に呼ぶ実体。beatrice -> engine (ブラウザ含む) を止める。"""
        if self._beatrice is not None:
            host, self._beatrice = self._beatrice, None
            await asyncio.get_event_loop().run_in_executor(None, host.stop)
        if self._engine is not None:
            engine, self._engine = self._engine, None
            await engine.stop()

    async def _abort_session(self, reason: str) -> None:
        """異常が確定した時点での全停止。ブラウザも taskkill する
        (会話モードの時間を消費させない・復帰不能なまま放置しない)。"""
        log.error("aborting session: %s", reason)
        try:
            # 先にブラウザ/beatrice を止める (会話モードの消費を最優先で断つ)。
            # pc.close() はハンドシェイク中だとハングしうるので後回し + タイムアウト。
            await self._teardown_pipeline()
            await self._close_all()
        finally:
            # 途中で何か失敗しても状態だけは必ず idle に戻す
            self._state = SessionState.IDLE

    async def _watchdog(self) -> None:
        """デッドマンスイッチ: 接続が猶予時間を超えて失われたら全停止する。

        - 初回接続前は deadman_initial_grace (/start からユーザーが Safari を
          開くまでの時間を見込む)。
        - 一度でも接続が立った後は deadman_grace (docs/spec.md: 15-30 秒)。
        ICE の failed イベントに頼らず状態をポーリングするため、クライアントが
        黙って消えたケースも拾える。
        """
        loop = asyncio.get_event_loop()
        connected_once = False
        last_ok = loop.time()
        try:
            while True:
                await asyncio.sleep(min(2.0, self.config.deadman_grace / 4))
                now = loop.time()
                if any(pc.connectionState == "connected" for pc in self._pcs):
                    connected_once = True
                    last_ok = now
                    continue
                grace = (
                    self.config.deadman_grace
                    if connected_once
                    else self.config.deadman_initial_grace
                )
                if now - last_ok > grace:
                    log.warning(
                        "deadman: no connection for %.0fs -> shutting down pipeline",
                        now - last_ok,
                    )
                    await self._close_all()
                    await self._teardown_pipeline()
                    self._state = SessionState.IDLE
                    return
        except asyncio.CancelledError:
            pass

    # -- peer connection lifecycle --------------------------------------

    async def _discard(self, pc: RTCPeerConnection) -> None:
        """1 つの PeerConnection を閉じて集合から除く (冪等)。"""
        if pc not in self._pcs:
            return
        self._pcs.discard(pc)
        await pc.close()
        if not self._pcs:
            self._state = SessionState.IDLE

    async def _close_all(self) -> None:
        pcs, self._pcs = list(self._pcs), set()
        for pc in pcs:
            try:
                # ハンドシェイク中の close はハング・失敗しうる。掃除は続行する
                await asyncio.wait_for(pc.close(), timeout=3)
            except Exception:
                log.debug("pc.close() failed or timed out", exc_info=True)

    async def _on_shutdown(self, app: web.Application) -> None:
        await self._close_all()
        await self._teardown_pipeline()


def build_app(config: RelayConfig | None = None) -> web.Application:
    """aiohttp Application を返す (テスト・外部起動用)。"""
    return RelayServer(config).app


def run(host: str = "0.0.0.0", port: int = 8080, config: RelayConfig | None = None) -> None:
    """デーモンを起動する。

    注意 (docs/spec.md「罠」): ブラウザ自動操作を伴う本番運用では、この
    プロセスをログオンセッション内のスタートアップに常駐させること。
    SSH/WSL 等の非対話コンテキストからだと OS レベル入力注入が失敗する。
    """
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )
    web.run_app(build_app(config), host=host, port=port)
