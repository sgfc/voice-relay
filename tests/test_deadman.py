"""デッドマンスイッチ (_watchdog) の動作。

実 CABLE デバイスに依存しないよう、エンジンをダミーに差し替えて
「接続が無いまま猶予を超えたらパイプラインが停止する」ことを検証する。
"""
from __future__ import annotations

import asyncio

import numpy as np

from relay.config import RelayConfig
from relay.control import RelayServer, SessionState
from relay.engine import Engine


class DummyEngine(Engine):
    def __init__(self) -> None:
        self.stopped = False

    async def start(self) -> None: ...
    async def stop(self) -> None:
        self.stopped = True
    def push_user_audio(self, block: np.ndarray) -> None: ...
    def pull_ai_audio(self) -> np.ndarray | None:
        return None


def test_deadman_tears_down_without_connection() -> None:
    async def scenario() -> None:
        config = RelayConfig(
            mode="browser", deadman_grace=0.5, deadman_initial_grace=0.5
        )
        server = RelayServer(config)
        engine = DummyEngine()
        server._engine = engine
        server._state = SessionState.ACTIVE
        server._watchdog_task = asyncio.ensure_future(server._watchdog())

        await asyncio.sleep(2.0)  # 猶予 0.5s + ポーリング間隔を跨ぐ

        assert engine.stopped, "watchdog がエンジンを停止していない"
        assert server._engine is None
        assert server._state == SessionState.IDLE
        assert server._watchdog_task is None or server._watchdog_task.done()

    asyncio.run(scenario())


def test_deadman_cancelled_on_manual_teardown() -> None:
    async def scenario() -> None:
        config = RelayConfig(mode="browser", deadman_grace=60, deadman_initial_grace=60)
        server = RelayServer(config)
        server._engine = DummyEngine()
        server._watchdog_task = asyncio.ensure_future(server._watchdog())
        await asyncio.sleep(0.1)

        await server._teardown_pipeline()  # /stop 相当
        await asyncio.sleep(0.1)

        assert server._watchdog_task is None
        assert server._engine is None

    asyncio.run(scenario())
