"""コマンドライン入口。

使い方:
    python -m app.cli init-db
    python -m app.cli extract path/to/book.pdf [--book-id BOOK001] [--title 書名]
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
    p = sub.add_parser("validate", help="原稿JSONをスキーマ検証")
    p.add_argument("path")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    return {"init-db": cmd_init_db, "extract": cmd_extract, "structure": cmd_structure, "validate": cmd_validate}[args.cmd](cfg, args)


if __name__ == "__main__":
    sys.exit(main())
