"""audio.py: インデックス指定と候補一覧つきエラーの追加分。"""
from __future__ import annotations

import pytest

from relay.audio import find_device, format_devices

_DEVICES = [
    {"name": "Microphone (Realtek)", "max_input_channels": 2, "max_output_channels": 0},
    {"name": "CABLE-A Output (VB-Audio Cable A)", "max_input_channels": 2, "max_output_channels": 0},
    {"name": "CABLE-B Input (VB-Audio Virtual Cable B)", "max_input_channels": 0, "max_output_channels": 2},
]


def test_find_device_by_index() -> None:
    assert find_device(1, "input", _DEVICES) == 1
    assert find_device("2", "output", _DEVICES) == 2  # 数字文字列も可 (CLI 経由)


def test_find_device_index_wrong_direction() -> None:
    with pytest.raises(LookupError, match="not a valid input"):
        find_device(2, "input", _DEVICES)  # 2 は再生専用


def test_find_device_index_out_of_range() -> None:
    with pytest.raises(LookupError):
        find_device(99, "output", _DEVICES)


def test_error_lists_candidates() -> None:
    with pytest.raises(LookupError) as exc:
        find_device("NoSuchDevice", "input", _DEVICES)
    msg = str(exc.value)
    assert "Microphone (Realtek)" in msg
    assert "CABLE-A Output" in msg
    assert "CABLE-B Input" not in msg  # output 専用は input 候補に出ない


def test_format_devices_filters_by_kind() -> None:
    out = format_devices("output", _DEVICES)
    assert "CABLE-B Input" in out
    assert "Microphone" not in out
