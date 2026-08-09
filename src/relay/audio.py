"""オーディオ I/O の定数とユーティリティ。

全区間 48kHz mono float32、ブロック 480 サンプル (10ms) に統一する。
リサンプル不要 (Web Live 経路はブラウザが共有モードレートで動くため、
仮想ケーブルを 48kHz に設定しておけばレート変換は発生しない)。
"""
from __future__ import annotations

import threading

import numpy as np

SAMPLE_RATE = 48_000
BLOCK_SAMPLES = 480  # 10ms

# Windows のサウンド設定で 48kHz に揃えておくこと。
# VB-Cable の "Input" は再生デバイス (ここへ書き込む)、"Output" は録音デバイス
# (ここから読む) である点に注意。
# A+B パック構成: A = ブラウザ→デーモン、B = デーモン→ブラウザマイク。
# ("CABLE-B Input" は 16ch 版 "CABLE-B In 16ch" と部分一致しないことを確認済み)
DEVICE_CABLE_A_CAPTURE = "CABLE-A Output"   # ブラウザ出力 (AI 原音) の取込元 (録音)
DEVICE_CABLE_B_RENDER = "CABLE-B Input"     # ブラウザマイクへの出力先 (再生)


class SampleFifo:
    """スレッド安全な mono float32 サンプル FIFO。

    asyncio ループ側と PortAudio コールバック (別スレッド) の受け渡しに使う。
    - オーバーフロー: 古いサンプルを捨てる (遅延を溜めない)。
    - アンダーフロー: 0 埋めして要求長を返す (グリッチはするが停止しない)。
    """

    def __init__(self, max_samples: int = SAMPLE_RATE) -> None:  # 既定 1 秒
        self._buf = np.zeros(0, dtype=np.float32)
        self._max = int(max_samples)
        self._lock = threading.Lock()

    def push(self, samples: np.ndarray) -> None:
        s = np.ascontiguousarray(samples, dtype=np.float32).reshape(-1)
        with self._lock:
            self._buf = np.concatenate([self._buf, s]) if self._buf.size else s.copy()
            if self._buf.size > self._max:
                self._buf = self._buf[-self._max :].copy()

    def pop(self, n: int) -> np.ndarray:
        """n サンプルを返す。足りなければ末尾を 0 埋め (固定長を保証)。"""
        with self._lock:
            if self._buf.size >= n:
                out = self._buf[:n].copy()
                self._buf = self._buf[n:]
                return out
            out = np.zeros(n, dtype=np.float32)
            out[: self._buf.size] = self._buf
            self._buf = np.zeros(0, dtype=np.float32)
            return out

    def available(self) -> int:
        with self._lock:
            return int(self._buf.size)

    def clear(self) -> None:
        with self._lock:
            self._buf = np.zeros(0, dtype=np.float32)


def find_device(selector: str | int, kind: str, devices=None) -> int:
    """デバイス指定を解決してインデックスを返す。

    selector: 数値 (または数字文字列) ならインデックス直接指定、
              それ以外は名前の部分一致 (VB-Cable の表示名ゆらぎ対策)。
    kind: 'input' (録音) | 'output' (再生)。
    devices: sounddevice.query_devices() 相当のリスト。省略時は実機を照会
             (テストではモックを注入できる)。

    見つからない場合は、選べる候補一覧を含む LookupError を投げる
    (--list-devices を案内する)。
    """
    if devices is None:
        import sounddevice as sd

        devices = sd.query_devices()
    key = "max_input_channels" if kind == "input" else "max_output_channels"

    idx = None
    if isinstance(selector, int):
        idx = selector
    elif isinstance(selector, str) and selector.strip().isdigit():
        idx = int(selector.strip())
    if idx is not None:
        if 0 <= idx < len(devices) and devices[idx][key] > 0:
            return idx
        raise LookupError(
            f"device index {idx} is not a valid {kind} device\n"
            + format_devices(kind, devices)
        )

    needle = str(selector).lower()
    for i, dev in enumerate(devices):
        if needle in dev["name"].lower() and dev[key] > 0:
            return i
    raise LookupError(
        f"audio device not found: {selector!r} ({kind})\n" + format_devices(kind, devices)
    )


def format_devices(kind: str | None = None, devices=None) -> str:
    """デバイス一覧を人間向けに整形する (--list-devices / エラー表示用)。

    kind を指定するとその方向のデバイスだけに絞る。
    """
    if devices is None:
        import sounddevice as sd

        devices = sd.query_devices()
    try:
        import sounddevice as sd

        api_names = [a["name"] for a in sd.query_hostapis()]
    except Exception:
        api_names = []

    lines = [f"available {kind or 'audio'} devices (数値は --cable-a/--cable-b に指定可):"]
    for i, dev in enumerate(devices):
        is_in = dev["max_input_channels"] > 0
        is_out = dev["max_output_channels"] > 0
        if kind == "input" and not is_in:
            continue
        if kind == "output" and not is_out:
            continue
        direction = "in " if is_in else "out"
        api = ""
        if api_names and "hostapi" in dev:
            api = f" [{api_names[dev['hostapi']]}]"
        lines.append(f"  {i:3d} ({direction}) {dev['name']}{api}")
    return "\n".join(lines)
