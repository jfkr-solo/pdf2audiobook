"""コマンドライン入口。

使い方:
    python -m app.cli init-db
    python -m app.cli extract path/to/book.pdf [--book-id BOOK001] [--title 書名]
    python -m app.cli structure BOOK001
    python -m app.cli script BOOK001 [--llm anthropic] [--sections CH001-SEC002,...]
    python -m app.cli dict BOOK001
    python -m app.cli check BOOK001
    python -m app.cli tts BOOK001 [--engine voicevox] [--limit 3]
    python -m app.cli build BOOK001 [--chapters CH001] [--allow-missing]
    python -m app.cli validate data/BOOK001/manuscript.json
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import db, manuscript
from .config import book_dir, load_config


def cmd_init_db(cfg, args) -> int:
    db.connect(Path(cfg["_root"]) / cfg["db_path"])
    print("DB を初期化しました:", cfg["db_path"])
    return 0


def cmd_extract(cfg, args) -> int:
    from .pdf_extract import extract, review_markdown

    src = Path(args.pdf)
    if not src.exists():
        print(f"PDF が見つかりません: {src}", file=sys.stderr)
        return 1
    conn = db.connect(Path(cfg["_root"]) / cfg["db_path"])
    book_id = args.book_id or db.next_book_id(conn)
    bdir = book_dir(cfg, book_id)
    dst = bdir / "source.pdf"
    if src.resolve() != dst.resolve():
        shutil.copy2(src, dst)

    job = db.start_job(conn, book_id, "extract")
    try:
        rng = None
        if args.pages:
            a, _, b = args.pages.partition("-")
            rng = range(int(a) - 1, int(b or a))
        prog = lambda i, n: print(f"\r  抽出中 p.{i} ({n}ページ)", end="", file=sys.stderr, flush=True)  # noqa: E731
        if args.ocr_engine:
            cfg.setdefault("extract", {})["ocr_engine"] = args.ocr_engine
        # クラウドOCRの応答はキャッシュし、再実行で二重に課金されないようにする
        cfg.setdefault("extract", {}).setdefault("cache_dir", str(bdir / "cache" / "ocr_google"))
        result = extract(str(dst), cfg, rng, progress=prog)
        print(file=sys.stderr)
        result["book_id"] = book_id
        result["title"] = args.title or result["title"] or src.stem
        (bdir / "pages.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        (bdir / "review.md").write_text(review_markdown(result), encoding="utf-8")
        db.upsert_book(conn, book_id, result["title"], dst, result["page_count"], result["author"])
    except Exception as e:
        db.finish_job(conn, job, "failed", repr(e))
        raise
    n_review = sum(len(p["needs_review"]) for p in result["pages"])
    n_removed = sum(len(p["removed"]) for p in result["pages"])
    db.finish_job(conn, job, "done", f"pages={result['page_count']} review={n_review}")

    print(f"書籍ID: {book_id}  タイトル: {result['title']}")
    print(f"ページ数: {result['page_count']}  目次項目: {len(result['toc'])}  OCR: {result['ocr_engine'] or '未設定'}")
    print(f"除去したヘッダー/フッター行: {n_removed}  図表・数式: {sum(len(p['figures']) for p in result['pages'])}")
    print(f"要確認: {n_review} 件 → {bdir / 'review.md'}")
    print(f"出力: {bdir / 'pages.json'}")
    return 0


def cmd_structure(cfg, args) -> int:
    from .structure import build, report_markdown

    bdir = book_dir(cfg, args.book_id)
    pages_doc = json.loads((bdir / "pages.json").read_text(encoding="utf-8"))
    conn = db.connect(Path(cfg["_root"]) / cfg["db_path"])
    job = db.start_job(conn, args.book_id, "structure")
    try:
        m, notes = build(pages_doc)
        manuscript.save(m, bdir / "manuscript.json")
        (bdir / "structure_report.md").write_text(report_markdown(m, notes), encoding="utf-8")
    except Exception as e:
        db.finish_job(conn, job, "failed", repr(e))
        raise
    n_sec = sum(len(c["sections"]) for c in m["chapters"])
    db.finish_job(conn, job, "done", f"chapters={len(m['chapters'])} sections={n_sec}")
    print(f"章: {len(m['chapters'])}  節: {n_sec}")
    for c in m["chapters"]:
        print(f"  {c['chapter_id']} {c['chapter_title']}（{len(c['sections'])}節）")
    print(f"出力: {bdir / 'manuscript.json'}")
    print(f"レポート: {bdir / 'structure_report.md'}")
    return 0


def _dict_paths(cfg, m, bdir) -> list[Path]:
    root = Path(cfg["_root"])
    paths = [root / p for p in m.get("dictionary_files") or ["dictionaries/common.json"]]
    book = bdir / "dictionary.json"
    if book not in paths:
        paths.append(book)  # 教材辞書を最後に（共通辞書より優先）
    return paths


def _dictionary(cfg, m, bdir):
    from . import reading_dict
    return reading_dict.load(_dict_paths(cfg, m, bdir))


def cmd_dict(cfg, args) -> int:
    """STEP 5: 辞書を読み上げ文に適用し、残った英字を未登録語として教材辞書に候補を書き足す。"""
    from . import reading_dict

    bdir = book_dir(cfg, args.book_id)
    m = manuscript.load(bdir / "manuscript.json")
    book_dict = bdir / "dictionary.json"
    entries = _dictionary(cfg, m, bdir)
    secs = [s for c in m["chapters"] for s in c["sections"]]
    for s in secs:
        new = reading_dict.apply(s["audio_text"], entries)
        if new != s["audio_text"] and s["audio"].get("status") == "done":
            s["audio"]["status"] = "stale"
        s["audio_text"] = new
    unknown = reading_dict.unknown_terms([s["audio_text"] for s in secs])
    added = []
    if unknown and not args.no_suggest:
        added = reading_dict.suggest(book_dict, unknown)
        entries = _dictionary(cfg, m, bdir)
        for s in secs:
            s["audio_text"] = reading_dict.apply(s["audio_text"], entries)
        unknown = reading_dict.unknown_terms([s["audio_text"] for s in secs])
    rel = str(book_dict.relative_to(Path(cfg["_root"]))) if book_dict.is_relative_to(Path(cfg["_root"])) else str(book_dict)
    files = [str(p.relative_to(Path(cfg["_root"]))) if p.is_relative_to(Path(cfg["_root"])) else str(p)
             for p in _dict_paths(cfg, m, bdir) if p.exists()]
    m["dictionary_files"] = files or [rel]
    manuscript.save(m, bdir / "manuscript.json")
    book_entries = reading_dict.load([book_dict])
    (bdir / "dictionary_report.md").write_text(
        reading_dict.report_markdown(m.get("title", ""), unknown, book_entries, book_dict), encoding="utf-8")
    print(f"辞書 {len(entries)} 語を適用。新しい候補 {len(added)} 語を {book_dict} に追加"
          f"（確認が必要な自動候補 {sum(1 for e in book_entries if e.get('auto'))} 語）")
    if unknown:
        print("読みが決まらず残った英字:", "、".join(f"{t}({n})" for t, n in unknown.most_common(20)))
    print(f"レポート: {bdir / 'dictionary_report.md'}")
    return 0


def cmd_script(cfg, args) -> int:
    from . import script_gen

    bdir = book_dir(cfg, args.book_id)
    m = manuscript.load(bdir / "manuscript.json")
    if args.llm:
        cfg.setdefault("llm", {})["provider"] = args.llm
    rewriter = script_gen.make_rewriter(cfg)
    conn = db.connect(Path(cfg["_root"]) / cfg["db_path"])
    job = db.start_job(conn, args.book_id, "script")
    only = set(args.sections.split(",")) if args.sections else None
    prog = lambda i, n, sid: print(f"\r  整形中 {sid} ({i}/{n})", end="", file=sys.stderr, flush=True)  # noqa: E731
    try:
        stats = script_gen.generate(m, cfg, rewriter, only=only, progress=prog, dictionary=_dictionary(cfg, m, bdir))
        print(file=sys.stderr)
        manuscript.save(m, bdir / "manuscript.json")
        (bdir / "script_report.md").write_text(script_gen.report_markdown(m, stats), encoding="utf-8")
    except Exception as e:
        db.finish_job(conn, job, "failed", repr(e))
        raise
    db.finish_job(conn, job, "done", " ".join(f"{k}={v}" for k, v in stats.items()))
    mode = "ルール＋生成AI" if rewriter else "ルールのみ"
    print(f"読み上げ文を作成しました（{mode}）: 節 {stats['sections']}  生成AI {stats['llm']}  "
          f"不採用 {stats['llm_rejected']}  失敗 {stats['llm_failed']}  本文なし {stats['empty']}")
    print(f"レポート: {bdir / 'script_report.md'}")
    return 0


def cmd_check(cfg, args) -> int:
    """STEP 6: 読み上げ文を原文と照合し、節ごとの validation を保存する。"""
    from . import text_check

    bdir = book_dir(cfg, args.book_id)
    m = manuscript.load(bdir / "manuscript.json")
    result = text_check.run(m)
    errors = manuscript.validate(m)
    if not errors:
        manuscript.save(m, bdir / "manuscript.json")
    (bdir / "validation_report.md").write_text(text_check.report_markdown(m, result, errors), encoding="utf-8")
    st = result["status"]
    print(f"問題なし {st.get('passed', 0)}  要確認 {st.get('warning', 0)}  要修正 {st.get('failed', 0)}  "
          f"順序の指摘 {len(result['order'])}  スキーマ {'OK' if not errors else 'NG'}")
    print(f"レポート: {bdir / 'validation_report.md'}")
    return 1 if errors or st.get("failed") else 0


def cmd_tts(cfg, args) -> int:
    """STEP 7: 節ごとの WAV を data/<BOOK>/cache/tts/ に作る。"""
    from .tts import base, synth

    bdir = book_dir(cfg, args.book_id)
    m = manuscript.load(bdir / "manuscript.json")
    settings = synth.voice_settings(cfg, args.engine)
    if args.speaker:
        settings["speaker"] = args.speaker
    engine = base.get_engine(settings["engine"], cfg)
    ok, info = engine.available()
    if not ok:
        print(f"TTS エンジン {settings['engine']} が使えません: {info}", file=sys.stderr)
        return 1
    only = set(args.sections.split(",")) if args.sections else None
    conn = db.connect(Path(cfg["_root"]) / cfg["db_path"])
    job = db.start_job(conn, args.book_id, "tts")
    prog = lambda i, n, sid: print(f"\r  合成中 {sid} ({i}/{n})", end="", file=sys.stderr, flush=True)  # noqa: E731
    try:
        stats = synth.run(m, bdir, engine, settings, only=only, force=args.force, limit=args.limit, progress=prog)
        print(file=sys.stderr)
    finally:
        manuscript.save(m, bdir / "manuscript.json")  # 途中で止めても、できた節の記録は残す
    db.finish_job(conn, job, "failed" if stats["failed"] else "done", " ".join(f"{k}={v}" for k, v in stats.items()))
    total = sum(s["audio"].get("duration_sec") or 0 for c in m["chapters"] for s in c["sections"])
    print(f"エンジン {settings['engine']}（{settings.get('speaker', '既定')}）: 作成 {stats['done']}  再利用 {stats['reused']}  "
          f"検証NGで省略 {stats['skipped']}  失敗 {stats['failed']}  合計 {total / 60:.1f} 分")
    for c in m["chapters"]:
        for s in c["sections"]:
            if s["audio"].get("status") == "failed" and s["audio"].get("error"):
                print(f"  失敗 {s['section_id']}: {s['audio']['error']}")
    return 1 if stats["failed"] else 0


def cmd_build(cfg, args) -> int:
    """STEP 8: 節ごとの WAV を章ごとの MP3 と全章結合版にまとめる（data/<BOOK>/audio/）。"""
    from . import audio, reading_dict
    from .script_gen import rule_based
    from .tts import base, synth

    if not audio.ffmpeg_available():
        print("ffmpeg がありません（brew install ffmpeg / apt install ffmpeg）", file=sys.stderr)
        return 1
    bdir = book_dir(cfg, args.book_id)
    m = manuscript.load(bdir / "manuscript.json")
    st = audio.build_settings(cfg)
    if args.no_headings:
        st["read_headings"] = False
    engine, voice = None, {}
    if st["read_headings"]:
        # 見出しは本文と同じ声で読む（STEP 7 の設定を使う）
        voice = synth.voice_settings(cfg, (m.get("voice_settings") or {}).get("engine"))
        if (m.get("voice_settings") or {}).get("speaker"):
            voice["speaker"] = m["voice_settings"]["speaker"]
        engine = base.get_engine(voice["engine"], cfg)
        ok, info = engine.available()
        if not ok:
            print(f"見出しを読む TTS エンジン {voice['engine']} が使えません: {info}（--no-headings で省略可）", file=sys.stderr)
            return 1
    entries = _dictionary(cfg, m, bdir)
    speakable = lambda t: reading_dict.apply(rule_based(t), entries)  # noqa: E731
    only = set(args.chapters.split(",")) if args.chapters else None
    conn = db.connect(Path(cfg["_root"]) / cfg["db_path"])
    job = db.start_job(conn, args.book_id, "build")
    prog = lambda i, n, cid: print(f"\r  書き出し中 {cid} ({i}/{n})", end="", file=sys.stderr, flush=True)  # noqa: E731
    try:
        stats = audio.Builder(m, bdir, st, engine, voice, speakable).run(
            only=only, force=args.force, allow_missing=args.allow_missing, whole=only is None, progress=prog)
        print(file=sys.stderr)
    finally:
        manuscript.save(m, bdir / "manuscript.json")
    (bdir / "audio_report.md").write_text(audio.report_markdown(m), encoding="utf-8")
    db.finish_job(conn, job, "failed" if stats["failed"] else "done",
                  f"built={stats['built']} reused={stats['reused']} failed={stats['failed']} whole={stats['whole']}")
    print(f"章: 作成 {stats['built']}  再利用 {stats['reused']}  失敗 {stats['failed']}  全章結合版: {stats['whole']}")
    for c in m["chapters"]:
        a = c.get("audio") or {}
        if a.get("status") == "failed":
            print(f"  失敗 {c['chapter_id']}: {a.get('error')}")
    print(f"出力: {bdir / 'audio'}  一覧: {bdir / 'audio_report.md'}")
    return 1 if stats["failed"] or stats["whole"] == "failed" else 0


def cmd_validate(cfg, args) -> int:
    errors = manuscript.validate(manuscript.load(args.path))
    if errors:
        print("NG:")
        for e in errors:
            print("  -", e)
        return 1
    print("OK: スキーマに適合しています")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pdf2audiobook")
    ap.add_argument("--config", help="config.yaml のパス")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    p = sub.add_parser("extract", help="STEP 2: PDF からページ単位でテキスト抽出")
    p.add_argument("pdf")
    p.add_argument("--book-id")
    p.add_argument("--title")
    p.add_argument("--pages", help="対象ページ（例: 1-30）。省略で全ページ")
    p.add_argument("--ocr-engine", choices=["auto", "google", "ocrmac", "tesseract", "none"], help="config.yaml の設定を上書き")
    p = sub.add_parser("structure", help="STEP 3: 章・節を構造化して manuscript.json を作成")
    p.add_argument("book_id")
    p = sub.add_parser("script", help="STEP 4: 節ごとの読み上げ文（audio_text）を作成")
    p.add_argument("book_id")
    p.add_argument("--llm", choices=["none", "anthropic"], help="config.yaml の llm.provider を上書き")
    p.add_argument("--sections", help="作り直す節ID（カンマ区切り）。省略で全節")
    p = sub.add_parser("dict", help="STEP 5: 読み方辞書を適用し、未登録語の読み候補を教材辞書に追加")
    p.add_argument("book_id")
    p.add_argument("--no-suggest", action="store_true", help="候補を書き足さず、未登録語の一覧だけ出す")
    p = sub.add_parser("check", help="STEP 6: 読み上げ文を原文と照合（文字カバー率・数値・順序）")
    p.add_argument("book_id")
    p = sub.add_parser("tts", help="STEP 7: 節ごとの音声（WAV）を作成")
    p.add_argument("book_id")
    p.add_argument("--engine", choices=["openjtalk", "google", "voicevox", "macos_say"], help="config.yaml の tts.engine を上書き")
    p.add_argument("--speaker", help="話者（say: Kyoko など / VOICEVOX: スタイルID / Google: 音声名）")
    p.add_argument("--sections", help="作る節ID（カンマ区切り）。省略で全節")
    p.add_argument("--limit", type=int, help="新しく作る節の数の上限（試しに数節だけ作るとき）")
    p.add_argument("--force", action="store_true", help="ハッシュが同じでも作り直す。検証 failed の節も作る")
    p = sub.add_parser("build", help="STEP 8: 章ごとの MP3 と全章結合版を作成")
    p.add_argument("book_id")
    p.add_argument("--chapters", help="作り直す章ID（カンマ区切り）。指定すると全章結合版は作らない")
    p.add_argument("--allow-missing", action="store_true", help="音声の無い節を飛ばして章を作る")
    p.add_argument("--no-headings", action="store_true", help="章・節の見出しを読み上げない")
    p.add_argument("--force", action="store_true", help="変わっていなくても作り直す")
    p = sub.add_parser("validate", help="原稿JSONをスキーマ検証")
    p.add_argument("path")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    return {"init-db": cmd_init_db, "extract": cmd_extract, "structure": cmd_structure, "script": cmd_script, "dict": cmd_dict, "check": cmd_check, "tts": cmd_tts, "build": cmd_build, "validate": cmd_validate}[args.cmd](cfg, args)


if __name__ == "__main__":
    sys.exit(main())
