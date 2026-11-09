"""STEP 1（設定・スキーマ・DB）と STEP 2（PDF抽出）のテスト。

    pytest -q
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db, manuscript  # noqa: E402
from app.config import load_config  # noqa: E402
from app.pdf_extract import extract  # noqa: E402

SAMPLE = ROOT / "tests" / "fixtures" / "sample.pdf"


@pytest.fixture(scope="session")
def sample_pdf():
    if not SAMPLE.exists():
        subprocess.run([sys.executable, str(ROOT / "scripts" / "make_sample_pdf.py"), str(SAMPLE)], check=True)
    return SAMPLE


@pytest.fixture(scope="session")
def no_ocr(sample_pdf):
    return extract(str(sample_pdf), {"extract": {"ocr_engine": "none"}})


# ---------------- STEP 1
def test_config_loads():
    cfg = load_config()
    assert cfg["tts"]["engine"] in {"openjtalk", "macos_say", "voicevox", "google"}
    assert cfg["extract"]["ocr_min_chars"] > 0


def minimal_manuscript():
    return {
        "schema_version": "1.0", "book_id": "BOOK001", "title": "IT基礎テキスト", "language": "ja-JP",
        "chapters": [{
            "chapter_id": "CH001", "chapter_number": 1, "chapter_title": "第1章 ネットワークの基礎",
            "sections": [{
                "section_id": "CH001-SEC001", "heading": "ネットワークとは", "source_pages": [2],
                "original_text": "原文", "audio_text": "読み上げ文",
                "supplement_text": {"text": "", "ai_generated": True},
                "validation": {"status": "pending", "char_coverage": None, "numbers_match": None, "messages": []},
                "audio": {"status": "not_started"},
            }],
            "summary": {"text": "要約", "ai_generated": True, "key_terms": ["TCP/IP"]},
        }],
    }


def test_schema_accepts_valid():
    assert manuscript.validate(minimal_manuscript()) == []


def test_schema_rejects_invalid():
    doc = minimal_manuscript()
    doc["book_id"] = "X1"
    del doc["chapters"][0]["sections"][0]["original_text"]
    doc["chapters"][0]["sections"][0]["validation"]["status"] = "ok"
    errors = manuscript.validate(doc)
    assert len(errors) == 3


def test_save_refuses_invalid(tmp_path):
    doc = minimal_manuscript()
    doc["language"] = "japanese"
    with pytest.raises(ValueError):
        manuscript.save(doc, tmp_path / "m.json")
    assert not (tmp_path / "m.json").exists()


def test_db_ids_and_jobs(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    assert db.next_book_id(conn) == "BOOK001"
    db.upsert_book(conn, "BOOK001", "t", "x.pdf", 5)
    assert db.next_book_id(conn) == "BOOK002"
    j = db.start_job(conn, "BOOK001", "extract")
    db.finish_job(conn, j, "failed", "boom")
    row = conn.execute("SELECT status, message FROM jobs WHERE id=?", (j,)).fetchone()
    assert tuple(row) == ("failed", "boom")


# ---------------- STEP 2
def test_metadata_and_toc(no_ocr):
    assert no_ocr["page_count"] == 5
    assert no_ocr["title"] == "IT基礎テキスト"
    assert no_ocr["author"] == "サンプル著者"
    assert [t["title"] for t in no_ocr["toc"] if t["level"] == 1] == ["第1章 ネットワークの基礎", "第2章 データベースの基礎"]


def test_header_footer_removed(no_ocr):
    p2 = no_ocr["pages"][1]
    assert "IT基礎テキスト" not in p2["text"]
    assert "- 2 -" not in p2["text"]
    assert {r["text"] for r in p2["removed"]} == {"IT基礎テキスト", "- 2 -"}
    # 表紙の書名は本文として残る（ヘッダー帯の外にあるため）
    assert "IT基礎テキスト" in no_ocr["pages"][0]["text"]


def test_body_text_is_complete(no_ocr):
    text = no_ocr["pages"][1]["text"]
    for s in ["TCP/IPは、インターネットにおける通信プロトコルの標準である。",
              "約43億個", "4,294,967,296", "・LANは限られた範囲のネットワーク"]:
        assert s in text


def test_headings_keep_font_size(no_ocr):
    lines = no_ocr["pages"][1]["lines"]
    chap = next(l for l in lines if l["text"].startswith("第1章"))
    body = next(l for l in lines if l["text"].startswith("ネットワークとは、"))
    assert chap["size"] > body["size"]
    assert no_ocr["body_font_size"] == round(body["size"])


def test_table_and_formula_detected(no_ocr):
    figs = no_ocr["pages"][1]["figures"]
    table = next(f for f in figs if f["kind"] == "table")
    assert table["rows"] == [["種類", "ビット数"], ["IPv4", "32ビット"], ["IPv6", "128ビット"]]
    assert any(f["kind"] == "formula" and "2^32" in f["text"] for f in figs)
    # 表のセル文字は本文テキストに重複して入らない
    assert "128ビット" not in no_ocr["pages"][1]["text"]


def test_scan_page_flagged_without_ocr(no_ocr):
    p5 = no_ocr["pages"][4]
    assert p5["method"] == "needs_ocr"
    assert p5["text"] == ""
    assert any("OCR" in r["reason"] for r in p5["needs_review"])


@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract 未インストール")
def test_scan_page_ocr_tesseract(sample_pdf):
    r = extract(str(sample_pdf), {"extract": {"ocr_engine": "tesseract", "ocr_lang": "jpn"}})
    p5 = r["pages"][4]
    assert p5["method"] == "ocr:tesseract"
    compact = p5["text"].replace(" ", "")
    assert "正規化" in compact


def test_cli_extract(tmp_path, sample_pdf):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"data_dir: {tmp_path}/data\ndb_path: {tmp_path}/data/a.db\nextract:\n  ocr_engine: none\n", encoding="utf-8")
    out = subprocess.run([sys.executable, "-m", "app.cli", "--config", str(cfg), "extract", str(sample_pdf)],
                         cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    pages = json.loads((tmp_path / "data" / "BOOK001" / "pages.json").read_text(encoding="utf-8"))
    assert pages["book_id"] == "BOOK001"
    assert (tmp_path / "data" / "BOOK001" / "review.md").exists()
