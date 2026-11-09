"""STEP 5（読み方辞書）のテスト。"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import reading_dict  # noqa: E402
from app.script_gen import generate  # noqa: E402


def test_apply_respects_word_boundaries_and_longest_first():
    entries = [{"surface": "DDI", "reading": "ディーディーアイ"},
               {"surface": "KDDI", "reading": "ケーディーディーアイ"},
               {"surface": "TCP/IP", "reading": "ティーシーピー・アイピー"},
               {"surface": "IP", "reading": "アイピー"}]
    assert reading_dict.apply("DDIは現在のKDDIです", entries) == "ディーディーアイは現在のケーディーディーアイです"
    assert reading_dict.apply("TCP/IPとIP", entries) == "ティーシーピー・アイピーとアイピー"
    assert reading_dict.apply("ZIPファイル", entries) == "ZIPファイル"  # 語の一部は置き換えない


def test_book_dictionary_overrides_common(tmp_path):
    common = tmp_path / "common.json"
    book = tmp_path / "book.json"
    common.write_text(json.dumps({"entries": [{"surface": "SQL", "reading": "エスキューエル"}]}), encoding="utf-8")
    book.write_text(json.dumps({"entries": [{"surface": "SQL", "reading": "シークェル"}]}), encoding="utf-8")
    entries = reading_dict.load([common, book, tmp_path / "missing.json"])
    assert reading_dict.apply("SQL", entries) == "シークェル"


def test_spell_out_only_acronyms():
    assert reading_dict.spell_out("GE") == "ジーイー"
    assert reading_dict.spell_out("IPv4") is None  # 大文字小文字の混ざった語は推測しない
    assert reading_dict.spell_out("au") == "エーユー"
    assert reading_dict.spell_out("Kyocera") is None


def test_suggest_adds_auto_entries_without_touching_existing(tmp_path):
    book = tmp_path / "dictionary.json"
    book.write_text(json.dumps({"entries": [{"surface": "DDI", "reading": "ディーディーアイ"}]}), encoding="utf-8")
    added = reading_dict.suggest(book, Counter({"DDI": 3, "IDO": 2, "Kyocera": 1}))
    assert [e["surface"] for e in added] == ["IDO", "Kyocera"]
    doc = json.loads(book.read_text(encoding="utf-8"))
    kyo = next(e for e in doc["entries"] if e["surface"] == "Kyocera")
    assert kyo["auto"] and kyo["reading"] == "" and "note" in kyo
    # 読みが空の候補は適用されない
    assert reading_dict.apply("Kyocera", reading_dict.load([book])) == "Kyocera"


def test_generate_applies_dictionary():
    m = {"chapters": [{"sections": [{"section_id": "S1", "heading": "h", "original_text": "GEの話",
                                     "audio_text": "", "validation": {"status": "pending", "messages": []},
                                     "audio": {"status": "done"}}]}]}
    generate(m, {}, dictionary=[{"surface": "GE", "reading": "ジーイー"}])
    s = m["chapters"][0]["sections"][0]
    assert s["audio_text"] == "ジーイーの話"
    assert s["audio"]["status"] == "stale"
