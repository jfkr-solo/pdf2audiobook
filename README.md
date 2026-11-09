# pdf2audiobook

PDF教材から、内容を正確に保った日本語オーディオブック（MP3）を作るツールです。
設計書: https://claude.ai/artifact/QofUhNvcnDHaaJcTy1wgbz

## 進捗

| STEP | 内容 | 状態 |
| --- | --- | --- |
| 1 | 骨格・設定・JSON Schema・SQLite | 完了 |
| 2 | PDFテキスト抽出・OCR（縦書き対応）・ヘッダー/フッター除去・図表検出 | 完了 |
| 3 | 章・節の構造化（ページ順補正・重複スキャン除去・柱から章検出） | 完了 |
| 4 | 読み上げ用文章の生成（ルール整形＋任意で生成AI） | 完了 |
| 5 | 読み方辞書 | 完了 |
| 6 | JSON保存・原文照合の検証 | 完了 |
| 7 | TTS（Open JTalk / Google / VOICEVOX / say を切り替え） | 完了 |
| 8 | 結合・MP3 | 次 |
| 9〜11 | 結合・MP3、品質確認、Web、外部アクセス | 未着手 |

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

## STEP 4 の実行方法

```bash
python -m app.cli script BOOK001                         # ルール整形のみ（APIキー不要）
python -m app.cli script BOOK001 --llm anthropic         # 生成AIでも整形（.env に ANTHROPIC_API_KEY、pip install anthropic）
python -m app.cli script BOOK001 --sections CH002-SEC003 # 指定した節だけ作り直す
```

- `manuscript.json` の各節に `audio_text` を書き込み、`script_report.md` に節ごとの字数と整形で変わった箇所の例を出す
- ルール整形: 漢数字の数値（一九九七年→1997年、一〇〇点→100点。「四六時中」などの語は変えない）、
  ダッシュ・三点リーダ→読点、〝〟や " → 「」、丸数字→「一つ目、」、×→かける、=→イコール、括弧→前後に間、OCRのゴミ記号を除去
- 生成AI整形は内容を削らない指示で行い、原文との一致率が 85% 未満なら採用せずルール整形に戻す（理由は節の `validation.messages` に記録）
- 文章が変わった節の音声は `stale` になり、STEP 7 で作り直される
- 英字・略語の読み（DDI など）は STEP 5 の辞書で扱う（script 実行時にも辞書を適用する）

## STEP 5 の実行方法

```bash
python -m app.cli dict BOOK001              # 辞書を適用し、残った英字の読み候補を data/BOOK001/dictionary.json に追加
python -m app.cli dict BOOK001 --no-suggest # 候補を足さず一覧だけ
```

- 共通辞書 `dictionaries/common.json` → 教材辞書 `data/BOOK001/dictionary.json` の順に読み、教材辞書が優先
- 長い表記から順に置き換え、英数字の語は語の途中では置き換えない（DDI の辞書で KDDI を壊さない）
- 大文字の略語は一文字ずつの読み（DDI→ディーディーアイ）を候補として `"auto": true` 付きで追加。
  読めない英単語は reading を空で追加するので、`dictionary_report.md` を見て読みを書き、確かめたら `auto` を消す
- 辞書を直したら `python -m app.cli dict BOOK001` をもう一度実行（または `script` で作り直し）

## STEP 6 の実行方法

```bash
python -m app.cli check BOOK001   # → manuscript.json の各節の validation と validation_report.md
```

- 文字カバー率: 原文の漢字・ひらがなが読み上げ文に同じ順で残っている割合（カタカナ・英字・記号は読み替えがあるので除外、漢数字は算用数字にそろえて比較）
- 数値の一致: 原文の数（漢数字は算用数字にそろえる）が読み上げ文にすべてあるか
- 章・節の順序: ID の連番、原文ページの逆戻り
- 判定: passed（95%以上かつ数値一致）/ warning（要確認。数値の不一致、OCRで不確かだった漢字・かなを含む）/ failed（85%未満または空。音声化の前に直す）
- 要修正の節があると終了コード 1

## STEP 7 の実行方法

```bash
python -m app.cli tts BOOK001                        # config.yaml の tts.engine で全節を合成
python -m app.cli tts BOOK001 --limit 2              # 試しに2節だけ
python -m app.cli tts BOOK001 --engine google        # エンジンを切り替える
python -m app.cli tts BOOK001 --sections CH001-SEC003 --force
```

- 節ごとの WAV（16bit・モノラル・24kHz）を `data/<BOOK>/cache/tts/<節ID>_<ハッシュ>.wav` に作る（Git には入れない）
- 文ごとに合成し、文の間に `sentence_pause_ms` の無音を入れる。長い文は読点で分ける
- 読み上げ文と声の設定のハッシュが同じなら作り直さない。文章や設定が変わった節だけ作り直し、古い WAV は消す
- STEP 6 で failed の節は飛ばす（`--force` で作る）。失敗した節は `audio.error` に理由が残る
- エンジン（`tts.engine`）:
  - `openjtalk`: 無料・オフライン。Linux のクラウド環境でも動く（`pip install pyopenjtalk-plus`）。声は機械的なので確認用
  - `google`: Google Cloud Text-to-Speech。`.env` に `GOOGLE_TTS_API_KEY`（Text-to-Speech API を有効にしたキー）
  - `voicevox`: VOICEVOX エンジン（`tts.voicevox_url`、既定 http://127.0.0.1:50021）。公開時はクレジット表記が必要
  - `macos_say`: macOS の say コマンド
- 話者はエンジンごとに `tts.speakers` で指定（`--speaker` で上書き）

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
