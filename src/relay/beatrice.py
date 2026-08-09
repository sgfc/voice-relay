"""charadock-beatrice-host のサブプロセスラッパー。

プロトコル (native/beatrice-host/src/main.cpp より):
  フレーム = uint32 サンプル数 (LE, ネイティブ) + float32 x N (モノラル)
  1 入力フレームにつき 1 出力フレームが同期的に返る。
  最大 4096 サンプル/フレーム、デフォルトブロック 480 (48kHz で 10ms)。
  ステータス・エラーは stderr。
"""

from __future__ import annotations

import struct
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

_HEADER = struct.Struct("<I")
MAX_FRAME_SAMPLES = 4096
DEFAULT_BLOCK_SAMPLES = 480  # 10ms @ 48kHz


@dataclass
class BeatriceParams:
    plugin: Path          # 公式 Beatrice 2 の .vst3 パス
    model: Path           # モデルの TOML パス
    voice: int = 0
    pitch_shift: float = 0.0
    formant_shift: float = 0.0
    input_gain: float = 0.0
    output_gain: float = 0.0
    intonation: float = 1.0
    pitch_correction: float = 0.0
    pitch_correction_type: int = 0
    sample_rate: int = 48000
    block_samples: int = DEFAULT_BLOCK_SAMPLES
    extra_args: list[str] = field(default_factory=list)

    def to_argv(self, exe: Path) -> list[str]:
        return [
            str(exe),
            "--plugin", str(self.plugin),
            "--model", str(self.model),
            "--voice", str(self.voice),
            "--pitch-shift", str(self.pitch_shift),
            "--formant-shift", str(self.formant_shift),
            "--input-gain", str(self.input_gain),
            "--output-gain", str(self.output_gain),
            "--intonation", str(self.intonation),
            "--pitch-correction", str(self.pitch_correction),
            "--pitch-correction-type", str(self.pitch_correction_type),
            "--sample-rate", str(self.sample_rate),
            "--block-samples", str(self.block_samples),
            *self.extra_args,
        ]


class BeatriceHost:
    """beatrice-host を子プロセスとして起動し、PCM を往復させる。

    スレッド安全性: process() は内部ロックで直列化する。
    リアルタイム経路ではブロックサイズを params.block_samples に揃えて呼ぶこと。
    パラメータ変更(話者切替等)は stop() -> start() で再起動する。
    """

    def __init__(self, exe: Path, params: BeatriceParams) -> None:
        self.exe = Path(exe)
        self.params = params
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._stderr_lines: list[str] = []
        self._stderr_thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._latency_samples = 0

    # -- lifecycle -------------------------------------------------------

    def start(self) -> None:
        if self._proc is not None:
            raise RuntimeError("already started")
        self._ready.clear()
        self._proc = subprocess.Popen(
            self.params.to_argv(self.exe),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

    def wait_ready(self, timeout: float = 20.0) -> int:
        """モデル読み込み完了 (stderr の `READY <latency>`) を待ち、レイテンシを返す。

        デーモンは READY を待ってから音声を流すこと (先頭欠け防止, docs/spec.md 参照)。
        タイムアウト or プロセス死亡時は stderr 付きで例外。
        """
        if self._ready.wait(timeout):
            return self._latency_samples
        proc = self._proc
        if proc is not None and proc.poll() is not None:
            raise RuntimeError(self._die(f"host exited before READY (code {proc.returncode})"))
        raise TimeoutError(self._die(f"timeout ({timeout}s) waiting for READY"))

    @property
    def latency_samples(self) -> int:
        return self._latency_samples

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()  # EOF で host は正常終了する
            proc.wait(timeout=3)
        except Exception:
            proc.kill()

    def __enter__(self) -> "BeatriceHost":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # -- audio -----------------------------------------------------------

    def process(self, samples: np.ndarray) -> np.ndarray:
        """モノラル float32 のフレームを変換して返す (同期)。"""
        proc = self._proc
        if proc is None or proc.stdin is None or proc.stdout is None:
            raise RuntimeError("host not running")
        buf = np.ascontiguousarray(samples, dtype=np.float32)
        if buf.ndim != 1:
            raise ValueError("mono 1-D array expected")
        if len(buf) > MAX_FRAME_SAMPLES:
            raise ValueError(f"frame too large: {len(buf)} > {MAX_FRAME_SAMPLES}")
        with self._lock:
            proc.stdin.write(_HEADER.pack(len(buf)))
            proc.stdin.write(buf.tobytes())
            proc.stdin.flush()
            header = proc.stdout.read(_HEADER.size)
            if len(header) != _HEADER.size:
                raise RuntimeError(self._die("host closed stdout"))
            (count,) = _HEADER.unpack(header)
            payload = proc.stdout.read(count * 4)
            if len(payload) != count * 4:
                raise RuntimeError(self._die("truncated output frame"))
        return np.frombuffer(payload, dtype=np.float32).copy()

    # -- diagnostics -----------------------------------------------------

    @property
    def stderr_log(self) -> str:
        return "".join(self._stderr_lines)

    def _drain_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for raw in proc.stderr:
            line = raw.decode("utf-8", errors="replace")
            self._stderr_lines.append(line)
            if not self._ready.is_set() and line.startswith("READY"):
                parts = line.split()
                if len(parts) > 1:
                    try:
                        self._latency_samples = int(parts[1])
                    except ValueError:
                        pass
                self._ready.set()

    def _die(self, message: str) -> str:
        return f"{message}\n--- host stderr ---\n{self.stderr_log}"
