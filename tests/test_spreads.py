"""見開きスキャンの分割と、柱の数（書名の数字）をノンブルとみなさない処理のテスト（API は呼ばない）。"""
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.ocr import render_png  # noqa: E402
from app.pdf_extract import _spread_clips  # noqa: E402


def _doc(w, h):
    doc = pymupdf.open()
    doc.new_page(width=w, height=h)
    return doc


def test_landscape_page_is_split_right_first():
    page = _doc(842, 595)[0]
    clips = _spread_clips(page, "auto", rtl=True)
    assert [s for s, _ in clips] == ["R", "L"]
    assert clips[0][1].x0 == 421 and clips[1][1].x1 == 421
    assert [s for s, _ in _spread_clips(page, "auto", rtl=False)] == ["L", "R"]


def test_portrait_or_disabled_is_not_split():
    assert _spread_clips(_doc(421, 595)[0], "auto", rtl=True) == [("", None)]
    assert _spread_clips(_doc(842, 595)[0], False, rtl=True) == [("", None)]


def test_enhance_renders_png():
    page = _doc(200, 200)[0]
    page.insert_text((20, 100), "abc")
    png = render_png(page, 72, enhance=True)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------- 小説（日付見出し・会話の段落・場面転換）
from app.script_gen import rule_based  # noqa: E402
from app.structure import build  # noqa: E402


def _line(text, x, y0=60, y1=None, size=13.0):
    return {"text": text, "size": size, "bbox": [x, y0, x + 14, y1 or (60 + 13 * len(text))], "ocr_conf": 99}


def test_chapter_pattern_and_dialogue_paragraphs():
    lines = [
        _line("2017年11月10日(金)", 380, y0=140, y1=300, size=14),
        _line("風が吹いた。大輔は海を見つめていた。風が吹いた。大輔は海を", 350, y1=540),
        _line("見つめていた。風が吹いた。大輔は海を見つめていた。風が吹い", 340, y1=540),
        _line("た。大輔は海を見つめていた。風が吹いた。大輔は海を見つめて", 330, y1=540),
        _line("いた。", 320, y1=100),
        _line("「大輔さーん!」", 310, y1=164),
        _line("真奈美が手を振る。真奈美が手を振る。真奈美が手を振る。真奈", 300, y1=540),
    ]
    page = {"page": 1, "method": "ocr:google", "writing_mode": "vertical", "lines": lines,
            "figures": [], "needs_review": [], "text": "", "running_head": None, "printed_page": None}
    m, _ = build({"book_id": "B", "title": "本", "source_pdf": "x.pdf", "page_count": 1,
                  "writing_mode": "vertical", "pages": [page]},
                 chapter_pattern=r"20\d\d年\d+月\d+日\(.\)")
    assert [c["chapter_title"] for c in m["chapters"]] == ["2017年11月10日(金)"]
    paras = m["chapters"][0]["sections"][0]["original_text"].split("\n")
    assert paras[1] == "「大輔さーん!」"


def test_novel_symbols():
    t = rule_based("平均すると11・5回になる\n＊\n「じゃあさ••••••」\n☆☆☆明日には、君へ。\n「♪ハッピー」〈日記〉")
    assert "11.5回" in t
    assert "＊" not in t and "*" not in t and "☆" not in t and "♪" not in t and "•" not in t
    assert "「日記」" in t
    assert "\n明日には" in t


def test_apply_ocr_fixes():
    sys.path.insert(0, str(ROOT / "scripts"))
    from apply_ocr_fixes import apply
    doc = {"pages": [{"page": 3, "lines": [{"text": "朝のルート8を行く。"}, {"text": "A"}], "text": ""}]}
    assert apply(doc, [{"page": 3, "find": "ルート8を", "replace": "ルート83を"},
                       {"page": 3, "find": "A", "replace": "", "whole_line": True}]) == []
    assert doc["pages"][0]["text"] == "朝のルート83を行く。\n"
    assert apply(doc, [{"page": 3, "find": "ない文字", "replace": "x"}])
