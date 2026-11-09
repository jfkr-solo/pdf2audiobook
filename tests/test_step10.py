"""STEP 10（Web 画面）のテスト。"""
import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from app.web.main import create_app  # noqa: E402


@pytest.fixture()
def book(tmp_path):
    bdir = tmp_path / "data" / "BOOK001"
    (bdir / "audio").mkdir(parents=True)
    (bdir / "audio" / "01_第1章 思い.mp3").write_bytes(bytes(range(256)) * 4)
    (tmp_path / "secret.mp3").write_bytes(b"x")
    sec = {"section_id": "CH001-SEC001", "heading": "h", "validation": {"status": "passed"}, "audio": {"status": "done"}}
    m = {"book_id": "BOOK001", "title": "テスト本", "author": "著者", "voice_settings": {"engine": "openjtalk"},
         "chapters": [
             {"chapter_id": "CH001", "chapter_number": 1, "chapter_title": "思い", "sections": [sec],
              "audio": {"status": "done", "file": "audio/01_第1章 思い.mp3", "duration_sec": 3725}},
             {"chapter_id": "CH002", "chapter_number": 2, "chapter_title": "まだ", "sections": [sec], "audio": {"status": "not_started"}}]}
    (bdir / "manuscript.json").write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    return {"_root": str(tmp_path), "data_dir": str(tmp_path / "data"), "db_path": "data/none.db"}


def test_pages(book):
    c = TestClient(create_app(book))
    r = c.get("/")
    assert r.status_code == 200 and "テスト本" in r.text and "1章" in r.text and "1時間02分" in r.text
    assert "gen-title" in r.text and "続きを聞く" in r.text  # 表紙画像が無いときは書名から作った表紙
    r = c.get("/books/BOOK001")
    assert r.status_code == 200 and "第1章 思い" in r.text and "音声なし" in r.text
    assert 'data-file="01_第1章 思い.mp3"' in r.text
    r = c.get("/books/BOOK001/status")
    assert r.status_code == 200 and "問題なし 2" in r.text and "1 / 2 章" in r.text
    assert c.get("/books/BOOK999").status_code == 404 and c.get("/books/..%2F..").status_code == 404


def test_cover_image_when_present(book, tmp_path):
    c = TestClient(create_app(book))
    assert c.get("/cover/BOOK001").status_code == 404
    (tmp_path / "data" / "BOOK001" / "cover.jpg").write_bytes(b"\xff\xd8jpg")
    assert c.get("/cover/BOOK001").content == b"\xff\xd8jpg"
    assert 'src="/cover/BOOK001"' in c.get("/").text


def test_media_supports_range_and_blocks_traversal(book):
    c = TestClient(create_app(book))
    r = c.get("/media/BOOK001/01_第1章 思い.mp3", headers={"Range": "bytes=10-19"})
    assert r.status_code == 206 and r.content == bytes(range(10, 20))
    assert c.get("/media/BOOK001/..%2Fmanuscript.json").status_code == 404
    assert c.get("/media/BOOK001/..%2F..%2F..%2Fsecret.mp3").status_code == 404


def test_password(book, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "pw")
    c = TestClient(create_app(book))
    assert c.get("/").status_code == 401 and "WWW-Authenticate" in c.get("/").headers
    good = {"Authorization": "Basic " + base64.b64encode(b"koki:pw").decode()}
    bad = {"Authorization": "Basic " + base64.b64encode(b"koki:no").decode()}
    assert c.get("/", headers=good).status_code == 200 and c.get("/", headers=bad).status_code == 401
    assert c.get("/healthz").status_code == 200


def test_rss_feed_and_token(book, monkeypatch):
    import xml.etree.ElementTree as ET
    monkeypatch.setenv("APP_PASSWORD", "pw")
    monkeypatch.setenv("APP_BASE_URL", "https://example.test")
    from app.web.main import feed_token
    t = feed_token("pw")
    c = TestClient(create_app(book))
    assert c.get("/feed/BOOK001.xml").status_code == 401
    assert c.get("/feed/BOOK001.xml?t=wrong").status_code == 401
    r = c.get(f"/feed/BOOK001.xml?t={t}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/rss+xml")
    ch = ET.fromstring(r.content).find("channel")
    items = ch.findall("item")
    assert ch.findtext("title") == "テスト本" and len(items) == 1  # 音声の無い章は載せない
    assert items[0].findtext("title") == "第1章 思い"
    enc = items[0].find("enclosure")
    assert enc.get("url").startswith("https://example.test/media/BOOK001/") and enc.get("url").endswith(f"?t={t}")
    assert enc.get("length") == "1024"
    path = enc.get("url").replace("https://example.test", "")
    assert c.get(path).status_code == 200  # 合言葉付きの URL なら音声も取れる
    assert c.get("/books/BOOK001?t=" + t).status_code == 401  # 合言葉は RSS と音声だけ
    good = {"Authorization": "Basic " + base64.b64encode(b"koki:pw").decode()}
    assert f"/feed/BOOK001.xml?t={t}" in c.get("/books/BOOK001/status", headers=good).text
