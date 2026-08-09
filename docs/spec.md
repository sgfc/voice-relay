# voice-relay 仕様書 v1.1

iOS端末から日本語で全二重AI会話を行い、応答音声にBeatrice 2の声質変換を噛ませるシステム。
Windows デスクトップPCが中継を担う。

改訂履歴:
- v1.2 (2026-08-09): P2/P3実装。仮想ケーブルを CABLE-A/C から CABLE-A/B に変更
  (VB-Cable A+B パック導入)。
- v1.1 (2026-08-09): P0完了。ビルド時の罠、READYハンドシェイク、実測RTF、
  モデル・規約情報、音質調査項目を追記。
- v1.0: 初版。

## 決定事項（経緯込み）

- **エンジンは Web版 ChatGPT Live**（真の全二重・定額・動作検証済み）。
  Realtime API は従量課金かつ疑似全二重のため不採用。ただしエンジンは
  `relay/engine.py` の `Engine` 抽象で差し替え可能にし、GPT-Live API 解禁時に
  `LiveApiEngine` へ乗り換えられる構造とする。
- **ブラウザは内蔵せず外部プロセス**。理由はログインの生存性
  （埋め込みWebViewはGoogle SSOがブロック・bot対策に弱い）。
  アプリモード `--app=` + 専用プロファイル `--user-data-dir` +
  固定ウィンドウ位置/サイズで起動し、座標クリックの安定性を確保。
- **声質変換は charadock-beatrice-host**（`native/beatrice-host/` に vendor 済み、
  Apache-2.0、来歴・改変記録は PROVENANCE.md）。公式 Beatrice 2 VST3 を
  ヘッドレス駆動する305行のC++20ホスト。VCClient は不要になる。
- **変換品質は実証済み**（VCClient/beatrice-client 経由 + 本ホスト経由の両方で確認）。
- **音声規約: 全区間 48kHz mono float32、ブロック 480 サンプル（10ms）**。
  仮想ケーブルを 48kHz に設定すればリサンプル不要。
- **仮想ケーブルは最終形でも2本残る**（ブラウザ境界のため）:
  - CABLE-A: ブラウザ出力（AI原音）→ デーモン取込
  - CABLE-B: デーモン出力（iOSの声）→ ブラウザマイク
  （v1.2 で C→B に変更: 実機に VB-Cable A+B パックを導入したため、
  デーモン経路は A/B の2本で完結する。VCClient はデーモン完成時に撤去。）

## アーキテクチャ

```
[iOS Safari]  ⇄ WebRTC(Opus) ⇄  [aiortcデーモン]  ⇄ CABLE-A/B ⇄  [外部ブラウザ: ChatGPT Live]
 AEC担当・初回タップ必須            │
                                    ├─ stdin/stdout ─ [beatrice-host 子プロセス] ─ 公式VST3+モデルTOML
                                    ├─ HTTP制御 (/start /stop /status) ← iOSショートカット
                                    └─ ICE状態監視 → デッドマンスイッチ
```

上り（あなたの声）は変換しない。下り（AI原音）だけ beatrice-host を通す。
PC側は全経路仮想のため音響エコーが存在せず、AECはiOS側のみで完結する。

## beatrice-host プロトコル・実測値

- フレーム = `uint32 サンプル数(LE)` + `float32 × N`（モノラル）。1入力に1出力が同期で返る。
- 最大 4096 サンプル/フレーム。`--block-samples`（デフォルト480）、`--sample-rate`（デフォルト48000）。
- モデルは `--model <TOML>`、話者は `--voice`、他 `--pitch-shift` `--formant-shift`
  `--intonation` 等。**パラメータは起動時渡し**。話者切替はプロセス再起動で行う。
- **READYハンドシェイク**: モデル読み込み完了時に stderr へ `READY <latency>` を出力。
  デーモンは READY を待ってから音声を流し始めること（先頭欠け防止）。
- **VSTはレイテンシを申告しない**（READY 0 = getLatencySamples() が 0）。
  遅延設計値が必要になったら入出力WAVの相互相関で実測する
  （test_beatrice_host.py に追加予定）。
- **実測RTF: 0.142**（8.09秒素材、モデル読み込み込み。定常は1ブロック10msあたり
  約1.4ms）。リアルタイム比7倍のヘッドルーム。IPC往復込みでこの値であり、
  サブプロセス方式のオーバーヘッドは問題にならない。
- EOF（stdin close）で正常終了。Python ラッパー: `src/relay/beatrice.py`（実装済み）。

## モデル・ライセンス

- 使用モデル: **Beatrice 2.0.0-rc.0 公式モデル1**（3話者マージ:
  つくよみちゃん / 刻鳴時雨 / OLUNE）。TOML が同梱 .bin 群を参照する構造のため、
  **TOML と .bin は同一フォルダに置き、--model に TOML を渡す**。
  `--voice` のインデックス対応は TOML 内の話者定義順（0/1/2 で聴き比べて確認済みに更新すること）。
- **rc.0 モデルは rc.2 VST で読み込み可**（動作確認済み）。
- **モデルファイル（.bin/.toml等）の再頒布・譲渡は規約で禁止**。リポジトリに含めない。
  モデル置き場を .gitignore に追加すること。
- つくよみちゃん声質の変換音声を**公開する場合**の禁止事項: 批判・攻撃、
  政治・宗教的呼びかけ、ゾーニングなしの刺激表現、素材化しての公開。
  私的な会話利用は問題なし。将来デモ公開時に再確認。
- 規約の特記事項: 話者切替直後等に**中間的な声質**が出た場合、影響した全話者の
  規約が適用される。切替をプロセス再起動方式にしている本設計では発生しにくい。
- Beatrice 本体・推論ライブラリ・モデルは同梱しない。`beatrice.lib` の直接利用は
  Project Beatrice の個別許諾が必要（aq2r/beatrice-client に許諾前例あり）。
  VST3 のホスト利用は通常利用の範囲。

## ビルドの罠（Windows・実際に踏んだもの）

- **VST3 SDK master は C++20 非互換**（module_win32.cpp が
  `generic_u8string()`(→std::u8string) を `const std::string&` に渡す）。
  対処: vendored CMakeLists の MSVC オプションに **`/Zc:char8_t-`** を追加済み
  （PROVENANCE.md の改変記録参照）。上流 CharaDock は macOS 主開発のため
  Windows 経路が未検証と推定。上流への issue/PR 候補。
- **CRT 混在リンクエラー (LNK2038)**: 上流 CMake は実行ファイル側を静的CRT(/MT)に
  するが、SDK サブディレクトリには伝播しない。対処: configure 時に
  **`-DSMTG_USE_STATIC_CRT=ON`** を渡す（build_native.ps1 に追加済み）。
  変更時は build ディレクトリを削除してから再 configure。
- **.ps1 は BOM 付き UTF-8 で保存**。日本語コメント入りの BOM なし UTF-8 は
  Windows PowerShell 5.1 が CP932 誤読し、空実行や無反応になる。
- **.ps1 は cmd.exe から直接叩かない**（引数が渡らず入力プロンプトで停止する）。
  PowerShell から実行するか `powershell -ExecutionPolicy Bypass -File ...` を使う。
  実行ポリシーは `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` 推奨。
- CMake は winget (`Kitware.CMake`) 版か VS2022 同梱版（Developer PowerShell から）。
  3.25 以上必須。
- 実行ファイル名は `charadock-beatrice-host.exe`（build/beatrice-host/Release/）。

## 音質メモ・調査項目

- beatrice-client 比で**わずかにノイズが乗る箇所あり**（静かな環境で気づく程度、
  実用上は許容範囲）。ただし該当テストは 44.1kHz 素材を**検証用の線形補間で
  48kHz にリサンプル**しており、ノイズはスクリプト由来の可能性が高い。
  → **48kHz ネイティブ素材で再テストして切り分けること**（P2 前に実施推奨）。
  それでも残る場合の調査候補: ブロックサイズ（480 vs beatrice-client の内部設定）、
  入出力ゲイン段、float32 クリッピング。

## 実装フェーズ

- **P0: beatrice-host 単体検証 — 完了 (2026-08-09)**
  ビルド成功・WAV変換成功・RTF 0.142・品質許容。
- **P1: 暫定運用で通し検証 — スキップ**（P2/P3 の自前実装で直接通し確認したため不要に）
- **P2: デーモン骨格 — 完了 (2026-08-09)**: aiortc で iOS⇄PC の WebRTC 確立、
  エコーバックで接続とオーディオI/O確認 (iOS実機 + Tailscale HTTPS)。
  web/index.html、control.py の /offer /start /stop /status 実装。
  ※音質切り分けは保留のまま着手した（ユーザー判断）。
- **P3: 統合 — 完了 (2026-08-09)**: sounddevice で CABLE-A/B 橋渡し +
  beatrice-host 子プロセス統合（READY 待ち実装済み）。iOS実機から ChatGPT Live と
  実会話し、変換音声で遅延・全二重とも体感良好を確認。
  デバイスは --list-devices で一覧、--cable-a/--cable-b で名前部分一致か番号指定。
- **P4: 堅牢化 — 完了 (2026-08-09)**:
  - 設定ファイル relay.toml（TOML、CLI が優先、範囲検証つき。雛形 relay.example.toml）。
  - ブラウザ自動操作（browser.py: Edge アプリモード起動 + SendInput 座標クリック +
    taskkill /T、--probe-cursor で座標調査）。実機で通し確認済み。
  - デッドマンスイッチ（_watchdog: 状態ポーリング、猶予 grace_seconds /
    initial_grace_seconds、ブラウザ含め全停止）。異常確定時 (起動途中失敗・
    会話中の処理不能) も同経路で全停止に収束。
  - 接続と起動の並列化: /offer は即 answer を返し、下りトラックは準備完了まで
    無音。猶予内の再接続は同じ会話セッションへ戻る (冪等な _ensure_pipeline)。
  - iOS からは /?start 付き URL で「ページ表示と同時に起動」。ショートカットは
    「URLを開く」1アクション。
  - 保留: 常駐化（必要になったらスタートアップ登録。ログオンセッション内必須）。
    話者切替はプロセス再起動方式のまま（relay.toml の voice 変更 + 再起動）。
  - 既知の揺らぎ: beatrice の音質が稀に不安定になる報告あり (P2 前から保留中の
    48kHz ネイティブ素材での切り分けと合わせて、頻発時に調査)。

## 既知の罠（実装時・必読）

- **入力注入はログオンセッション内から**。SSH/WSL等の非対話コンテキストから
  起動したプロセスはデスクトップへのマウス/キー注入に失敗する（セッション分離）。
  デーモンはスタートアップ登録でログオンセッション内に常駐させ、
  外からはHTTPで合図だけ送る。
- **ロック画面・スリープ中はUI操作が届かない**。設定で無効化。
- **DOM自動化（Playwright等）は使わない**。ChatGPTログインが自動化検知と衝突する。
  OSレベル座標クリックは本物のユーザー操作と区別されずマイク許可の
  ジェスチャ要件も満たす。
- **Safariのジェスチャ要件**: iOS側の初回タップは仕様上不可避（許容済み）。
- **通話系の音声加工**: 暫定運用でDiscord等を使う場合はノイズ抑制/AGCを切る。
- **Free プランは音声モードに時間上限の可能性**。デスクトップアプリの音声は
  Free 非対応のため Web 版を使用（確認済み）。長時間運用フェーズで Plus を検討。
- **Bluetoothヘッドセット(HFP)は遅延・帯域が劣化**。iOS側は有線かAAC通話モード。

## 将来の拡張（優先度順メモ）

1. `LiveApiEngine`: GPT-Live API 解禁後の差し替え。ブラウザ・CABLE-A/C・
   座標クリックが全廃され単一プロセス化。テキストイベント（ログ・ツール呼出）も取れる。
2. `native/wasapi-loopback/`: プロセス単位ループバックキャプチャ
   （Windows 10 2004+ の ApplicationLoopback）で CABLE-A を先行撤去する
   C++ヘルパー。beatrice-host と同じ stdin/stdout パターンで実装し、
   build_native.ps1 の配列に登録する。
3. 話者のセッション中切替（beatrice-host の stdin 制御コマンド化。中間声質の
   規約条項に留意）。
4. 自作モデルの話し出し欠け対策（beatrice-trainer で冒頭サンプルを厚くした再学習）。
5. 音声でコーディングエージェントに作業を振る拡張（CharaDock が先行事例）。
6. 上流貢献: /Zc:char8_t- と SMTG_USE_STATIC_CRT の件を ochisamu/CharaDock へ
   issue/PR。
   