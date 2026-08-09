"""control.py の送出トラック (BeatriceTrack) と _mono_frame。"""
from __future__ import annotations

import asyncio

import numpy as np

from relay.control import BeatriceTrack, _mono_frame
from relay.engine import Engine


class DummyEngine(Engine):
    """pull_ai_audio が既知ブロックを返すだけのエンジン。"""

    def __init__(self, block: np.ndarray) -> None:
        self.block = block
        self.pushed: list[np.ndarray] = []

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def push_user_audio(self, block: np.ndarray) -> None:
        self.pushed.append(block)
    def pull_ai_audio(self) -> np.ndarray | None:
        return self.block


def test_mono_frame_shape_and_values() -> None:
    block = np.array([0.0, 0.5, -0.5, 1.0], dtype=np.float32)
    frame = _mono_frame(block, 48000, pts=960)
    assert frame.sample_rate == 48000
    assert frame.samples == 4
    assert frame.format.name == "s16"
    assert frame.pts == 960
    got = frame.to_ndarray()[0]
    np.testing.assert_array_equal(got, [0, 16383, -16383, 32767])


def test_beatrice_track_produces_frames() -> None:
    async def scenario() -> None:
        block = np.full(480, 0.25, dtype=np.float32)
        engine = DummyEngine(block)
        track = BeatriceTrack(lambda: (engine, None))

        f1 = await track.recv()
        f2 = await track.recv()

        assert f1.samples == 480 and f1.sample_rate == 48000
        assert f1.pts == 0 and f2.pts == 480  # pts が 480 ずつ進む
        # 0.25 -> int16 (astype による切り捨て)
        np.testing.assert_array_equal(f1.to_ndarray()[0], np.full(480, int(0.25 * 32767)))

    asyncio.run(scenario())


def test_beatrice_track_silence_on_underflow() -> None:
    async def scenario() -> None:
        class Empty(DummyEngine):
            def pull_ai_audio(self):
                return None

        track = BeatriceTrack(lambda: (Empty(np.zeros(0, np.float32)), None))
        f = await track.recv()
        assert f.samples == 480
        np.testing.assert_array_equal(f.to_ndarray()[0], np.zeros(480, dtype=np.int16))

    asyncio.run(scenario())


def test_beatrice_track_silence_before_pipeline_ready() -> None:
    """パイプライン起動前 (engine=None) は無音を送る (並列起動の要)。"""

    async def scenario() -> None:
        track = BeatriceTrack(lambda: (None, None))
        f = await track.recv()
        assert f.samples == 480
        np.testing.assert_array_equal(f.to_ndarray()[0], np.zeros(480, dtype=np.int16))

    asyncio.run(scenario())
