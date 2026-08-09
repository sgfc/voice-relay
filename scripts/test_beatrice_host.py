"""フェーズ0 単体テスト: WAV -> beatrice-host -> WAV

使い方 (Windows, リポジトリルートで):
  uv run python scripts/test_beatrice_host.py ^
      --exe build/beatrice-host/Release/charadock-beatrice-host.exe ^
      --plugin "C:/Program Files/Common Files/VST3/beatrice_2.0.0-rc.2.vst3" ^
      --model  "C:/path/to/model/model.toml" ^
      --voice 0 ^
      input_48k_mono.wav output_converted.wav

入力 WAV は 48kHz mono を推奨 (int16/float32 どちらでも可)。
別レートの場合は簡易リサンプルする (検証用途なので線形補間)。
"""

from __future__ import annotations

import argparse
import sys
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from relay.beatrice import BeatriceHost, BeatriceParams  # noqa: E402


def read_wav_mono_f32(path: Path, target_rate: int) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width == 2:
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif width == 4:
        data = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        raise SystemExit(f"unsupported sample width: {width}")
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    if rate != target_rate:
        n_out = round(len(data) * target_rate / rate)
        x_out = np.linspace(0, len(data) - 1, n_out)
        data = np.interp(x_out, np.arange(len(data)), data).astype(np.float32)
        print(f"resampled {rate} Hz -> {target_rate} Hz")
    return data.astype(np.float32)


def write_wav_mono_f32(path: Path, data: np.ndarray, rate: int) -> None:
    pcm = np.clip(data, -1.0, 1.0)
    pcm16 = (pcm * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm16.tobytes())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", type=Path, required=True)
    ap.add_argument("--plugin", type=Path, required=True)
    ap.add_argument("--model", type=Path, required=True)
    ap.add_argument("--voice", type=int, default=0)
    ap.add_argument("--pitch-shift", type=float, default=0.0)
    ap.add_argument("--formant-shift", type=float, default=0.0)
    ap.add_argument("--block-samples", type=int, default=480)
    ap.add_argument("input_wav", type=Path)
    ap.add_argument("output_wav", type=Path)
    args = ap.parse_args()

    params = BeatriceParams(
        plugin=args.plugin,
        model=args.model,
        voice=args.voice,
        pitch_shift=args.pitch_shift,
        formant_shift=args.formant_shift,
        block_samples=args.block_samples,
    )
    audio = read_wav_mono_f32(args.input_wav, params.sample_rate)
    block = params.block_samples
    pad = (-len(audio)) % block
    if pad:
        audio = np.concatenate([audio, np.zeros(pad, dtype=np.float32)])

    out_chunks: list[np.ndarray] = []
    t0 = time.perf_counter()
    with BeatriceHost(args.exe, params) as host:
        for i in range(0, len(audio), block):
            out_chunks.append(host.process(audio[i : i + block]))
        elapsed = time.perf_counter() - t0
        log = host.stderr_log

    result = np.concatenate(out_chunks)
    write_wav_mono_f32(args.output_wav, result, params.sample_rate)

    duration = len(audio) / params.sample_rate
    print(f"audio: {duration:.2f}s, processing: {elapsed:.2f}s, RTF: {elapsed/duration:.3f}")
    print(f"output: {args.output_wav} ({len(result)} samples)")
    if log.strip():
        print("--- host stderr (レイテンシ報告等はここに出る) ---")
        print(log)


if __name__ == "__main__":
    main()
