"""STEP 8（章ごとの MP3 と全章結合版）のテスト。音声は短い無音で代用する。"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from app import audio  # noqa: E402
from app.tts import base  # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg がありません")
RATE = 24000


class FakeEngine(base.TTSEngine):
    name = "fake"

    def __init__(self):
        self.calls = []

    def available(self):
        return True, ""

    def synthesize(self, text, settings):
        self.calls.append(text)
        return base.make_wav(base.silence(500, RATE), RATE)


def _book(tmp_path, sections_per_chapter=(2, 1)):
    chapters = []
    for ci, n in enumerate(sections_per_chapter):
        secs = []
        for si in range(n):
            sid = f"CH{ci + 1:03d}-SEC{si + 1:03d}"
            rel = f"cache/tts/{sid}_x.wav"
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / rel).write_bytes(base.make_wav(base.silence(1000, RATE), RATE))
            secs.append({"section_id": sid, "heading": f"見出し{si + 1}", "audio": {"status": "done", "file": rel}})
        chapters.append({"chapter_id": f"CH{ci + 1:03d}", "chapter_number": ci, "chapter_title": ["プロローグ", "思いを実現させる"][ci],
                         "sections": secs, "audio": {"status": "not_started"}})
    return {"book_id": "BOOK999", "title": "テスト本", "author": "著者", "chapters": chapters}


ST = {"section_pause_ms": 500, "chapter_pause_ms": 1000, "heading_pause_ms": 250, "read_headings": True,
      "bitrate": "64k", "loudnorm": "", "sample_rate": RATE}


def _probe(f):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:format_tags", "-of", "json", str(f)],
                       capture_output=True, text=True)
    return json.loads(r.stdout)["format"]


def test_labels_and_file_names():
    assert audio.chapter_label({"chapter_title": "思いを実現させる", "chapter_number": 1}) == "第1章 思いを実現させる"
    assert audio.chapter_label({"chapter_title": "あとがき", "chapter_number": 6}) == "あとがき"
    assert audio.chapter_label({"chapter_title": "プロローグ", "chapter_number": 0}) == "プロローグ"
    assert audio.file_name({"chapter_title": "A/B", "chapter_number": 2}, 5) == "02_第2章 A B.mp3"


def test_build_chapters_and_whole(tmp_path):
    m, eng = _book(tmp_path), FakeEngine()
    st = audio.Builder(m, tmp_path, ST, eng, {"sample_rate": RATE}).run()
    assert (st["built"], st["failed"], st["whole"]) == (2, 0, "built")
    a1 = m["chapters"][0]["audio"]
    # プロローグ: 章タイトル0.5 + 0.5 + 見出し0.5 + 0.25 + 本文1 + 節間0.5 + 見出し0.5 + 0.25 + 本文1 = 5.0秒
    assert a1["file"] == "audio/00_プロローグ.mp3" and a1["duration_sec"] == pytest.approx(5.0)
    assert m["chapters"][1]["audio"]["file"] == "audio/01_第1章 思いを実現させる.mp3"
    assert "第1章、思いを実現させる" in eng.calls or "第1章 思いを実現させる" in eng.calls
    f = _probe(tmp_path / a1["file"])
    assert float(f["duration"]) == pytest.approx(5.0, abs=0.15)
    assert f["tags"]["album"] == "テスト本" and f["tags"]["track"] == "1/2" and "BOOK999 CH001" in f["tags"]["comment"]
    whole = _probe(tmp_path / "audio" / audio.WHOLE_NAME)
    assert float(whole["duration"]) == pytest.approx(m["audio"]["duration_sec"], abs=0.2)
    assert not list((tmp_path / "cache" / "build").glob("*.wav"))  # 中間の WAV は残さない


def test_rebuild_only_changed_chapter(tmp_path):
    m, eng = _book(tmp_path), FakeEngine()
    b = audio.Builder(m, tmp_path, ST, eng, {"sample_rate": RATE})
    b.run()
    n = len(eng.calls)
    assert b.run() == {"built": 0, "reused": 2, "failed": 0, "missing": [], "whole": "reused"}
    assert len(eng.calls) == n  # 見出しの音声も使い回す
    s = m["chapters"][1]["sections"][0]
    s["audio"]["file"] = "cache/tts/CH002-SEC001_y.wav"
    (tmp_path / s["audio"]["file"]).write_bytes(base.make_wav(base.silence(2000, RATE), RATE))
    st = b.run()
    assert (st["built"], st["reused"], st["whole"]) == (1, 1, "built")


def test_missing_section_fails_chapter_unless_allowed(tmp_path):
    m = _book(tmp_path)
    m["chapters"][0]["sections"][1]["audio"] = {"status": "failed"}
    b = audio.Builder(m, tmp_path, dict(ST, read_headings=False))
    st = b.run()
    assert st["failed"] == 1 and st["whole"] == "failed" and st["missing"] == ["CH001-SEC002"]
    assert "CH001-SEC002" in m["chapters"][0]["audio"]["error"]
    st = b.run(allow_missing=True)
    assert st["failed"] == 0 and st["whole"] == "built"
    assert m["chapters"][0]["audio"]["duration_sec"] == pytest.approx(1.0)  # 見出しを読まない設定
    assert "audio" in audio.report_markdown(m) or "00_プロローグ.mp3" in audio.report_markdown(m)
