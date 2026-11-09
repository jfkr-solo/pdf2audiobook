"""STEP 9（音声の品質確認）のテスト。音声は正弦波（声の代わり）と無音で作る。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from app import audio_check  # noqa: E402
from app.tts import base, synth  # noqa: E402

RATE = 24000


def _tone(sec, amp=8000):
    t = np.arange(int(RATE * sec)) / RATE
    return (amp * np.sin(2 * np.pi * 220 * t)).astype("<i2")


def _quiet(sec):
    return np.zeros(int(RATE * sec), dtype="<i2")


def _speech(sentences, gap=0.4, cut=False):
    """1文 = 1秒の音 + 末尾に少し無音。cut=True なら最後の文を音の途中で切る。"""
    parts = []
    for i in range(sentences):
        parts += [_tone(1.0), _quiet(0.05 if cut and i == sentences - 1 else gap)]
    if cut:
        parts[-1] = _tone(0.1)
    return np.concatenate(parts)


def _book(tmp_path, signals, texts):
    secs = []
    for i, (x, t) in enumerate(zip(signals, texts), 1):
        rel = f"cache/tts/S{i}.wav"
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(base.make_wav(x.tobytes(), RATE))
        secs.append({"section_id": f"CH001-SEC{i:03d}", "heading": f"h{i}", "audio_text": t,
                     "audio": {"status": "done", "file": rel, "text_hash": "x"}})
    return {"title": "t", "chapters": [{"chapter_id": "CH001", "chapter_title": "c", "sections": secs}]}


def test_analyze_finds_long_silence_and_cut_tail():
    st = audio_check.DEFAULTS
    r = audio_check.analyze(np.concatenate([_tone(1), _quiet(4), _tone(1), _quiet(0.3)]).astype(float), RATE, st)
    assert 3.9 <= r["longest_silence"] <= 4.1 and r["tail_ratio"] == 0
    r = audio_check.analyze(_speech(3, cut=True).astype(float), RATE, st)
    assert r["tail_ratio"] > 0.3
    r = audio_check.analyze(_quiet(2).astype(float), RATE, st)
    assert not r["voiced"]


def test_check_sections_flags_by_rate_and_content(tmp_path):
    ten = "あいうえおかきくけこ" * 2  # 20字 / 1.4秒 ≒ 1文
    normal = [_speech(5) for _ in range(4)]
    signals = normal + [_speech(2), np.concatenate([_tone(1), _quiet(5), _tone(4)]), _speech(5, cut=True), _quiet(3)]
    texts = [ten * 5] * 4 + [ten * 5, ten * 5, ten * 5, ten]
    m = _book(tmp_path, signals, texts)
    rows = {r["section_id"]: r for r in audio_check.check_sections(m, tmp_path, audio_check.DEFAULTS)}
    assert all(rows[f"CH001-SEC{i:03d}"]["status"] == "passed" for i in range(1, 5))
    assert rows["CH001-SEC005"]["status"] == "failed" and "速すぎ" in rows["CH001-SEC005"]["messages"][0]
    assert rows["CH001-SEC006"]["status"] == "warning" and any("無音" in x for x in rows["CH001-SEC006"]["messages"])
    assert rows["CH001-SEC007"]["status"] == "warning" and any("途切れ" in x for x in rows["CH001-SEC007"]["messages"])
    assert rows["CH001-SEC008"]["status"] == "failed" and "無音しかありません" in rows["CH001-SEC008"]["messages"]
    md = audio_check.report_markdown(m, list(rows.values()), [])
    assert "要作り直し 2" in md and "CH001-SEC005" in md


def test_missing_file_and_changed_text(tmp_path):
    m = _book(tmp_path, [_speech(3)] * 2, ["あいう。"] * 2)
    (tmp_path / "cache/tts/S1.wav").unlink()
    voice = {"engine": "fake", "sample_rate": RATE}
    m["chapters"][0]["sections"][1]["audio"]["text_hash"] = synth.text_hash("前の文。", voice)
    rows = audio_check.check_sections(m, tmp_path, audio_check.DEFAULTS, voice)
    assert rows[0]["status"] == "failed" and "ファイルがありません" in rows[0]["messages"][0]
    assert rows[1]["status"] == "failed" and "変わっています" in rows[1]["messages"][0]


def test_stale_sections_are_regenerated_by_tts(tmp_path):
    class Eng(base.TTSEngine):
        calls = 0

        def available(self):
            return True, ""

        def synthesize(self, text, settings):
            Eng.calls += 1
            return base.make_wav(_speech(1).tobytes(), RATE)

    voice = {"engine": "fake", "sample_rate": RATE, "sentence_pause_ms": 400}
    m = _book(tmp_path, [_speech(1)] * 2, ["一つ目。", "二つ目。"])
    for s in m["chapters"][0]["sections"]:
        s["validation"] = {"status": "passed", "messages": []}
        s["audio"]["text_hash"] = synth.text_hash(s["audio_text"], voice)
    assert synth.run(m, tmp_path, Eng(), voice)["reused"] == 2
    assert audio_check.mark_stale(m, {"CH001-SEC002"}) == 1
    st = synth.run(m, tmp_path, Eng(), voice)
    assert (st["done"], st["reused"]) == (1, 1) and m["chapters"][0]["sections"][1]["audio"]["status"] == "done"


def test_check_files_reports_missing_mp3_and_newer_sections(tmp_path):
    m = _book(tmp_path, [_speech(1)], ["あ。"])
    m["chapters"][0]["audio"] = {"status": "done", "file": "audio/x.mp3", "duration_sec": 1.0, "generated_at": "2026-01-01T00:00:00+00:00"}
    m["chapters"][0]["sections"][0]["audio"]["generated_at"] = "2026-02-01T00:00:00+00:00"
    notes = audio_check.check_files(m, tmp_path)
    assert any("ファイルがありません" in n for n in notes) and any("全章結合版" in n for n in notes)
    assert any("新しい節" in n for n in notes)
