"""audio.py: SampleFifo と find_device のユニットテスト。"""
from __future__ import annotations

import numpy as np
import pytest

from relay.audio import SampleFifo, find_device


def test_fifo_push_pop_order() -> None:
    f = SampleFifo()
    f.push(np.array([1, 2, 3], dtype=np.float32))
    f.push(np.array([4, 5], dtype=np.float32))
    assert f.available() == 5
    np.testing.assert_array_equal(f.pop(4), [1, 2, 3, 4])
    assert f.available() == 1


def test_fifo_underflow_zero_pads() -> None:
    f = SampleFifo()
    f.push(np.array([9, 8], dtype=np.float32))
    out = f.pop(5)
    assert out.shape == (5,)
    np.testing.assert_array_equal(out, [9, 8, 0, 0, 0])
    assert f.available() == 0


def test_fifo_overflow_drops_oldest() -> None:
    f = SampleFifo(max_samples=4)
    f.push(np.arange(6, dtype=np.float32))  # 0..5 -> 末尾 4 個だけ残る
    assert f.available() == 4
    np.testing.assert_array_equal(f.pop(4), [2, 3, 4, 5])


_DEVICES = [
    {"name": "Microphone (Realtek)", "max_input_channels": 2, "max_output_channels": 0},
    {"name": "CABLE-A Output (VB-Audio Cable A)", "max_input_channels": 2, "max_output_channels": 0},
    {"name": "CABLE-B Input (VB-Audio Virtual Cable B)", "max_input_channels": 0, "max_output_channels": 2},
]


def test_find_device_partial_match() -> None:
    assert find_device("CABLE-A Output", "input", _DEVICES) == 1
    assert find_device("CABLE-B Input", "output", _DEVICES) == 2


def test_find_device_respects_direction() -> None:
    # CABLE-B Input は録音チャンネルを持たないので input では見つからない
    with pytest.raises(LookupError):
        find_device("CABLE-B Input", "input", _DEVICES)


def test_find_device_missing() -> None:
    with pytest.raises(LookupError):
        find_device("NoSuchDevice", "output", _DEVICES)
