"""異常確定時の全停止 (ブラウザ含む) の検証。

- パイプライン途中失敗: 起動済みのエンジン (=ブラウザ) を巻き戻す
- 会話中の下り処理失敗: on_failure 経由で全停止する
"""
from __future__ import annotations

import asyncio

import numpy as np
import pytest
from aiortc.mediastreams import MediaStreamError

import relay.control as control
from relay.config import RelayConfig
from relay.control import BeatriceTrack, RelayServer, SessionState
from relay.engine import Engine


class DummyEngine(Engine):
    def __init__(self, *args, **kwargs) -> None:
        self.stopped = False

    async def start(self) -> None: ...
    async def stop(self) -> None:
        self.stopped = True
    def push_user_audio(self, block: np.ndarray) -> None: ...
    def pull_ai_audio(self) -> np.ndarray | None:
        return None


def test_partial_pipeline_failure_rolls_back_engine(monkeypatch) -> None:
    """エンジン (ブラウザ) 起動後に beatrice が落ちたら、エンジンも止める。"""

    async def scenario() -> None:
        created: list[DummyEngine] = []

        def make_engine(*args, **kwargs) -> DummyEngine:
            e = DummyEngine()
            created.append(e)
            return e

        monkeypatch.setattr(control, "BrowserDeviceEngine", make_engine)

        config = RelayConfig(
            mode="browser",
            beatrice_exe="Z:/no/such/host.exe",  # Popen が FileNotFoundError
            plugin="p.vst3",
            model="m.toml",
        )
        server = RelayServer(config)
        with pytest.raises(Exception):
            await server._ensure_pipeline()

        assert created, "エンジンが作られていない"
        assert created[0].stopped, "途中失敗でエンジン (ブラウザ) が止まっていない"
        assert server._engine is None
        assert server._beatrice is None

    asyncio.run(scenario())


def test_downlink_failure_triggers_abort() -> None:
    """会話中に beatrice が死んだら on_failure が呼ばれ、トラックは終了する。"""

    async def scenario() -> None:
        class BrokenBeatrice:
            def process(self, block: np.ndarray) -> np.ndarray:
                raise RuntimeError("host closed stdout")

        reasons: list[str] = []

        async def on_failure(reason: str) -> None:
            reasons.append(reason)

        class Feeding(DummyEngine):
            def pull_ai_audio(self):
                return np.zeros(480, dtype=np.float32)

        track = BeatriceTrack(
            lambda: (Feeding(), BrokenBeatrice()), on_failure=on_failure
        )
        with pytest.raises(MediaStreamError):
            await track.recv()
        await asyncio.sleep(0.05)  # ensure_future の実行を待つ

        assert reasons and "host closed stdout" in reasons[0]
        assert track.readyState == "ended"

    asyncio.run(scenario())


def test_abort_session_full_stop() -> None:
    """_abort_session はパイプラインを畳んで idle に戻す。"""

    async def scenario() -> None:
        server = RelayServer(RelayConfig(mode="browser"))
        engine = DummyEngine()
        server._engine = engine
        server._state = SessionState.ACTIVE

        await server._abort_session("test reason")

        assert engine.stopped
        assert server._engine is None
        assert server._state == SessionState.IDLE

    asyncio.run(scenario())
