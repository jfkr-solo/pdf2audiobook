"""STEP 6（原文との照合）のテスト。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import manuscript, text_check  # noqa: E402
from app.pdf_extract import extract  # noqa: E402
from app.script_gen import generate, rule_based  # noqa: E402
from app.structure import build  # noqa: E402

SAMPLE = ROOT / "tests" / "fixtures" / "sample.pdf"


def _sec(orig, audio, review=None):
    return {"section_id": "CH001-SEC001", "heading": "h", "source_pages": [1], "original_text": orig,
            "audio_text": audio, "needs_review": review or [],
            "validation": {"status": "pending", "messages": []}, "audio": {"status": "not_started"}}


def test_rule_based_output_passes():
    orig = "一九九七年九月、私は①一〇〇点を取った。DDI(第二電電)の話―そして〝思い"
    v = text_check.check_section(_sec(orig, rule_based(orig)))
    assert v["status"] == "passed" and v["numbers_match"] and v["char_coverage"] >= 0.95


def test_dropped_content_fails_and_missing_number_warns():
    orig = "人生の結果は考え方と熱意と能力の掛け算で決まる。" * 4
    v = text_check.check_section(_sec(orig, "人生の結果は考え方で決まる。"))
    assert v["status"] == "failed" and v["char_coverage"] < 0.85
    v = text_check.check_section(_sec("一九九七年に京都で始めた", "1998年に京都で始めた"))
    assert v["status"] == "warning" and v["numbers_match"] is False
    assert any("1997" in x for x in v["messages"])


def test_low_confidence_japanese_word_warns_but_symbols_do_not():
    review = [{"page": 3, "reason": "OCRで読み取ったページ（平均信頼度 98.0）。低信頼の語 2 個", "text": "―、「"}]
    assert text_check.check_section(_sec("あいう", "あいう", review))["status"] == "passed"
    review = [{"page": 3, "reason": "OCRで読み取ったページ（平均信頼度 98.0）。低信頼の語 1 個", "text": "跌"}]
    v = text_check.check_section(_sec("あいう", "あいう", review))
    assert v["status"] == "warning" and any("p.3「跌」" in x for x in v["messages"])


def test_run_on_sample_manuscript():
    r = extract(str(SAMPLE), {"extract": {"ocr_engine": "none"}})
    r["book_id"] = "BOOK001"
    m = build(r)[0]
    generate(m, {})
    result = text_check.run(m)
    assert result["order"] == []
    assert result["status"].get("failed", 0) == 0
    assert manuscript.validate(m) == []
