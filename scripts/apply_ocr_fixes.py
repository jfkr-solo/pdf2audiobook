"""人が画像と見比べて確かめた OCR の誤読を pages.json に反映する。

data/<BOOK>/ocr_fixes.json に [{"page": 21, "find": "ルート8を", "replace": "ルート83を", "note": "..."}] の形で書き、
  python scripts/apply_ocr_fixes.py BOOK002
を extract の後・structure の前に実行する（extract をやり直したら再実行）。page は pages.json のページ番号。
find はそのページの1行の中に1回だけ現れる文字列にする。見つからない・複数ある項目はエラーにして、何も書き換えない。
replace を空にすると、その文字列を消す（本文に紛れ込んだノンブルなど）。"whole_line": true なら行全体が find と同じ行だけを対象にする。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import book_dir, load_config  # noqa: E402


def apply(pages_doc: dict, fixes: list[dict]) -> list[str]:
    by_page = {p["page"]: p for p in pages_doc["pages"]}
    errors = []
    plan = []
    for f in fixes:
        p = by_page.get(f["page"])
        if f.get("whole_line"):
            hits = [l for l in (p or {}).get("lines", []) if l["text"].strip() == f["find"]]
            n = len(hits)
        else:
            hits = [l for l in (p or {}).get("lines", []) if f["find"] in l["text"]]
            n = sum(l["text"].count(f["find"]) for l in hits)
        if n != 1:
            errors.append(f"p.{f['page']}「{f['find']}」: {n} 箇所（1箇所である必要）")
            continue
        plan.append((p, hits[0], f))
    if errors:
        return errors
    for p, line, f in plan:
        line["text"] = f["replace"] if f.get("whole_line") else line["text"].replace(f["find"], f["replace"])
        p.setdefault("ocr_fixes", []).append({k: f[k] for k in ("find", "replace", "note", "whole_line") if k in f})
    for p in {id(x[0]): x[0] for x in plan}.values():
        p["text"] = "\n".join(l["text"] for l in p["lines"] if "in_table" not in l)
    return []


def main() -> int:
    book_id = sys.argv[1]
    bdir = book_dir(load_config(), book_id)
    pages_path = bdir / "pages.json"
    doc = json.loads(pages_path.read_text(encoding="utf-8"))
    if any(p.get("ocr_fixes") for p in doc["pages"]):
        print("この pages.json には反映済みです（extract をやり直してから実行してください）", file=sys.stderr)
        return 1
    fixes = json.loads((bdir / "ocr_fixes.json").read_text(encoding="utf-8"))
    errors = apply(doc, fixes)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    pages_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(fixes)} 件を反映: {pages_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
