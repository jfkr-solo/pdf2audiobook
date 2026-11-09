"""STEP 7（音声合成）のテスト。実際の TTS の代わりに、文字数に比例した長さの無音を返すエンジンを使う。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from app.tts import base, synth  # noqa: E402


class FakeEngine(base.TTSEngine):
    name = "fake"

    def __init__(self, fail_on=None):
        self.calls, self.fail_on = [], fail_on

    def available(self):
        return True, ""

    def synthesize(self, text, settings):
        self.calls.append(text)
        if self.fail_on and self.fail_on in text:
            raise base.TTSError("合成できません")
        return base.make_wav(base.silence(10 * len(text), settings["sample_rate"]), settings["sample_rate"])


CFG = {"tts": {"engine": "openjtalk", "speed": 1.0, "sentence_pause_ms": 400,
               "speakers": {"voicevox": 13, "google": "ja-JP-Neural2-B"}}, "audio": {"sample_rate": 24000}}


def _m(*texts, status="passed"):
    secs = [{"section_id": f"CH001-SEC{i:03d}", "heading": "h", "source_pages": [1], "original_text": t, "audio_text": t,
             "validation": {"status": status, "messages": []}, "audio": {"status": "not_started"}}
            for i, t in enumerate(texts, 1)]
    return {"chapters": [{"chapter_id": "CH001", "chapter_title": "c", "sections": secs}]}


def test_voice_settings_picks_speaker_per_engine():
    assert synth.voice_settings(CFG)["engine"] == "openjtalk" and "speaker" not in synth.voice_settings(CFG)
    assert synth.voice_settings(CFG, "voicevox")["speaker"] == "13"
    assert synth.voice_settings(CFG, "google")["speaker"] == "ja-JP-Neural2-B"


def test_split_sentences_cuts_long_ones_at_commas():
    s = synth.split_sentences("短い文。次の文！\n" + "長い、" * 80 + "終わり。")
    assert s[:2] == ["短い文。", "次の文！"] and all(len(x) <= 150 for x in s) and s[-1].endswith("終わり。")


def test_concat_inserts_pauses():
    a = base.make_wav(base.silence(1000, 24000), 24000)
    assert base.duration_sec(base.concat([a, a], 500, 24000)) == pytest.approx(2.5)


def test_run_makes_reuses_and_remakes(tmp_path):
    m, eng = _m("一つ目の節です。二文目。", "二つ目の節です。"), FakeEngine()
    st = synth.voice_settings(CFG)
    assert synth.run(m, tmp_path, eng, st) == {"done": 2, "reused": 0, "skipped": 0, "failed": 0}
    a = m["chapters"][0]["sections"][0]["audio"]
    assert a["status"] == "done" and (tmp_path / a["file"]).exists() and a["duration_sec"] > 0
    assert m["voice_settings"]["engine"] == "openjtalk"
    n = len(eng.calls)
    assert synth.run(m, tmp_path, eng, st)["reused"] == 2 and len(eng.calls) == n  # 同じ文章なら作り直さない
    old = tmp_path / a["file"]
    m["chapters"][0]["sections"][0]["audio_text"] = "書き換えた文です。"
    assert synth.run(m, tmp_path, eng, st)["done"] == 1
    assert not old.exists() and (tmp_path / a["file"]).exists()  # 古い音声は消える
    assert synth.run(m, tmp_path, eng, dict(st, speed=1.2))["done"] == 2  # 声の設定が変われば作り直す


def test_run_skips_failed_validation_and_records_errors(tmp_path):
    m = _m("直す必要のある節。", status="failed")
    assert synth.run(m, tmp_path, FakeEngine(), synth.voice_settings(CFG))["skipped"] == 1
    assert synth.run(m, tmp_path, FakeEngine(), synth.voice_settings(CFG), force=True)["done"] == 1
    m = _m("ここは大丈夫。ここで失敗。")
    st = synth.run(m, tmp_path, FakeEngine(fail_on="失敗"), synth.voice_settings(CFG))
    a = m["chapters"][0]["sections"][0]["audio"]
    assert st["failed"] == 1 and a["status"] == "failed" and "合成できません" in a["error"]


def test_openjtalk_engine_outputs_24k_mono():
    pytest.importorskip("pyopenjtalk")
    from app.tts.openjtalk import OpenJTalk
    wav = OpenJTalk().synthesize("京セラを創業しました。", {"sample_rate": 24000})
    ch, width, rate, _ = base.wav_params(wav)
    assert (rate, ch, width) == (24000, 1, 2) and base.duration_sec(wav) > 0.5
