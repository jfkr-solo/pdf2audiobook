"""Google Cloud Vision の応答解析のテスト（API は呼ばず、保存済み応答＝キャッシュを使う）。"""
import json
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.ocr import google_page  # noqa: E402


def sym(ch, x, y, w, h, conf=0.99, brk=None):
    s = {"text": ch, "confidence": conf,
         "boundingBox": {"vertices": [{"x": x, "y": y}, {"x": x + w, "y": y}, {"x": x + w, "y": y + h}, {"x": x, "y": y + h}]}}
    if brk:
        s["property"] = {"detectedBreak": {"type": brk}}
    return s


def column(text, x, w=60, conf=0.99):
    syms = [sym(c, x, 200 + i * 62, w, 60, conf) for i, c in enumerate(text)]
    syms[-1]["property"] = {"detectedBreak": {"type": "LINE_BREAK"}}
    return {"words": [{"symbols": syms}]}


def fake_response():
    paras = [
        column("魂というものは、生き方次第で磨かれる。", 2000),   # 右の行（先に読む）
        column("才覚が人並みはずれたものであればあるほど", 1880),
        column("らしんばん", 1950, w=25),                         # ルビ（細い）
        column("羅針盤が必要となります。", 1760, conf=0.4),       # 低信頼
    ]
    return {"fullTextAnnotation": {"pages": [{"blocks": [{"paragraphs": paras}]}]}, "_dpi": 300}


def test_google_vertical_lines_ruby_and_confidence(tmp_path):
    cache = tmp_path / "p0001.json"
    cache.write_text(json.dumps(fake_response(), ensure_ascii=False), encoding="utf-8")
    page = pymupdf.open().new_page(width=595, height=842)
    r = google_page(page, api_key="unused", cache_path=cache)  # キャッシュがあれば API を呼ばない
    assert r["writing_mode"] == "vertical"
    texts = [l["text"] for l in r["lines"] if not l["is_ruby"]]
    assert texts == ["魂というものは、生き方次第で磨かれる。", "才覚が人並みはずれたものであればあるほど", "羅針盤が必要となります。"]
    assert [l["text"] for l in r["lines"] if l["is_ruby"]] == ["らしんばん"]
    assert "羅" in r["low_conf_words"]
    assert r["lines"][0]["bbox"][0] == round(2000 * 72 / 300, 1)
