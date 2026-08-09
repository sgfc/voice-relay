# 開発・検証ガイド

運用手順は [../README.md](../README.md)、仕様・設計判断・既知の罠は
[spec.md](spec.md)(こちらが正)を参照。

## 構成

```
src/relay/                Python デーモン (Windows側 Python で実行)
  control.py              HTTP/WebRTC サーバー本体・デッドマンスイッチ・送出トラック
  engine.py               エンジン抽象 + BrowserDeviceEngine (CABLE-A/B 橋渡し)
  beatrice.py             beatrice-host サブプロセスラッパー (READY 待ち含む)
  browser.py              ブラウザ起動 + SendInput 座標クリック + taskkill
  audio.py                48kHz/480サンプル規約・SampleFifo・デバイス解決
  config.py               relay.toml の読み込み・検証
  __main__.py             CLI (uv run relay)
native/beatrice-host/     vendor: CharaDock の最小ヘッドレスVST3ホスト
                          (Apache-2.0, 改変記録は PROVENANCE.md)
native/<future>/          C++ヘルパー追加位置。scripts/build_native.ps1 に登録
scripts/                  build_native.ps1 / test_beatrice_host.py
web/index.html            iOS用 WebRTC クライアント (単一ファイル)
tests/                    pytest (実デバイス・実VST不要で走る)
```

## テスト

```powershell
uv run --extra dev pytest tests/ -q
```

実 CABLE デバイスや実 VST に依存しない設計:
- beatrice のプロトコルは `tests/_fake_beatrice_host.py`(偽ホスト)で検証
- エンジンはダミー差し替え、デバイス解決はモックのデバイスリストで検証

## 検証モード (echo)

WebRTC の接続とオーディオ I/O だけを切り分けて確認する:

```powershell
uv run relay --mode echo
```

iOS から接続すると自分の声がそのまま返る(イヤホン必須。ハウリング注意)。
ブラウザ・CABLE・beatrice を一切使わないため、問題の切り分けに使う。

## beatrice-host 単体検証 (WAV -> WAV)

```powershell
uv run python scripts/test_beatrice_host.py `
  --exe build/beatrice-host/Release/charadock-beatrice-host.exe `
  --plugin "<...>.vst3" --model "<...>.toml" --voice 0 `
  input_48k_mono.wav out.wav
```

RTF(実時間比)が表示される。実測 0.142(P0、モデル読み込み込み)。
入力は 48kHz mono 推奨。別レートは線形補間の簡易リサンプルになるため
音質評価には 48kHz ネイティブ素材を使うこと(spec.md「音質メモ」)。

## 段階的な切り分け手順

1. `--mode echo` — WebRTC・iOS側の問題を切り分け
2. `--mode browser`(beatrice 設定なし)— CABLE 配線だけ確認(AI原音が無変換で返る)
3. beatrice 設定あり — 本経路
4. `[browser]` 設定あり — ブラウザ自動操作込みの完全形

CLI 引数は relay.toml より優先されるので、relay.toml を書き換えずに
`--mode echo` などで一時的に切り替えられる。

## ネイティブビルド

```powershell
.\scripts\build_native.ps1 -Vst3SdkRoot C:\src\vst3sdk
```

- 既知の罠(/Zc:char8_t-、SMTG_USE_STATIC_CRT、.ps1 の BOM 等)は spec.md 参照
- CMake 設定を変えたら build ディレクトリを削除してから再 configure
- 新しい C++ ヘルパーは beatrice-host と同じ stdin/stdout フレームプロトコルで
  実装し、build_native.ps1 の配列に登録する(spec.md「将来の拡張」)
