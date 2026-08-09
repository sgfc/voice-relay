"""設定ファイル (TOML) の読み込みと RelayConfig。

優先順位: CLI 引数 (明示指定) > 設定ファイル > 既定値。
設定ファイルはマシン固有のパスを含むためリポジトリに入れない
(relay.toml は .gitignore 済み。雛形は relay.example.toml)。
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .audio import DEVICE_CABLE_A_CAPTURE, DEVICE_CABLE_B_RENDER

DEFAULT_CONFIG_PATH = Path("relay.toml")

# BeatriceParams へ素通しできるチューニングキーと有効範囲
# (native/beatrice-host/src/main.cpp の検証と同一。起動を待たず設定時に弾く)
BEATRICE_TUNING_RANGES: dict[str, tuple[float, float] | None] = {
    "pitch_shift": (-24.0, 24.0),     # 半音
    "formant_shift": (-2.0, 2.0),
    "intonation": (-1.0, 3.0),
    "input_gain": (-60.0, 20.0),      # dB
    "output_gain": (-60.0, 20.0),     # dB
    "pitch_correction": (0.0, 1.0),
    "pitch_correction_type": None,    # 型インデックス。ホスト側に委ねる
}
BEATRICE_TUNING_KEYS = tuple(BEATRICE_TUNING_RANGES)


def validate_beatrice_tuning(tuning: dict) -> None:
    """チューニング値の範囲を検証する。CLI 上書き後にも呼ぶこと。"""
    for key, value in tuning.items():
        bounds = BEATRICE_TUNING_RANGES.get(key)
        if bounds is not None:
            lo, hi = bounds
            if not (lo <= float(value) <= hi):
                raise ValueError(
                    f"[beatrice] {key} = {value} は範囲外 ({lo:g} 〜 {hi:g})"
                )


@dataclass
class ClickStep:
    """ブラウザ起動後の座標クリック 1 回分。delay は直前ステップからの秒数。"""

    x: int
    y: int
    delay: float = 5.0


@dataclass
class BrowserConfig:
    """外部ブラウザ (ChatGPT Live) の自動操作設定。

    アプリモード + 専用プロファイル + 固定ウィンドウ位置/サイズで起動し、
    座標クリックの安定性を確保する (docs/spec.md 決定事項)。
    """

    exe: Path
    url: str
    user_data_dir: Path
    window_position: tuple[int, int] = (100, 100)
    window_size: tuple[int, int] = (1000, 800)
    clicks: list[ClickStep] = field(default_factory=list)

    def to_argv(self) -> list[str]:
        return [
            str(self.exe),
            f"--app={self.url}",
            f"--user-data-dir={self.user_data_dir}",
            f"--window-position={self.window_position[0]},{self.window_position[1]}",
            f"--window-size={self.window_size[0]},{self.window_size[1]}",
            "--no-first-run",
            "--disable-session-crashed-bubble",
        ]


@dataclass
class RelayConfig:
    """デーモンの起動時設定。CLI / 設定ファイルから組み立てる。"""

    mode: str = "echo"  # "echo" | "browser"
    host: str = "0.0.0.0"
    port: int = 8080
    beatrice_exe: Path | None = None
    plugin: Path | None = None
    model: Path | None = None
    voice: int = 0
    beatrice_tuning: dict = field(default_factory=dict)  # BEATRICE_TUNING_KEYS のみ
    cable_a: str | int = DEVICE_CABLE_A_CAPTURE
    cable_b: str | int = DEVICE_CABLE_B_RENDER
    browser: BrowserConfig | None = None
    # デッドマンスイッチ (docs/spec.md: 切断後 15-30 秒猶予)
    deadman_grace: float = 20.0          # 接続喪失からの猶予秒
    deadman_initial_grace: float = 90.0  # パイプライン起動から初回接続までの猶予秒

    @property
    def beatrice_configured(self) -> bool:
        return bool(self.beatrice_exe and self.plugin and self.model)


def parse_xy(text: str) -> tuple[int, int]:
    """CLI の "100,200" 形式を (100, 200) にする。"""
    parts = text.split(",")
    if len(parts) != 2:
        raise ValueError(f"'x,y' 形式で指定してください: {text!r}")
    return (int(parts[0].strip()), int(parts[1].strip()))


def default_browser_exe() -> Path | None:
    """既知の場所から Chromium 系ブラウザを探す (probe の既定値用)。"""
    candidates = [
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def load_config(path: Path) -> RelayConfig:
    """TOML を読み RelayConfig を返す。未知キーはエラーにして事故を防ぐ。"""
    with open(path, "rb") as f:
        data = tomllib.load(f)

    cfg = RelayConfig()
    _apply_section(data.pop("server", {}), {"mode", "host", "port"}, cfg, "server")
    if cfg.mode not in ("echo", "browser"):
        raise ValueError(f"[server] mode は echo か browser: {cfg.mode!r}")

    beat = data.pop("beatrice", {})
    if beat:
        for key in ("exe", "plugin", "model"):
            if key in beat:
                attr = "beatrice_exe" if key == "exe" else key
                setattr(cfg, attr, Path(beat.pop(key)))
        if "voice" in beat:
            cfg.voice = int(beat.pop("voice"))
        for key in list(beat):
            if key in BEATRICE_TUNING_KEYS:
                cfg.beatrice_tuning[key] = beat.pop(key)
        validate_beatrice_tuning(cfg.beatrice_tuning)
        _reject_unknown(beat, "beatrice")

    audio = data.pop("audio", {})
    if audio:
        cfg.cable_a = audio.pop("cable_a", cfg.cable_a)
        cfg.cable_b = audio.pop("cable_b", cfg.cable_b)
        _reject_unknown(audio, "audio")

    dead = data.pop("deadman", {})
    if dead:
        cfg.deadman_grace = float(dead.pop("grace_seconds", cfg.deadman_grace))
        cfg.deadman_initial_grace = float(
            dead.pop("initial_grace_seconds", cfg.deadman_initial_grace)
        )
        _reject_unknown(dead, "deadman")

    brow = data.pop("browser", {})
    if brow:
        clicks = [
            ClickStep(int(c["x"]), int(c["y"]), float(c.get("delay", 5.0)))
            for c in brow.pop("clicks", [])
        ]
        try:
            cfg.browser = BrowserConfig(
                exe=Path(brow.pop("exe")),
                url=brow.pop("url"),
                # 相対パス (例: browser-profile) はリポジトリルート起点で絶対化する。
                # Chromium は相対 --user-data-dir を自身の cwd 基準で解釈するため。
                user_data_dir=Path(brow.pop("user_data_dir")).resolve(),
                window_position=tuple(brow.pop("window_position", (100, 100))),
                window_size=tuple(brow.pop("window_size", (1000, 800))),
                clicks=clicks,
            )
        except KeyError as e:
            raise ValueError(f"[browser] に必須キーがありません: {e}") from e
        _reject_unknown(brow, "browser")

    _reject_unknown(data, "(top-level)")
    return cfg


def _apply_section(section: dict, allowed: set[str], cfg: RelayConfig, name: str) -> None:
    for key in list(section):
        if key in allowed:
            setattr(cfg, key, section.pop(key))
    _reject_unknown(section, name)


def _reject_unknown(remaining: dict, section: str) -> None:
    if remaining:
        raise ValueError(f"relay.toml [{section}] に未知のキー: {sorted(remaining)}")
