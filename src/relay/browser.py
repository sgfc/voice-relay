"""外部ブラウザの自動操作: 起動・座標クリック・taskkill。

docs/spec.md の決定事項に従う:
- DOM 自動化 (Playwright 等) は使わない (ChatGPT のログインが自動化検知と衝突)。
- OS レベルの座標クリック (SendInput) は本物のユーザー操作と区別されず、
  マイク許可のジェスチャ要件も満たす。
- 入力注入はログオンセッション内のプロセスからのみ有効 (SSH/WSL 不可)。
- 専用プロファイル + 固定ウィンドウ位置/サイズで座標の安定性を確保。
"""
from __future__ import annotations

import asyncio
import ctypes
import logging
import subprocess
import sys
from ctypes import wintypes

from .config import BrowserConfig

log = logging.getLogger("relay.browser")

# --- SendInput (Windows) ------------------------------------------------

_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_ABSOLUTE = 0x8000


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT)]

    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def click_at(x: int, y: int) -> None:
    """物理ピクセル座標 (x, y) を左クリックする (SendInput)。

    DPI 仮想化で座標がずれないよう DPI aware を宣言してから送る。
    ロック画面・スリープ中は届かない (docs/spec.md「罠」)。
    """
    if sys.platform != "win32":
        raise RuntimeError("click_at is Windows-only")
    user32 = ctypes.windll.user32
    user32.SetProcessDPIAware()
    screen_w = user32.GetSystemMetrics(0)
    screen_h = user32.GetSystemMetrics(1)
    nx = int(x * 65535 / (screen_w - 1))
    ny = int(y * 65535 / (screen_h - 1))

    def send(flags: int, dx: int = 0, dy: int = 0) -> None:
        inp = _INPUT(type=0)  # INPUT_MOUSE
        inp.mi = _MOUSEINPUT(dx, dy, 0, flags, 0, None)
        if user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT)) != 1:
            raise OSError("SendInput failed (非対話セッションから起動していないか確認)")

    send(_MOUSEEVENTF_MOVE | _MOUSEEVENTF_ABSOLUTE, nx, ny)
    send(_MOUSEEVENTF_LEFTDOWN)
    send(_MOUSEEVENTF_LEFTUP)


def probe_cursor_loop() -> None:
    """カーソル座標を1秒ごとに表示する (クリック座標の調べ方: 目的のボタンに
    カーソルを置いて値を読む)。Ctrl+C で終了。"""
    user32 = ctypes.windll.user32
    user32.SetProcessDPIAware()
    pt = wintypes.POINT()
    print("目的のボタンにカーソルを合わせて座標を読み取ってください (Ctrl+C で終了)")
    import time

    try:
        while True:
            user32.GetCursorPos(ctypes.byref(pt))
            print(f"\rcursor: x={pt.x:5d} y={pt.y:5d}", end="", flush=True)
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n読み取った x/y を relay.toml の [[browser.clicks]] に記入してください")


# --- controller ---------------------------------------------------------


class BrowserController:
    """ブラウザプロセスの起動 -> クリックシーケンス -> 停止 (taskkill)。"""

    def __init__(self, config: BrowserConfig) -> None:
        self.config = config
        self._proc: subprocess.Popen | None = None

    async def start(self) -> None:
        """ブラウザを起動し、設定されたクリックを順に実行する。冪等。"""
        if self._proc is not None and self._proc.poll() is None:
            log.info("browser already running (pid=%d)", self._proc.pid)
            return
        argv = self.config.to_argv()
        log.info("launching browser: %s", " ".join(argv))
        self._proc = subprocess.Popen(argv)
        for i, step in enumerate(self.config.clicks):
            await asyncio.sleep(step.delay)
            log.info("click %d/%d at (%d, %d)", i + 1, len(self.config.clicks), step.x, step.y)
            click_at(step.x, step.y)

    def stop(self) -> None:
        """ブラウザをプロセスツリーごと止める (taskkill /T /F)。冪等。"""
        proc, self._proc = self._proc, None
        if proc is None:
            return
        if proc.poll() is None:
            # 専用プロファイルの起動プロセスが親のため、/T で配下ごと落とせる。
            # ユーザーが普段使いしている別プロファイルの Chrome には影響しない。
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
            )
        try:
            proc.wait(timeout=5)
        except Exception:
            pass

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None
