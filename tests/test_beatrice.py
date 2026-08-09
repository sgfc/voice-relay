"""beatrice.py の READY ハンドシェイクとフレーム往復 (偽ホスト使用)。"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import numpy as np
import pytest

from relay.beatrice import BeatriceHost

FAKE = Path(__file__).with_name("_fake_beatrice_host.py")


def _host() -> BeatriceHost:
    """偽ホストを起動する BeatriceHost。to_argv を差し替えて python で叩く。"""
    host = BeatriceHost(Path(sys.executable), object())
    host.params = types.SimpleNamespace(to_argv=lambda exe: [str(exe), str(FAKE)])
    return host


def test_ready_handshake() -> None:
    host = _host()
    host.start()
    try:
        assert host.wait_ready(timeout=10) == 0
        assert host.latency_samples == 0
    finally:
        host.stop()


def test_process_roundtrip() -> None:
    host = _host()
    host.start()
    try:
        host.wait_ready(timeout=10)
        block = np.linspace(-0.5, 0.5, 480, dtype=np.float32)
        out = host.process(block)
        assert out.shape == (480,)
        assert np.allclose(out, block)  # 偽ホストは恒等変換
    finally:
        host.stop()


def test_wait_ready_times_out_if_no_ready() -> None:
    """READY を出さないプロセスでは TimeoutError になる。"""
    host = BeatriceHost(Path(sys.executable), object())
    # 何も出力せず少し待つだけのプロセス
    host.params = types.SimpleNamespace(
        to_argv=lambda exe: [str(exe), "-c", "import time; time.sleep(5)"]
    )
    host.start()
    try:
        with pytest.raises(TimeoutError):
            host.wait_ready(timeout=1.0)
    finally:
        host.stop()
