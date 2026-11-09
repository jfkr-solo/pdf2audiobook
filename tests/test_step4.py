"""STEP 4（読み上げ文の生成）のテスト。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import manuscript  # noqa: E402
from app.pdf_extract import extract  # noqa: E402
from app.script_gen import generate, kanji_numbers_to_arabic, rule_based  # noqa: E402
from app.structure import build  # noqa: E402

SAMPLE = ROOT / "tests" / "fixtures" / "sample.pdf"


def test_kanji_numbers():
    assert kanji_numbers_to_arabic("一九九七年九月") == "1997年九月"
    assert kanji_numbers_to_arabic("一〇〇点から二七〇〇点") == "100点から2700点"
    # 語として使われる漢数字は変えない
    for w in ("四六時中", "二三日", "七五三", "一人ひとり", "一生懸命"):
        assert kanji_numbers_to_arabic(w) == w


def test_rule_based_symbols():
    assert rule_based("結果=考え方×熱意×能力") == "結果イコール考え方かける熱意かける能力"
    assert rule_based("人格―心魂の品格―が") == "人格、心魂の品格、が"
    assert rule_based("〝結核家系\"でした") == "「結核家系」でした"
    assert rule_based("まじめ、正直･･････そういう") == "まじめ、正直、そういう"
    assert rule_based("①火を近づける") == "一つ目、火を近づける"
    assert rule_based("アメリカのGE(ゼネラル・エレクトリック)社") == "アメリカのGE、ゼネラル・エレクトリック、社"
    assert rule_based("〇点から一〇〇点") == "0点から100点"
    assert rule_based("ok|です") == "okです"


def _manuscript():
    r = extract(str(SAMPLE), {"extract": {"ocr_engine": "none"}})
    r["book_id"] = "BOOK001"
    return build(r)[0]


def test_generate_fills_audio_text_and_stays_valid():
    m = _manuscript()
    stats = generate(m, {})
    secs = [s for c in m["chapters"] for s in c["sections"]]
    assert stats["sections"] == len(secs)
    assert all(s["audio_text"] for s in secs if s["original_text"])
    assert manuscript.validate(m) == []


def test_llm_output_rejected_when_content_dropped():
    m = _manuscript()
    calls = []

    def summarizing(heading, text):  # 内容を大きく削る＝不採用になるべき
        calls.append(heading)
        return text[:10]

    stats = generate(m, {}, summarizing)
    assert calls and stats["llm"] == 0 and stats["llm_rejected"] == len(calls)
    s = m["chapters"][0]["sections"][0]
    assert s["audio_text"] == rule_based(s["original_text"])
    assert any("採用せず" in x for x in s["validation"]["messages"])


def test_llm_output_accepted_and_audio_marked_stale():
    m = _manuscript()
    s = m["chapters"][0]["sections"][0]
    s["audio"]["status"] = "done"
    stats = generate(m, {}, lambda h, t: t + "。")
    assert stats["llm"] == stats["sections"] - stats["empty"]
    assert s["audio"]["status"] == "stale"
    # 2回目は文章が変わらないので messages が重複しない
    generate(m, {}, lambda h, t: t + "。")
    assert not any(x.startswith("script:") for x in s["validation"]["messages"])
