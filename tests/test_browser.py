"""browser.py: ウィンドウ前面化のベストエフォート動作。"""
from __future__ import annotations

import subprocess
import sys

from relay.browser import bring_to_foreground


def test_bring_to_foreground_missing_pid_returns_false() -> None:
    """存在しない pid では False を返すだけで例外にしない (保険動作の要件)。"""
    assert bring_to_foreground(0x7FFFFFFF) is False


def test_bring_to_foreground_windowless_process_returns_false() -> None:
    """ウィンドウを持たないプロセスでも False (クリック続行を妨げない)。"""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    try:
        assert bring_to_foreground(proc.pid) is False
    finally:
        proc.kill()
