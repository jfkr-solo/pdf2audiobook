# pdf2audiobook

PDF教材から、内容を正確に保った日本語オーディオブック（MP3）を作るツールです。
設計書: https://claude.ai/artifact/QofUhNvcnDHaaJcTy1wgbz

## 進捗

| STEP | 内容 | 状態 |
| --- | --- | --- |
| 1 | 骨格・設定・JSON Schema・SQLite | 完了 |
| 2 | PDFテキスト抽出・OCR（縦書き対応）・ヘッダー/フッター除去・図表検出 | 完了 |
| 3 | 章・節の構造化（ページ順補正・重複スキャン除去・柱から章検出） | 完了 |
| 4 | 読み上げ用文章の生成 | 次 |
| 5〜11 | 辞書、TTS、結合、検証、Web、外部アクセス | 未着手 |

## セットアップ（Mac mini）

```bash
brew install python@3.12 ffmpeg tesseract tesseract-lang   # tesseract-lang に縦書き用 jpn_vert も含まれる
cd pdf2audiobook
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install ocrmac          # 任意: macOS Vision でOCR（日本語の精度が高い）
cp .env.example .env        # APIキーは .env に（Gitに入れない）
```

## STEP 1〜2 の実行方法

```bash
python scripts/make_sample_pdf.py tests/fixtures/sample.pdf   # 動作確認用PDFを作る
python -m app.cli init-db
python -m app.cli extract tests/fixtures/sample.pdf            # 自分の教材なら PDF のパスを指定
python -m pytest -q                                            # テスト
```

期待される出力:

```
書籍ID: BOOK001  タイトル: IT基礎テキスト
ページ数: 5  目次項目: 7  OCR: tesseract
除去したヘッダー/フッター行: 6  図表・数式: 3
要確認: 1 件 → data/BOOK001/review.md
出力: data/BOOK001/pages.json
```

- `data/BOOK001/pages.json`: ページごとの行（文字・フォントサイズ・位置）、本文テキスト、除去した行、図表・数式、要確認箇所
- `data/BOOK001/review.md`: 人が確認すべき箇所の一覧（OCRページ、判読不能文字、図表）

## STEP 3 の実行方法

```bash
python -m app.cli extract path/to/book.pdf --title 書名   # 縦書きスキャンは自動判定（tesseract-lang の jpn_vert が必要）
python -m app.cli structure BOOK001                       # → data/BOOK001/manuscript.json と structure_report.md
python -m app.cli validate data/BOOK001/manuscript.json
```

- `manuscript.json`: 章・節ごとの原文（`original_text`）。`audio_text` は STEP 4 で埋める
- `structure_report.md`: 章・節の一覧、字数、読み上げ時間の目安、ページ順の補正や除外したノイズの記録

## 設計上の約束

- 判読できない文字は推測で補わず `needs_review` に記録する
- 原文（`original_text`）と読み上げ文（`audio_text`）は別々に保存する
- AIが書いた補足・要約は `ai_generated: true` で区別する
- 各工程の成果物をファイルに残し、失敗した工程から再開できる

## OCR方式の比較（Mac mini）

```bash
pip install ocrmac
python -m app.cli extract 生き方.pdf --book-id BOOK002 --title 生き方 --ocr-engine ocrmac --pages 1-20
python -m app.cli extract 生き方.pdf --book-id BOOK001 --title 生き方 --ocr-engine tesseract --pages 1-20
python scripts/compare_ocr.py data/BOOK001/pages.json data/BOOK002/pages.json --pages 4,11
```

## Google Cloud Vision で OCR する

1. [Google Cloud Console](https://console.cloud.google.com/) でプロジェクトを作り、課金アカウントを紐付ける（毎月 1,000 ページまでは無料枠）
2. 「APIとサービス」で **Cloud Vision API** を有効化
3. 「認証情報」で APIキーを作成し、キーの制限で「Cloud Vision API」のみに絞る
4. `.env` に `GOOGLE_VISION_API_KEY=取得したキー` を書く（Git には入れない）

```bash
python -m app.cli extract 生き方.pdf --book-id BOOK001 --title 生き方 --ocr-engine google
python -m app.cli structure BOOK001
```

応答は `data/<BOOK_ID>/cache/ocr_google/` に保存され、再実行してもAPIは呼ばれません（二重課金なし）。
