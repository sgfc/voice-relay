# voice-relay

iOS端末から日本語で全二重AI会話(Web版 ChatGPT Live)を行い、応答音声に
Beatrice 2 の声質変換を噛ませる中継システム。Windows PC 上で動作する。

```
[iOS Safari] ⇄ WebRTC ⇄ [デーモン] ⇄ CABLE-A/B ⇄ [Edge: ChatGPT Live]
                            └─ beatrice-host (声質変換, 下りのみ)
```

- 上り(自分の声)は無変換でブラウザのマイクへ、下り(AIの声)だけ変換してiOSへ返す。
- 設計の経緯・詳細仕様は [docs/spec.md](docs/spec.md)、開発者向けは
  [docs/development.md](docs/development.md)。

## 必要なもの

| 種別 | 内容 |
|---|---|
| OS | Windows 10/11(デーモンはWindows側で実行。WSL不可) |
| Python | 3.12+([uv](https://docs.astral.sh/uv/) 推奨) |
| ビルド環境 | Visual Studio 2022 (C++)、CMake 3.25+(初回のみ) |
| 仮想ケーブル | [VB-Cable A+B パック](https://vb-audio.com/Cable/)(donationware) |
| ブラウザ | Microsoft Edge(Chromium系なら可) |
| AI | ChatGPT アカウント(Web版の音声モードを使用:無料アカウントでも可) |
| 声質変換 | Beatrice 2 の VST3 とモデル(**各自で入手**。モデルは再頒布禁止のため同梱していない) |
| リモート接続 | [Tailscale](https://tailscale.com/)(iOSのマイク許可にHTTPSが必須のため) |

## セットアップ

### 1. ネイティブホストのビルド(初回のみ)

```powershell
git clone --recursive https://github.com/steinbergmedia/vst3sdk C:\src\vst3sdk
.\scripts\build_native.ps1 -Vst3SdkRoot C:\src\vst3sdk
```

`build/beatrice-host/Release/charadock-beatrice-host.exe` ができる。
ビルドで詰まったら [docs/spec.md](docs/spec.md) の「ビルドの罠」を参照。

### 2. Python 環境

```powershell
uv venv --python 3.12
uv pip install -e .
```

### 3. 仮想ケーブルの設定

VB-Cable A+B をインストール後、Windowsのサウンド設定で
**CABLE-A / CABLE-B の Input/Output 全deviceを 48000 Hz に**設定する
(各デバイスのプロパティ → 詳細)。デバイスが見えるかは:

```powershell
uv run relay --list-devices
```

### 4. 設定ファイル

```powershell
Copy-Item relay.example.toml relay.toml
```

`relay.toml` を編集(コメント参照)。最低限:
- `[beatrice]` の `plugin` / `model` を自分のパスに
- `pitch_shift`(-24〜+24 半音)/ `formant_shift`(-2〜+2)は好みで

### 5. ブラウザの準備

```powershell
# 専用プロファイルで一度起動し、ChatGPT に手動ログインする
uv run relay --probe-cursor
```

手順: relay.toml の `[browser]` に exe / url / user_data_dir と
`window_position` / `window_size` を先に記入しておく(`clicks` はまだ不要)。
`--probe-cursor` はその位置・サイズでEdgeを起動し、カーソル座標を表示し続ける。
**音声モード開始ボタンにカーソルを合わせて**表示された x/y を
`[[browser.clicks]]` に記入すれば完成(座標は起動位置に依存するため、
必ず本番と同じ position/size で測ること)。初回はこのウィンドウで
ChatGPT に手動ログインする。プロファイルは `browser-profile/`(gitignore済み)。

位置自体を試行錯誤したい場合は、tomlを書き換えずに
`--window-position X,Y --window-size W,H` で一時上書きして試せる。

ブラウザ側の音声デバイスは **出力 = CABLE-A Input、マイク = CABLE-B Output**
に設定しておく(Windowsの「アプリの音量とデバイスの基本設定」か、サイトの設定)。

### 6. Tailscale(iOSから使う場合)

PC・iPhone 両方で同じ tailnet にログインし、管理コンソールで
MagicDNS と HTTPS Certificates を有効化。PC側で:

```powershell
tailscale serve --bg 8080
```

iOS Safari で `https://<hostname>.<tailnet>.ts.net/` を開く。
※ マイク取得(getUserMedia)はHTTPS必須。LANの素のHTTPでは動かない。

## 運用

### 起動

```powershell
uv run relay
```

iOS Safari でページを開いて「通話開始」をタップすると、PC側でブラウザが
自動起動し音声モードが始まり、AIの声が変換されて返ってくる。
iOSショートカットから `POST /start` を叩いて先にブラウザを立ち上げておくこともできる。

### エンドポイント

| メソッド/パス | 動作 |
|---|---|
| `GET /` | iOS用クライアントページ |
| `POST /start` | パイプライン起動(ブラウザ+beatrice)。冪等 |
| `POST /stop` | 全停止(ブラウザ taskkill 含む)。冪等 |
| `GET /status` | `{state, mode, connections, beatrice}` |

### iOSショートカット(任意)

ショートカットアプリで「**URLを開く**」アクション1つに
`https://<host>.ts.net/?start` を設定するだけでよい。ページが開くと同時に
パイプライン起動(ブラウザ+beatrice)が走り、画面のログに進捗が出る。
「準備完了」が出たら「通話開始」をタップすると、残りはWebRTC接続だけなので
すぐ会話が始まる。

即時停止用に「URLの内容を取得」(方法: **POST**)で `/stop` を叩く
ショートカットも作っておくと便利。実行時はiPhoneのTailscaleが接続状態であること。

### 自動停止(デッドマンスイッチ)

接続が失われて猶予時間(既定20秒、`[deadman]` で変更)を超えると、
ブラウザ含め自動で全停止する。beatrice の異常など処理不能が確定した場合も同様。
会話モードの時間を無駄に消費しない設計。再開はiOSから接続し直すだけ。

### 話者・声の調整

`relay.toml` の `[beatrice]` を編集して再起動(iOS側は切断→再接続)。
- `voice`: 話者インデックス(公式モデル1は 0/1/2)
- `pitch_shift` / `formant_shift`: CLI `--pitch-shift` / `--formant-shift` でも上書き可

### 注意事項

- デーモンは**ログオンセッション内**で起動する(座標クリックのため。SSH/WSLから不可)
- ロック画面・スリープ中はクリックが届かない。長時間運用時は電源設定を確認
- iOS側は有線イヤホンかAAC接続を推奨(Bluetooth HFPは遅延・音質が劣化)
- 変換音声を公開する場合はモデルの利用規約を確認(spec.md「モデル・ライセンス」)

## トラブルシュート

| 症状 | 対処 |
|---|---|
| `audio device not found` | `uv run relay --list-devices` で名前を確認し `[audio]` を修正(番号指定も可) |
| iOSで「パイプライン起動に失敗」 | メッセージに原因が出る(パラメータ範囲外・パス誤りなど)。PC側ログに詳細 |
| iOSでマイクが取れない | URLが `https://` か確認。Tailscale serve の設定を確認 |
| ポート使用中 (10048) | 前のデーモンが残っている: `Get-NetTCPConnection -LocalPort 8080 -State Listen \| % { Stop-Process -Id $_.OwningProcess -Force }` |
| venv が壊れた(lib64 エラー) | WSL側で `uv run` した形跡。`.venv` を削除してWindows側で作り直す |
| クリックが空振りする | `--probe-cursor` で座標を取り直す。ページ読み込みが遅い場合は `delay` を増やす |

## 位置づけ・免責

- 本プロジェクトは個人利用を想定した非公式ツールであり、OpenAI・
  Project Beatrice・CharaDock のいずれとも無関係です。
- ChatGPT の音声モードを通常のブラウザで利用し、その音声を利用者
  自身の端末へ中継するものです。レート制限・利用時間制限を回避する
  機能はありません。
- セッション開始/終了の UI 操作を自動化する機能を含みます。この種の
  自動操作の規約上の扱いには解釈の余地があるため、利用は自己責任で、
  懸念がある場合はブラウザを手動で起動して運用してください。
- 本ツールを第三者向けサービスとして提供することは想定していません
  (個人向け ChatGPT の規約範囲を超えるため。その用途は API を利用
  してください)。
- 変換音声・キャラクター声質の利用は各モデル/キャラクターの規約
  (上記「注意事項」・spec.md) に従ってください。

## 謝辞

- [CharaDock](https://github.com/ochisamu/CharaDock) (ochisamu氏) —
  beatrice-host (ヘッドレスVST3ホスト) の実装を vendor して利用しています。
  Realtime API + Beatrice 2 という構成の先行実証でもあり、本プロジェクトの
  設計はここから多くを得ています。
- [Project Beatrice](https://prj-beatrice.com) — 軽量・低遅延の声質変換
  Beatrice 2 と公式モデルの無償公開に感謝します。

## License

MIT([LICENSE](LICENSE))。ただし `native/beatrice-host/` は Apache-2.0 の
vendor コード(来歴は [PROVENANCE.md](native/beatrice-host/PROVENANCE.md))。
VST3 SDK・Beatrice 本体・モデルは含まれない(各自入手。モデルは再頒布禁止)。
