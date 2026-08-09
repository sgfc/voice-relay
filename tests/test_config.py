"""config.py: TOML 読み込み・検証・ブラウザ argv。"""
from __future__ import annotations

from pathlib import Path

import pytest

from relay.config import BrowserConfig, ClickStep, load_config

EXAMPLE = Path(__file__).resolve().parents[1] / "relay.example.toml"


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "relay.toml"
    p.write_text(text, encoding="utf-8")
    return p


def test_example_config_loads() -> None:
    cfg = load_config(EXAMPLE)
    assert cfg.mode == "browser"
    assert cfg.port == 8080
    assert cfg.beatrice_configured
    assert cfg.browser is None  # 雛形の [browser] はコメントアウト


def test_full_config(tmp_path: Path) -> None:
    p = _write(tmp_path, """
[server]
mode = "browser"
port = 9000

[beatrice]
exe = "host.exe"
plugin = "p.vst3"
model = "m.toml"
voice = 2
pitch_shift = 1.5

[audio]
cable_a = 42

[deadman]
grace_seconds = 5

[browser]
exe = "chrome.exe"
url = "https://example.com/"
user_data_dir = "profile"
window_position = [10, 20]
window_size = [800, 600]

[[browser.clicks]]
x = 100
y = 200
delay = 3.0
""")
    cfg = load_config(p)
    assert cfg.port == 9000
    assert cfg.voice == 2
    assert cfg.beatrice_tuning == {"pitch_shift": 1.5}
    assert cfg.cable_a == 42
    assert cfg.deadman_grace == 5.0
    assert cfg.deadman_initial_grace == 90.0  # 既定のまま
    assert cfg.browser is not None
    assert cfg.browser.clicks == [ClickStep(100, 200, 3.0)]


def test_invalid_mode_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, "[server]\nmode = \"typo\"\n")
    with pytest.raises(ValueError, match="mode"):
        load_config(p)


def test_unknown_key_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, "[server]\npot = 8080\n")
    with pytest.raises(ValueError, match="未知のキー"):
        load_config(p)


def test_tuning_out_of_range_rejected(tmp_path: Path) -> None:
    """beatrice-host の検証を待たず、設定読み込み時に範囲外を弾く。"""
    p = _write(tmp_path, """
[beatrice]
exe = "host.exe"
plugin = "p.vst3"
model = "m.toml"
formant_shift = 7.0
""")
    with pytest.raises(ValueError, match="formant_shift"):
        load_config(p)


def test_browser_missing_required_key(tmp_path: Path) -> None:
    p = _write(tmp_path, "[browser]\nexe = \"chrome.exe\"\n")
    with pytest.raises(ValueError, match="必須キー"):
        load_config(p)


def test_parse_xy() -> None:
    from relay.config import parse_xy

    assert parse_xy("100,200") == (100, 200)
    assert parse_xy(" 10 , 20 ") == (10, 20)
    with pytest.raises(ValueError):
        parse_xy("100")
    with pytest.raises(ValueError):
        parse_xy("a,b")


def test_browser_argv() -> None:
    b = BrowserConfig(
        exe=Path("chrome.exe"),
        url="https://example.com/",
        user_data_dir=Path("prof"),
        window_position=(10, 20),
        window_size=(800, 600),
    )
    argv = b.to_argv()
    assert argv[0] == "chrome.exe"
    assert "--app=https://example.com/" in argv
    assert "--user-data-dir=prof" in argv
    assert "--window-position=10,20" in argv
    assert "--window-size=800,600" in argv
