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
                   low_conf: float = 60.0) -> dict[str, Any]:
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
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
