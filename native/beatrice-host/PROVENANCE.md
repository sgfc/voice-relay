# Provenance

このディレクトリは以下のリポジトリから vendor したものです。

- Upstream: https://github.com/ochisamu/CharaDock
- Path: `native/beatrice-host/`
- Commit: `8948ff0e1dc9ba064dc5537f4ac639f9c72dcca6` (2026-08-09)
- License: Apache-2.0 (同梱の LICENSE を参照。NOTICE は上流のものをそのまま保持)

CharaDock は PuruPuru PNGTuber (Copyright 2026 masa, Apache-2.0) の派生であり、
LICENSE の著作権表記はその系譜による。詳細は NOTICE を参照。

## 改変記録

Apache-2.0 §4(b) に基づき、上流からの変更をここに記録すること。

- CMakeLists.txt: MSVCビルドに/Zc:char8_t-を追加（VST3 SDKのmodule_win32.cppがC++20のchar8_tと非互換のため）

## 注意

- 本ディレクトリは Beatrice 本体・推論ライブラリ・音声モデルを一切含まない。
  実行時にユーザーがインストール済みの公式 Beatrice 2 VST3 とモデル TOML を
  コマンドライン引数で指定する設計（上流 README 参照）。
