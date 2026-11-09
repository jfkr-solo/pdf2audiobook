"""2つの pages.json（例: tesseract と macOS Vision）の読み取り結果をページごとに比べる。

    python scripts/compare_ocr.py data/BOOK001/pages.json data/BOOK002/pages.json --pages 4,11,60
"""
import argparse
import difflib
import json


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--pages", default="4,11")
    args = ap.parse_args()
    A = {p["page"]: p for p in json.load(open(args.a, encoding="utf-8"))["pages"]}
    B = {p["page"]: p for p in json.load(open(args.b, encoding="utf-8"))["pages"]}
    for n in (int(x) for x in args.pages.split(",")):
        a, b = A.get(n, {}).get("text", ""), B.get(n, {}).get("text", "")
        ratio = difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
        print(f"===== p.{n}  一致率 {ratio:.1%}  文字数 {len(a)} / {len(b)}")
        for line in difflib.unified_diff(a.splitlines(), b.splitlines(), "A", "B", lineterm="", n=0):
            print(line)


if __name__ == "__main__":
    main()
