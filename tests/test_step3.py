"""STEP 3（章・節の構造化）のテスト。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import manuscript  # noqa: E402
from app.pdf_extract import extract, printed_page_number  # noqa: E402
from app.structure import build, order_pages  # noqa: E402

SAMPLE = ROOT / "tests" / "fixtures" / "sample.pdf"


def _built():
    r = extract(str(SAMPLE), {"extract": {"ocr_engine": "none"}})
    r["book_id"] = "BOOK001"
    return build(r)


def test_chapters_and_sections_from_toc():
    m, notes = _built()
    assert manuscript.validate(m) == []
    assert [c["chapter_title"] for c in m["chapters"]] == ["第1章 ネットワークの基礎", "第2章 データベースの基礎"]
    assert [c["chapter_number"] for c in m["chapters"]] == [1, 2]
    assert [s["heading"] for s in m["chapters"][0]["sections"]] == ["1.1 ネットワークとは", "1.2 IPアドレス", "1.3 DNS"]
    assert any("表紙" in n for n in notes)


def test_paragraphs_and_bullets_kept():
    m, _ = _built()
    sec = m["chapters"][0]["sections"][0]
    assert sec["source_pages"] == [2]
    assert sec["original_text"].split("\n") == [
        "ネットワークとは、複数のコンピュータを接続し、データをやり取りする仕組みである。TCP/IPは、インターネットにおける通信プロトコルの標準である。",
        "・LANは限られた範囲のネットワーク",
        "・WANは広い範囲を結ぶネットワーク",
    ]
    assert sec["audio_text"] == ""  # STEP 4 で作る


def test_figures_and_review_attached_to_sections():
    m, _ = _built()
    sec = m["chapters"][0]["sections"][1]
    kinds = {f["kind"] for f in sec["figures"]}
    assert {"table", "formula"} <= kinds
    last = m["chapters"][1]["sections"][-1]
    assert any("OCR" in r["reason"] for r in last["needs_review"])


def test_kanji_page_numbers():
    assert printed_page_number("〇一七プロローグ") == 17
    assert printed_page_number("〇一五。プロローグ") == 15
    assert printed_page_number("プロローグ 〇二〇") == 20
    assert printed_page_number("Oゐ一三プロローグ") is None  # 読めないものは推測しない


def test_page_reorder_with_swapped_scan():
    # スキャンで 14 と 15 が入れ替わった並び。一部のノンブルは読めない
    nums = [13, 15, 14, 17, None, 19, 18, 21, 20]
    pages = [{"page": i + 1, "printed_page": n, "lines": []} for i, n in enumerate(nums)]
    ordered, notes = order_pages(pages)
    assert [p["book_page"] for p in ordered] == [13, 14, 15, 16, 17, 18, 19, 20, 21]
    assert [p["page"] for p in ordered] == [1, 3, 2, 5, 4, 7, 6, 9, 8]
    assert any("入れ替え" in n for n in notes)


def test_vertical_heading_detected_by_position():
    """Google Vision では見出しと本文の文字サイズ差が小さい。天にはみ出した行を見出しとみなす。"""
    from app.structure import _is_heading
    body = 15.8
    base = 140.0  # 本文の行頭（上端）
    heading = {"text": "単純な原理原則が", "size": 18.5, "bbox": [397.9, 86.2, 416.9, 231.6], "ocr_conf": 99}
    second = {"text": "道しるべとなる「生きた哲学」", "size": 18.0, "bbox": [380.0, 80.0, 398.0, 300.0], "ocr_conf": 99}
    body_line = {"text": "不祥事を起こす人がいます", "size": 17.0, "bbox": [226.6, 140.6, 243.8, 336.7], "ocr_conf": 99}
    sentence = {"text": "惧しているのです。", "size": 18.7, "bbox": [226.6, 80.0, 243.8, 336.7], "ocr_conf": 99}
    assert _is_heading(heading, body, True, base, True)
    assert _is_heading(second, body, True, base, True)
    assert not _is_heading(body_line, body, True, base, True)
    assert not _is_heading(sentence, body, True, base, True)
