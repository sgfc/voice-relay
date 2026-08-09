"""エントリポイント。デーモン (control.py) を起動する。

使い方 (Windows, リポジトリルートで):
  uv run relay                       # ./relay.toml があれば読む。無ければ echo モード
  uv run relay --config my.toml     # 設定ファイルを指定
  uv run relay --mode echo          # CLI は設定ファイルより優先
  uv run relay --list-devices       # オーディオデバイス一覧
  uv run relay --probe-cursor       # クリック座標の調査 (カーソル位置を表示)

設定ファイルの雛形は relay.example.toml を relay.toml にコピーして編集する
(マシン固有パスを含むためリポジトリには入れない)。
iOS からは Tailscale HTTPS (tailscale serve) 経由で開く。
"""
from __future__ import annotations

import argparse
from pathlib import Path

from relay.config import DEFAULT_CONFIG_PATH, RelayConfig, load_config
from relay.control import run


def main() -> None:
    ap = argparse.ArgumentParser(prog="relay", description="voice-relay daemon")
    ap.add_argument("--config", type=Path, default=None,
                    help=f"設定ファイル (TOML)。省略時は {DEFAULT_CONFIG_PATH} があれば読む")
    ap.add_argument("--host", default=None, help="bind address (default: 0.0.0.0)")
    ap.add_argument("--port", type=int, default=None, help="port (default: 8080)")
    ap.add_argument("--mode", choices=["echo", "browser"], default=None,
                    help="echo: エコーバック / browser: 本経路")
    ap.add_argument("--beatrice-exe", type=Path, default=None)
    ap.add_argument("--plugin", type=Path, default=None, help="Beatrice 2 の .vst3 パス")
    ap.add_argument("--model", type=Path, default=None, help="モデルの .toml パス")
    ap.add_argument("--voice", type=int, default=None, help="話者インデックス")
    ap.add_argument("--pitch-shift", type=float, default=None,
                    help="ピッチシフト (半音単位。relay.toml より優先)")
    ap.add_argument("--formant-shift", type=float, default=None,
                    help="フォルマントシフト (relay.toml より優先)")
    ap.add_argument("--cable-a", default=None, help="AI 原音の取込元 (録音)。名前部分一致か番号")
    ap.add_argument("--cable-b", default=None, help="ブラウザマイクへの出力先 (再生)。同上")
    ap.add_argument("--list-devices", action="store_true",
                    help="オーディオデバイス一覧を表示して終了")
    ap.add_argument("--probe-cursor", action="store_true",
                    help="設定の位置/サイズでブラウザを起動し、カーソル座標を表示し続ける")
    ap.add_argument("--window-position", default=None, metavar="X,Y",
                    help="ブラウザウィンドウ位置の上書き (probe での試行錯誤用)")
    ap.add_argument("--window-size", default=None, metavar="W,H",
                    help="ブラウザウィンドウサイズの上書き (同上)")
    args = ap.parse_args()

    if args.list_devices:
        from relay.audio import format_devices

        print(format_devices("input"))
        print()
        print(format_devices("output"))
        return

    # 設定ファイル -> CLI 上書き の順で組み立てる
    config_path = args.config
    if config_path is None and DEFAULT_CONFIG_PATH.exists():
        config_path = DEFAULT_CONFIG_PATH
    if config_path is not None:
        config = load_config(config_path)
        print(f"config: {config_path}")
    else:
        config = RelayConfig()

    if args.probe_cursor:
        from relay.browser import probe_cursor_loop
        from relay.config import BrowserConfig, default_browser_exe, parse_xy

        bc = config.browser
        if bc is None:
            # [browser] 未設定でも既定値で起動できるようにする (初期設定の試行錯誤用)
            exe = default_browser_exe()
            if exe is None:
                print("エラー: ブラウザが見つかりません。relay.toml の [browser] で exe を指定してください。")
                return
            bc = BrowserConfig(
                exe=exe,
                url="https://chatgpt.com/",
                user_data_dir=Path("browser-profile").resolve(),
            )
        if args.window_position:
            bc.window_position = parse_xy(args.window_position)
        if args.window_size:
            bc.window_size = parse_xy(args.window_size)

        import subprocess

        subprocess.Popen(bc.to_argv())
        print(f"ブラウザ起動: position={list(bc.window_position)} size={list(bc.window_size)}")
        print("この位置/サイズでよければ、relay.toml の [browser] に以下を記入:")
        print(f"  window_position = {list(bc.window_position)}")
        print(f"  window_size = {list(bc.window_size)}")
        print("位置を変えて試すには: --window-position X,Y --window-size W,H")
        probe_cursor_loop()
        return

    for cli_name, attr in [
        ("mode", "mode"), ("host", "host"), ("port", "port"),
        ("beatrice_exe", "beatrice_exe"), ("plugin", "plugin"),
        ("model", "model"), ("voice", "voice"),
        ("cable_a", "cable_a"), ("cable_b", "cable_b"),
    ]:
        value = getattr(args, cli_name)
        if value is not None:
            setattr(config, attr, value)

    if args.pitch_shift is not None:
        config.beatrice_tuning["pitch_shift"] = args.pitch_shift
    if args.formant_shift is not None:
        config.beatrice_tuning["formant_shift"] = args.formant_shift
    from relay.config import validate_beatrice_tuning

    validate_beatrice_tuning(config.beatrice_tuning)

    run(host=config.host, port=config.port, config=config)


if __name__ == "__main__":
    main()
