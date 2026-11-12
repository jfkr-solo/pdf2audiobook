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
| 8 | 結合・MP3（章ごと・全章結合版、音量正規化、ID3タグ） | 完了 |
| 9 | 音声の品質確認・部分再生成 | 完了 |
| 10 | Web画面・スマホ対応（再生・続きから・倍速・処理状況） | 完了 |
| 11 | 外部アクセス・RSS（Cloud Run） | 完了（配置は手順に沿って実行） |

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
- 漢字の読み違い（「思いを描く」を「かく」と読むなど）は `"yomi": true` の項目で直す。表記は残したまま、
  合成時に Google なら `<phoneme alphabet="yomigana">`、他のエンジンなら仮名に置き換える。`"pattern"` で文脈を限定できる
  ```json
  {"surface": "描く", "reading": "えがく", "yomi": true, "pattern": "(?<!絵に)描く"}
  ```
  読みを直した節だけが `tts` で作り直しになる
- Google が実際にどう読んでいるかは `python scripts/check_readings.py BOOK001` で洗い出せる
  （要 `pip install sudachipy sudachidict_core`。読み指定ありとなしの音声が同じかで判定し、`reading_check_report.md` に一覧）。
  `--try "文脈" 語 候補1 候補2` で、その箇所の Google の読みを候補から当てられる

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

## STEP 8 の実行方法

```bash
python -m app.cli build BOOK001                  # 章ごとの MP3 と全章結合版 → data/BOOK001/audio/
python -m app.cli build BOOK001 --chapters CH002 # その章だけ作り直す（全章結合版は作らない）
python -m app.cli build BOOK001 --allow-missing  # 音声の無い節を飛ばして作る
```

- 章ごとに「章タイトル → 節見出し → 本文 …」の順でつなぎ、FFmpeg の loudnorm で音量をそろえて MP3（モノラル、`audio.bitrate`）にする
- ファイル名は `00_プロローグ.mp3`、`01_第1章 思いを実現させる.mp3` …、`99_全章結合版.mp3`
- ID3 タグ: タイトル（章名）、アルバム（書名）、トラック番号、コメント（書籍ID・章ID・作成日時）
- 見出しは STEP 7 と同じ声で読む（`audio.read_headings: false` か `--no-headings` で省略）。見出しの音声も使い回す
- 材料（節の音声・見出し・設定）が変わっていない章は作り直さない。全章結合版は章の MP3 をつなぐだけなので速い
- 音声の無い節がある章は作らず、`audio_report.md` と章の `audio.error` に理由を残す
- 間の長さ: 見出しの後 `audio.heading_pause_ms`（章タイトルの後はその2倍）、節の間 `tts.section_pause_ms`、章の間 `tts.chapter_pause_ms`

## STEP 9 の実行方法

```bash
python -m app.cli audio-check BOOK001               # → audio_check_report.md
python -m app.cli audio-check BOOK001 --regenerate  # 要作り直しの節だけ合成し直し、章の MP3 をまとめ直す
python -m app.cli audio-check BOOK001 --mark-stale --include-warnings   # 要確認の節も stale にする（作り直しは tts → build）
```

- 節ごとの判定:
  - 話す速さ（読み上げ文の文字数 ÷ 再生時間）が本全体の中央値から外れていないか（`audio_check.rate_warn` / `rate_fail`）
  - 途中の長い無音（`max_silence_sec`）、末尾の途切れ、音割れ
  - 無音だけの音声、ファイルが無い、読み上げ文や声の設定が音声を作った後に変わった、stale のまま
- 章の MP3 と全章結合版: ファイルがあるか、長さが記録と合うか（ffprobe）、章より新しい節の音声が無いか
- 判定: passed / warning（要確認）/ failed（要作り直し）。failed があると終了コード 1
- 同じ文章と設定なら同じ音になるエンジン（Open JTalk など）では、作り直しても直らないことがある。そのときは STEP 4〜5 で読み上げ文や辞書を直す

## STEP 10 の実行方法（Web 画面）

```bash
APP_PASSWORD=好きなパスワード uvicorn app.web.main:create_app --factory --host 0.0.0.0 --port 8000
```

- `/`: ライブラリ（表紙、聞いた割合と残り時間、「続きを聞く」、すべて／途中／未再生／聞き終わった の絞り込み）。表紙は `data/<本>/cover.jpg`（または .png）を置くとそれを使い、無ければ書名から作る。`/books/BOOK001`: 章を選んで再生。`/books/BOOK001/status`: 処理状況と履歴
- 続きから再生: 章ごとの再生位置と速さを、その端末のブラウザに保存する
- 再生速度 1.0〜2.0 倍、15秒戻る・30秒進む、章が終わると次の章へ。ロック画面からも操作できる（Media Session）
- 音声は `/media/<本>/<ファイル>` からシークできる形（Range 対応）で配信。`audio/` の MP3 以外は返さない
- `APP_PASSWORD` を設定するとパスワードが必要になる（ユーザー名は何でもよい）。外部に公開するときは必ず設定する
- iPhone の Safari で開き、「ホーム画面に追加」でアプリのように使える

### ポッドキャストで聞く（STEP 11）

- `/feed/<本>.xml` は章を1話ずつにした RSS（1章から順に並ぶ serial 形式、検索に載らないよう itunes:block）
- `APP_PASSWORD` があるときは、RSS と音声の URL に合言葉 `?t=...` が付く（パスワードから作る。`APP_FEED_TOKEN` で指定も可）。合言葉では Web 画面は開けない
- URL は処理状況のページ（`/books/<本>/status`）に表示される。公開 URL が違う場合（プロキシの内側など）は `APP_BASE_URL` を設定
- Apple Podcast の「URLで番組をフォロー」や Overcast に登録すると、倍速・続きから・オフライン再生がアプリ側で使える

## Cloud Run に置く（iPhone から外出先で聞く）

Web 画面だけを Cloud Run で動かし、音声は Cloud Storage のバケットから読みます（音声づくりはこのリポジトリの CLI で行う）。

1. [Google Cloud Shell](https://shell.cloud.google.com/) を開き、次を実行（パスワードを聞かれるので決めて入力）
   ```bash
   git clone https://github.com/jfkr-solo/pdf2audiobook.git && cd pdf2audiobook
   gcloud config set project <プロジェクトID>
   bash deploy/cloudrun.sh
   ```
2. 本のデータ（`BOOK001/manuscript.json` と `BOOK001/audio/*.mp3`）をバケットに置く
   ```bash
   unzip BOOK001_upload.zip && gcloud storage cp -r BOOK001 gs://<プロジェクトID>-pdf2audiobook/
   ```
3. 表示された URL を iPhone の Safari で開き、パスワードを入れる（ユーザー名は何でもよい）。ポッドキャストの URL は処理状況のページにある

- バケットは非公開（public access prevention）。音声は Web 画面のパスワードか RSS の合言葉が無いと取れない
- 使わないときはインスタンス 0 台になるので、料金はほぼ音声の保存と転送の分だけ
- コードを更新したら `git pull && bash deploy/cloudrun.sh` で上書き

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
