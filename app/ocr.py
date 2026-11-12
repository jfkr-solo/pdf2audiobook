"""スキャンページのOCR。縦書き（jpn_vert）と横書き（jpn）の両方に対応する。

各エンジンは「行」のリストを返す。行は pdf_extract の行と同じ形
（text, size, bold, font, bbox[PDF座標], ocr_conf）で、縦書きなら右の行から順に並ぶ。
信頼度の低い語は推測で直さず、low_conf_words として返して要確認に回す。
"""
from __future__ import annotations

import csv
import io
import re
import statistics
import subprocess
from typing import Any

import pymupdf

CJK_SPACE_RE = re.compile(r"(?<=[^\x00-\x7F]) +|(?<=[\x00-\x7F]) +(?=[^\x00-\x7F])")


def join_cjk(s: str) -> str:
    """tesseract が日本語の語の間に入れる空白を取り除く（英単語間の空白は残す）。"""
    return CJK_SPACE_RE.sub("", s).strip()


def _tesseract_tsv(png: bytes, lang: str, psm: int) -> list[dict[str, str]]:
    out = subprocess.run(
        ["tesseract", "stdin", "stdout", "-l", lang, "--psm", str(psm), "tsv"],
        input=png, capture_output=True, check=True,
    )
    text = out.stdout.decode("utf-8", errors="replace")
    return list(csv.DictReader(io.StringIO(text), delimiter="\t", quoting=csv.QUOTE_NONE))


def tesseract_page(page: pymupdf.Page, vertical: bool, dpi: int = 300,
                   low_conf: float = 60.0, clip=None) -> dict[str, Any]:
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, clip=clip)
    lang, psm = ("jpn_vert", 5) if vertical else ("jpn", 3)
    rows = _tesseract_tsv(pix.tobytes("png"), lang, psm)
    scale = 72.0 / dpi

    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        if r.get("level") == "5" and r.get("text", "").strip():
            groups.setdefault((r["block_num"], r["par_num"], r["line_num"]), []).append(r)

    lines, weak = [], []
    for words in groups.values():
        x0 = min(int(w["left"]) for w in words)
        y0 = min(int(w["top"]) for w in words)
        x1 = max(int(w["left"]) + int(w["width"]) for w in words)
        y1 = max(int(w["top"]) + int(w["height"]) for w in words)
        confs = [float(w["conf"]) for w in words if float(w["conf"]) >= 0]
        text = join_cjk(" ".join(w["text"] for w in words))
        # 文字の大きさ: 縦書きは行の幅、横書きは行の高さ
        thick = statistics.median(
            int(w["width"]) if vertical else int(w["height"]) for w in words
        )
        lines.append({
            "text": text,
            "size": round(thick * scale, 1),
            "column_width": round((x1 - x0) * scale, 1),
            "bold": False,
            "font": f"ocr:tesseract:{lang}",
            "bbox": [round(v * scale, 1) for v in (x0, y0, x1, y1)],
            "ocr_conf": round(statistics.mean(confs), 1) if confs else 0.0,
        })
        weak += [join_cjk(w["text"]) for w in words if 0 <= float(w["conf"]) < low_conf]

    if vertical:
        lines.sort(key=lambda l: (-l["bbox"][2], l["bbox"][1]))
    else:
        lines.sort(key=lambda l: (l["bbox"][1], l["bbox"][0]))
    confs = [l["ocr_conf"] for l in lines if l["text"]]
    return {
        "lines": lines,
        "mean_conf": round(statistics.mean(confs), 1) if confs else 0.0,
        "low_conf_words": weak,
        "writing_mode": "vertical" if vertical else "horizontal",
    }


def detect_vertical(page: pymupdf.Page) -> bool:
    """縦書き・横書きの両方でOCRし、平均信頼度と文字数が高い方を採用する。"""
    v = tesseract_page(page, vertical=True, dpi=150)
    h = tesseract_page(page, vertical=False, dpi=150)
    score = lambda r: r["mean_conf"] * min(1.0, sum(len(l["text"]) for l in r["lines"]) / 200)  # noqa: E731
    return score(v) >= score(h)


def ocrmac_page(page: pymupdf.Page, dpi: int = 300) -> dict[str, Any]:
    """macOS Vision による OCR（Mac mini 上で推奨）。"""
    from ocrmac import ocrmac
    from PIL import Image

    pix = page.get_pixmap(dpi=dpi)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    results = ocrmac.OCR(img, language_preference=["ja-JP", "en-US"]).recognize()
    w, h = page.rect.width, page.rect.height
    lines, weak = [], []
    for text, conf, (x, y, bw, bh) in results:
        bbox = [x * w, (1 - y - bh) * h, (x + bw) * w, (1 - y) * h]  # 左下原点の比率→PDF座標
        vertical = bh > bw * 1.5
        lines.append({"text": text.strip(), "size": round((bw * w) if vertical else (bh * h), 1),
                      "column_width": round(bw * w, 1), "bold": False, "font": "ocr:ocrmac",
                      "bbox": [round(v, 1) for v in bbox], "ocr_conf": round(conf * 100, 1)})
        if conf < 0.6:
            weak.append(text)
    n_vert = sum(1 for l in lines if (l["bbox"][3] - l["bbox"][1]) > (l["bbox"][2] - l["bbox"][0]) * 1.5)
    vertical = n_vert > len(lines) / 2
    lines.sort(key=(lambda l: (-l["bbox"][2], l["bbox"][1])) if vertical else (lambda l: (l["bbox"][1], l["bbox"][0])))
    confs = [l["ocr_conf"] for l in lines]
    return {"lines": lines, "mean_conf": round(statistics.mean(confs), 1) if confs else 0.0,
            "low_conf_words": weak, "writing_mode": "vertical" if vertical else "horizontal"}


def render_png(page: pymupdf.Page, dpi: int, clip=None, enhance: bool = False) -> bytes:
    """OCR 用にグレースケールで描画する。enhance なら紙の白飛び・影をならして薄い文字を濃くする
    （スキャン時のムラで端の行が薄く、OCR が読み落とす本のため）。"""
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, clip=clip)
    if not enhance:
        return pix.tobytes("png")
    import numpy as np
    from PIL import Image, ImageFilter

    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
    im = Image.fromarray(a.copy())
    bg = np.asarray(im.filter(ImageFilter.MaxFilter(15)).filter(ImageFilter.GaussianBlur(25)), dtype=float)
    n = np.clip(a / np.maximum(bg, 1) * 255, 0, 255)       # 背景の明るさで割って地を白にそろえる
    n = np.clip((n - 120) / (245 - 120) * 255, 0, 255)     # 薄い文字を濃く
    buf = io.BytesIO()
    Image.fromarray(n.astype(np.uint8)).save(buf, "PNG")
    return buf.getvalue()


# ---------------------------------------------------------------- Google Cloud Vision
VISION_URL = "https://vision.googleapis.com/v1/images:annotate"


def _vision_request(png: bytes, api_key: str, lang_hints: list[str]) -> dict[str, Any]:
    import base64
    import json
    import time
    import urllib.error
    import urllib.request

    body = json.dumps({"requests": [{
        "image": {"content": base64.b64encode(png).decode()},
        "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
        "imageContext": {"languageHints": lang_hints},
    }]}).encode()
    req = urllib.request.Request(f"{VISION_URL}?key={api_key}", data=body,
                                 headers={"Content-Type": "application/json"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                res = json.loads(r.read())["responses"][0]
            if "error" in res:
                raise RuntimeError(f"Vision API エラー: {res['error'].get('message')}")
            return res
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503) and attempt < 3:
                time.sleep(2 ** (attempt + 1))
                continue
            detail = e.read().decode(errors="replace")[:300]
            raise RuntimeError(f"Vision API HTTP {e.code}: {detail}") from None
    raise RuntimeError("Vision API: 再試行の上限に達しました")


def _vision_lines(res: dict[str, Any], scale: float) -> list[dict[str, Any]]:
    """fullTextAnnotation の文字（symbol）を改行情報で行にまとめる。"""
    lines: list[dict[str, Any]] = []
    for page in res.get("fullTextAnnotation", {}).get("pages", []):
        for block in page.get("blocks", []):
            for para in block.get("paragraphs", []):
                cur: list[dict] = []

                def flush():
                    if not cur:
                        return
                    xs = [v.get("x", 0) for s in cur for v in s["boundingBox"]["vertices"]]
                    ys = [v.get("y", 0) for s in cur for v in s["boundingBox"]["vertices"]]
                    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
                    vertical = (y1 - y0) > (x1 - x0) * 1.5
                    thick = statistics.median(
                        (max(v.get("x", 0) for v in s["boundingBox"]["vertices"]) - min(v.get("x", 0) for v in s["boundingBox"]["vertices"]))
                        if vertical else
                        (max(v.get("y", 0) for v in s["boundingBox"]["vertices"]) - min(v.get("y", 0) for v in s["boundingBox"]["vertices"]))
                        for s in cur)
                    text = "".join(s["text"] + (" " if s.get("property", {}).get("detectedBreak", {}).get("type") == "SPACE" else "") for s in cur)
                    confs = [s.get("confidence", 0) for s in cur]
                    lines.append({
                        "text": join_cjk(text),
                        "size": round(thick * scale, 1),
                        "column_width": round((x1 - x0) * scale, 1),
                        "bold": False,
                        "font": "ocr:google",
                        "bbox": [round(v * scale, 1) for v in (x0, y0, x1, y1)],
                        "ocr_conf": round(statistics.mean(confs) * 100, 1),
                        "_vertical": vertical,
                        "_weak": [s["text"] for s in cur if s.get("confidence", 1) < 0.6],
                    })
                    cur.clear()

                for word in para.get("words", []):
                    for sym in word.get("symbols", []):
                        cur.append(sym)
                        brk = sym.get("property", {}).get("detectedBreak", {}).get("type")
                        if brk in ("LINE_BREAK", "EOL_SURE_SPACE"):
                            flush()
                flush()
    return lines


def google_page(page: pymupdf.Page, api_key: str, dpi: int = 300, cache_path=None,
                lang_hints: list[str] | None = None, clip=None, enhance: bool = False) -> dict[str, Any]:
    """Google Cloud Vision の DOCUMENT_TEXT_DETECTION で OCR する。
    応答は cache_path に保存し、再実行時は API を呼ばない（料金の二重払いを防ぐ）。
    clip を渡すとその範囲（見開きの片側など）だけを読む。座標は clip の左上が原点になる。"""
    import json
    from pathlib import Path

    res = None
    if cache_path and Path(cache_path).exists():
        res = json.loads(Path(cache_path).read_text(encoding="utf-8"))
    if res is None:
        res = _vision_request(render_png(page, dpi, clip, enhance), api_key, lang_hints or ["ja"])
        res["_dpi"] = dpi
        if cache_path:
            Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
            Path(cache_path).write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    scale = 72.0 / res.get("_dpi", dpi)
    lines = _vision_lines(res, scale)

    n_vert = sum(1 for l in lines if l["_vertical"] and len(l["text"]) >= 5)
    n_horz = sum(1 for l in lines if not l["_vertical"] and len(l["text"]) >= 5)
    vertical = n_vert > n_horz
    # ルビ（ふりがな）: 本文の行より明らかに細い行。本文から外して記録する
    body = [l["size"] for l in lines if len(l["text"]) >= 10]
    med = statistics.median(body) if body else 0
    weak: list[str] = []
    for l in lines:
        l["is_ruby"] = bool(med) and l["size"] < med * 0.6 and len(l["text"]) <= 8
        weak += l.pop("_weak")
        l.pop("_vertical")
    if vertical:
        lines.sort(key=lambda l: (-l["bbox"][2], l["bbox"][1]))
    else:
        lines.sort(key=lambda l: (l["bbox"][1], l["bbox"][0]))
    confs = [l["ocr_conf"] for l in lines if not l["is_ruby"]]
    return {"lines": lines, "mean_conf": round(statistics.mean(confs), 1) if confs else 0.0,
            "low_conf_words": weak, "writing_mode": "vertical" if vertical else "horizontal"}
