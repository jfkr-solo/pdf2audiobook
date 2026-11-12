"""読み上げを自然にする処理（読みだけ指定する辞書・段落ごとの SSML 合成・文途中の改行つなぎ）のテスト。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import reading_dict  # noqa: E402
from app.script_gen import join_broken_lines, rule_based  # noqa: E402
from app.tts import base, synth  # noqa: E402

YOMI = [{"surface": "性", "reading": "さが", "yomi": True, "pattern": "(?<=生き物の)性"},
        {"surface": "大和", "reading": "だいわ", "yomi": True, "pattern": "(?<=「)大和(?=」)"},
        {"surface": "DDI", "reading": "ディーディーアイ"}]


def test_yomi_entries_are_not_applied_to_script_text():
    assert reading_dict.apply("人間という生き物の性です。DDI", YOMI) == "人間という生き物の性です。ディーディーアイ"


def test_apply_yomi_ssml_and_plain():
    t = "生き物の性と<性格>。「大和」と大和"
    assert reading_dict.apply_yomi(t, YOMI, ssml=True) == (
        '生き物の<phoneme alphabet="yomigana" ph="さが">性</phoneme>と&lt;性格&gt;。'
        '「<phoneme alphabet="yomigana" ph="だいわ">大和</phoneme>」と大和')
    assert reading_dict.apply_yomi(t, YOMI) == "生き物のさがと<性格>。「だいわ」と大和"


def test_text_hash_changes_only_when_used_yomi_changes():
    st = {"engine": "google"}
    h = synth.text_hash("生き物の性", st, YOMI)
    assert h != synth.text_hash("生き物の性", st, [])
    assert synth.text_hash("関係ない文", st, YOMI) == synth.text_hash("関係ない文", st, [])


class SSMLEngine(base.TTSEngine):
    name, ssml = "fake_ssml", True

    def __init__(self):
        self.calls = []

    def synthesize(self, text, settings):
        self.calls.append(text)
        return base.make_wav(base.silence(1000, 24000), 24000)


def test_ssml_engine_synthesizes_per_paragraph():
    eng = SSMLEngine()
    wav = synth.synthesize_section(eng, "一文目。生き物の性です。\n二段落目。", {"sample_rate": 24000, "paragraph_pause_ms": 500},
                                   yomi=YOMI)
    assert eng.calls == ['<speak>一文目。生き物の<phoneme alphabet="yomigana" ph="さが">性</phoneme>です。</speak>',
                         "<speak>二段落目。</speak>"]
    assert base.duration_sec(wav) == 2.5


def test_split_paragraphs_keeps_long_paragraph_under_limit():
    para = "とても長い文です。" * 300
    parts = synth.split_paragraphs(para + "\n次。")
    assert all(len(p) <= synth.SSML_MAX_CHARS for p in parts) and "".join(parts[:-1]) == para and parts[-1] == "次。"


def test_join_broken_lines():
    assert join_broken_lines("上りつめると、\nなか見えなくなる。") == "上りつめると、なか見えなくなる。"
    assert join_broken_lines("することな\nど真剣に") == "することなど真剣に"
    assert join_broken_lines("生き抜いていく\nそのような") == "生き抜いていく、そのような"
    assert join_broken_lines("外側から、\n一つ目、知性") == "外側から、\n一つ目、知性"  # 箇条書きは残す
    assert join_broken_lines("能力\nつまり") == "能力\nつまり"  # 見出し・式の行は残す


def test_rule_based_plus_and_hyphen():
    assert rule_based("1+1の答え") == "1たす1の答え"
    assert rule_based("性格+哲学") == "性格プラス哲学"
    assert rule_based("魂-真我") == "魂、真我"
