"""会話エンジンの抽象化。

エンジンは差し替え可能な部品として扱う (docs/spec.md 参照):
- BrowserDeviceEngine: Web版 ChatGPT Live を外部ブラウザ+仮想ケーブル経由で使う (現行)
- LiveApiEngine:       GPT-Live API 解禁後にここへ差し替える (将来)
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

from .audio import (
    BLOCK_SAMPLES,
    DEVICE_CABLE_A_CAPTURE,
    DEVICE_CABLE_B_RENDER,
    SAMPLE_RATE,
    SampleFifo,
    find_device,
)


class Engine(ABC):
    """AI との音声往復を抽象化する。48kHz mono float32、10ms ブロック前提。"""

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    def push_user_audio(self, block: np.ndarray) -> None:
        """iOS 側 (AEC 済み) の音声をエンジンへ送る。"""

    @abstractmethod
    def pull_ai_audio(self) -> np.ndarray | None:
        """AI の応答音声 (Beatrice 変換前の原音) を取り出す。無ければ None。"""


class BrowserDeviceEngine(Engine):
    """外部ブラウザ (アプリモード・専用プロファイル) + CABLE-A/B を橋渡しする実装。

    - push_user_audio: iOS の声を CABLE-B Input (再生) へ書き込む → ブラウザマイク。
    - pull_ai_audio:   CABLE-A Output (録音) から AI 原音を取り出す (変換前)。

    PortAudio コールバック (別スレッド) と FIFO で受け渡す。レート整合は
    仮想ケーブルを 48kHz に設定して回避する前提 (docs/spec.md)。サウンドカード
    クロックと WebRTC 送出クロックの微小ドリフトは FIFO が吸収する
    (長時間通話でごく稀にグリッチ。ドリフト補償は P4 候補)。

    browser (BrowserController) を渡すと start/stop でブラウザの起動
    (アプリモード + 座標クリック) と taskkill も行う。未指定なら手動運用。
    """

    def __init__(
        self,
        cable_a: str | int = DEVICE_CABLE_A_CAPTURE,
        cable_b: str | int = DEVICE_CABLE_B_RENDER,
        sample_rate: int = SAMPLE_RATE,
        block: int = BLOCK_SAMPLES,
        browser=None,  # BrowserController | None (未指定ならブラウザは手動運用)
    ) -> None:
        self._cable_a = cable_a
        self._cable_b = cable_b
        self._rate = sample_rate
        self._block = block
        self._browser = browser
        self._to_browser = SampleFifo()    # push_user_audio -> CABLE-B render
        self._from_browser = SampleFifo()  # CABLE-A capture -> pull_ai_audio
        self._in_stream = None
        self._out_stream = None

    async def start(self) -> None:
        import sounddevice as sd

        cin = find_device(self._cable_a, "input")
        cout = find_device(self._cable_b, "output")
        self._in_stream = sd.InputStream(
            device=cin,
            channels=1,
            samplerate=self._rate,
            blocksize=self._block,
            dtype="float32",
            callback=self._on_capture,
        )
        self._out_stream = sd.OutputStream(
            device=cout,
            channels=1,
            samplerate=self._rate,
            blocksize=self._block,
            dtype="float32",
            callback=self._on_render,
        )
        self._in_stream.start()
        self._out_stream.start()
        if self._browser is not None:
            await self._browser.start()  # 起動 + 音声モードの座標クリック

    async def stop(self) -> None:
        # ブラウザを先に落とす (ChatGPT 側の会話セッションを確実に終える)
        if self._browser is not None:
            self._browser.stop()
        for stream in (self._in_stream, self._out_stream):
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
        self._in_stream = None
        self._out_stream = None
        self._to_browser.clear()
        self._from_browser.clear()

    # -- PortAudio callbacks (別スレッド) --------------------------------

    def _on_capture(self, indata, frames, time_info, status) -> None:
        self._from_browser.push(indata[:, 0])

    def _on_render(self, outdata, frames, time_info, status) -> None:
        outdata[:, 0] = self._to_browser.pop(frames)

    # -- Engine 契約 -----------------------------------------------------

    def push_user_audio(self, block: np.ndarray) -> None:
        self._to_browser.push(block)

    def pull_ai_audio(self) -> np.ndarray | None:
        if self._from_browser.available() < self._block:
            return None
        return self._from_browser.pop(self._block)
