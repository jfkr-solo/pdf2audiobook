"""STEP 2: PDF をページ単位で読み込み、本文・見出し候補・図表・要確認箇所を抽出する。

出力は data/<BOOK_ID>/pages.json。推測で文字を補完せず、
判読できない箇所は needs_review に記録する。
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

import pymupdf

# ページ番号だけの行（例: "12", "- 12 -", "p.12", "12 / 240", "― 12 ―"）
PAGE_NUM_RE = re.compile(r"^[\s\-―–—(（]*(p\.?|P\.?|ページ)?\s*\d{1,4}\s*(/\s*\d{1,4})?[\s\-―–—)）]*$")
MATH_CHARS = set("=＝+−-×÷±∑∫√∞≤≥≠≈∂∆πθαβγλμσΣΠ^_")
BAD_CHARS = {"�"}  # 置換文字（文字化け）


def _norm(s: str) -> str:
    """ヘッダー/フッター比較用の正規化（数字は # に置き換え）。"""
    s = unicodedata.normalize("NFKC", s).strip()
    return re.sub(r"\d+", "#", s)


def _page_lines(page: pymupdf.Page, textpage=None) -> list[dict[str, Any]]:
    d = page.get_text("dict", textpage=textpage, sort=True)
    lines = []
    for block in d["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            spans = [s for s in line["spans"] if s["text"].strip()]
            if not spans:
                continue
            text = "".join(s["text"] for s in spans).strip()
            size = max(s["size"] for s in spans)
            bold = any(s["flags"] & 16 for s in spans) or any("Bold" in s.get("font", "") for s in spans)
            lines.append({
                "text": text,
                "size": round(size, 1),
                "bold": bold,
                "font": spans[0].get("font", ""),
                "bbox": [round(v, 1) for v in line["bbox"]],
            })
    return lines


# ---------------------------------------------------------------- OCR
def _ocr_available(engine: str) -> str | None:
    import os
    if engine == "none":
        return None
    if engine == "google":
        if not os.environ.get("GOOGLE_VISION_API_KEY"):
            raise RuntimeError("ocr_engine=google には環境変数 GOOGLE_VISION_API_KEY（.env）が必要です")
        return "google"
    if engine in ("auto", "ocrmac"):
        try:
            import ocrmac  # noqa: F401
            return "ocrmac"
        except ImportError:
            if engine == "ocrmac":
                return None
    if engine in ("auto", "tesseract"):
        import shutil
        if shutil.which("tesseract"):
            return "tesseract"
    return None


# ---------------------------------------------------------------- 図表
def _figures(page: pymupdf.Page) -> list[dict[str, Any]]:
    pno = page.number + 1
    figs: list[dict[str, Any]] = []
    for img in page.get_images(full=True):
        for r in page.get_image_rects(img[0]):
            if r.get_area() > page.rect.get_area() * 0.8:
                continue  # ページ全体のスキャン画像は図ではない
            figs.append({"page": pno, "kind": "image", "bbox": [round(v, 1) for v in r]})
    try:
        tables = page.find_tables()
        for t in tables.tables:
            rows = [[(c or "").replace("\n", " ").strip() for c in row] for row in t.extract()]
            figs.append({"page": pno, "kind": "table", "bbox": [round(v, 1) for v in t.bbox], "rows": rows})
    except Exception:  # 表検出の失敗で全体を止めない
        pass
    return figs


def _in_bbox(line_bbox, box, tol=2.0) -> bool:
    x0, y0, x1, y1 = line_bbox
    return x0 >= box[0] - tol and y0 >= box[1] - tol and x1 <= box[2] + tol and y1 <= box[3] + tol


def _looks_like_formula(text: str) -> bool:
    t = text.replace(" ", "")
    if len(t) < 3:
        return False
    t = unicodedata.normalize("NFKC", t)
    if "=" not in t:
        return False
    if any(c in t for c in "^√∑∫∂"):
        return True
    mathish = sum(1 for c in t if c in MATH_CHARS or c.isdigit() or c in ".,()")
    return mathish / len(t) >= 0.5


# ---------------------------------------------------------------- メイン
KANJI_NUM = "〇一二三四五六七八九十百"
# 余白にある「ページ番号＋柱（章名など）」の行。例: "〇一七プロローグ", "第1章 xx 23"
RUNNING_RE = re.compile(rf"^\s*[#{KANJI_NUM}]{{1,4}}\s*(?P<a>.*?)\s*$|^\s*(?P<b>.*?)\s*[#{KANJI_NUM}]{{1,4}}\s*$")
# 漢数字ノンブル（"〇一七" = 17）。OCRで混同しやすい字も数字として扱う
DIGIT_MAP = {**{c: "0" for c in "〇○OoＯ0"}, **{c: "1" for c in "一ー-1"}, **{c: "2" for c in "二ニ2"},
             "三": "3", "3": "3", "四": "4", "4": "4", "五": "5", "5": "5", "六": "6", "6": "6",
             **{c: "7" for c in "七ヒ7"}, **{c: "8" for c in "八バハ8"}, "九": "9", "9": "9"}


def printed_page_number(text: str) -> int | None:
    """余白行の先頭/末尾にある3桁前後のノンブルを読む。読めなければ None（推測しない）。"""
    t = re.sub(r"[\s,.、。・:;]", "", unicodedata.normalize("NFKC", text))
    for cand in (t, t[::-1]):
        digits = ""
        for c in cand:
            if c not in DIGIT_MAP:
                break
            digits += DIGIT_MAP[c]
        if cand is not t:
            digits = digits[::-1]
        if 2 <= len(digits) <= 4 and int(digits) > 0:
            return int(digits)
    return None


def _norm_margin(s: str) -> str:
    """余白行の比較用: 算用数字・漢数字のページ番号を # にそろえる。"""
    s = _norm(s)
    return re.sub(rf"^[{KANJI_NUM}]{{1,4}}|[{KANJI_NUM}]{{1,4}}$", "#", s)


def _in_margin(bbox, w, h, ratio) -> bool:
    x0, y0, x1, y1 = bbox
    return y1 <= h * ratio or y0 >= h * (1 - ratio) or x1 <= w * ratio or x0 >= w * (1 - ratio)


def extract(pdf_path: str, cfg: dict[str, Any] | None = None, pages_range: range | None = None,
            progress=None) -> dict[str, Any]:
    from . import ocr as ocrmod

    ecfg = (cfg or {}).get("extract", {})
    ocr_min = ecfg.get("ocr_min_chars", 20)
    margin_ratio = ecfg.get("margin_ratio", 0.08)
    repeat_ratio = ecfg.get("repeat_ratio", 0.5)
    ocr_engine = _ocr_available(ecfg.get("ocr_engine", "auto"))
    vertical_cfg = ecfg.get("vertical", "auto")  # auto | true | false
    if ecfg.get("ocr_lang") == "jpn_vert":
        vertical_cfg = True
    ocr_dpi = ecfg.get("ocr_dpi", 300)
    low_conf = ecfg.get("ocr_low_conf", 60)

    doc = pymupdf.open(pdf_path)
    meta = doc.metadata or {}
    pages: list[dict[str, Any]] = []
    vertical: bool | None = None if vertical_cfg == "auto" else bool(vertical_cfg)
    indices = list(pages_range) if pages_range is not None else range(doc.page_count)

    # 1) ページごとに抽出（必要なら OCR）
    for i in indices:
        page = doc[i]
        pno = page.number + 1
        lines = _page_lines(page)
        method, mode, mean_conf = "text", "horizontal", None
        review: list[dict] = []
        n_chars = sum(len(l["text"]) for l in lines)
        if n_chars < ocr_min and page.get_images():
            if ocr_engine == "tesseract":
                if vertical is None:
                    vertical = ocrmod.detect_vertical(page)
                r = ocrmod.tesseract_page(page, vertical, dpi=ocr_dpi, low_conf=low_conf)
            elif ocr_engine == "google":
                import os
                cache = Path(ecfg["cache_dir"]) / f"p{pno:04d}.json" if ecfg.get("cache_dir") else None
                r = ocrmod.google_page(page, os.environ["GOOGLE_VISION_API_KEY"], dpi=ocr_dpi, cache_path=cache)
            elif ocr_engine == "ocrmac":
                r = ocrmod.ocrmac_page(page, dpi=ocr_dpi)
            else:
                r = None
                method = "needs_ocr"
                review.append({"page": pno, "reason": "スキャン画像ページだがOCRエンジンが未設定のため本文を取得できない"})
            if r:
                lines, mode, mean_conf = r["lines"], r["writing_mode"], r["mean_conf"]
                method = f"ocr:{ocr_engine}"
                reason = f"OCRで読み取ったページ（平均信頼度 {mean_conf}）"
                if r["low_conf_words"]:
                    reason += f"。低信頼の語 {len(r['low_conf_words'])} 個"
                review.append({"page": pno, "reason": reason,
                               "text": "、".join(r["low_conf_words"][:20])})
        for l in lines:
            if any(c in BAD_CHARS for c in l["text"]):
                review.append({"page": pno, "reason": "判読不能文字を含む", "text": l["text"]})
        pages.append({
            "page": pno,
            "width": round(page.rect.width, 1),
            "height": round(page.rect.height, 1),
            "method": method,
            "writing_mode": mode,
            "ocr_mean_conf": mean_conf,
            "lines": lines,
            "figures": _figures(page),
            "needs_review": review,
        })
        if progress:
            progress(pno, len(indices))

    # 2) ヘッダー・フッター・柱・ページ番号の検出（上下左右の余白）
    margin_counter: Counter[str] = Counter()
    for p in pages:
        margin_counter.update({_norm_margin(l["text"]) for l in p["lines"]
                               if _in_margin(l["bbox"], p["width"], p["height"], margin_ratio)})
    min_repeat = max(2, int(len(pages) * repeat_ratio)) if len(pages) >= 3 else 10**9
    repeated = {k for k, c in margin_counter.items() if c >= min_repeat}

    # 3) 不要行の除去・図表/数式の印付け
    for p in pages:
        kept, removed = [], []
        running, printed = None, None
        tables = [f for f in p["figures"] if f["kind"] == "table"]
        for l in p["lines"]:
            if l.get("is_ruby"):
                removed.append({"text": l["text"], "reason": "ruby"})
                continue
            ocr_page = p["method"].startswith("ocr")
            if _in_margin(l["bbox"], p["width"], p["height"], margin_ratio * (1.6 if ocr_page else 1)):
                t = l["text"]
                if _norm_margin(t) in repeated or PAGE_NUM_RE.match(t):
                    removed.append({"text": t, "reason": "header_footer"})
                    continue
                if p["method"].startswith("ocr"):
                    m = RUNNING_RE.match(_norm(t))
                    num = printed_page_number(t)
                    if num and printed is None:
                        printed = num
                    if (m or num) and len(t) <= 30:
                        head = re.sub(r"^[^\w「]+|[^\w」]+$", "", re.sub(rf"^[\W#{KANJI_NUM}OoＯーヒバハニ0-9]+", "", unicodedata.normalize("NFKC", t)))
                        running = head or running
                        removed.append({"text": t, "reason": "running_head"})
                        continue
                    if l.get("ocr_conf", 100) < 30:
                        removed.append({"text": t, "reason": "margin_noise"})
                        continue
            for ti, t in enumerate(tables):
                if _in_bbox(l["bbox"], t["bbox"]):
                    l["in_table"] = ti
                    break
            if _looks_like_formula(l["text"]):
                l["formula"] = True
                p["figures"].append({"page": p["page"], "kind": "formula", "bbox": l["bbox"], "text": l["text"]})
            kept.append(l)
        p["lines"] = kept
        p["removed"] = removed
        p["running_head"] = running
        p["printed_page"] = printed
        p["text"] = "\n".join(l["text"] for l in kept if "in_table" not in l)
        if not kept and not p["figures"] and p["method"] != "needs_ocr":
            p["needs_review"].append({"page": p["page"], "reason": "本文が空のページ"})

    body_sizes = Counter(round(l["size"]) for p in pages for l in p["lines"] if "in_table" not in l)
    return {
        "source_pdf": str(pdf_path),
        "title": meta.get("title") or None,
        "author": meta.get("author") or None,
        "page_count": doc.page_count,
        "toc": [{"level": lv, "title": t, "page": pg} for lv, t, pg in doc.get_toc()],
        "body_font_size": body_sizes.most_common(1)[0][0] if body_sizes else None,
        "ocr_engine": ocr_engine,
        "writing_mode": "vertical" if vertical else "horizontal",
        "pages": pages,
    }


def review_markdown(result: dict[str, Any]) -> str:
    """要確認箇所の一覧（人間が見るためのMarkdown）。"""
    out = [f"# 要確認箇所: {result.get('title') or result['source_pdf']}", ""]
    items = [r for p in result["pages"] for r in p["needs_review"]]
    if not items:
        out.append("要確認箇所はありません。")
    for r in items:
        line = f"- p.{r['page']}: {r['reason']}"
        if r.get("text"):
            line += f" 「{r['text'][:60]}」"
        out.append(line)
    figs = [f for p in result["pages"] for f in p["figures"]]
    if figs:
        out += ["", "## 図表・数式（読み上げ時に説明文が必要）", ""]
        for f in figs:
            extra = f" 「{f['text']}」" if f.get("text") else ""
            if f["kind"] == "table":
                extra = f" {len(f['rows'])}行"
            out.append(f"- p.{f['page']}: {f['kind']}{extra}")
    return "\n".join(out) + "\n"
