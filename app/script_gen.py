"""STEP 4: 原文（original_text）から読み上げ用の文章（audio_text）を作る。

ルールベースの整形だけで動く（APIキー不要）。config.yaml の llm.provider を anthropic にすると、
ルール整形の後に生成AIで「耳で聞いて分かる文」に整える。AIの出力は原文との一致率で確かめ、
内容が欠けたと判断したらルール整形の結果に戻す（原文の内容を勝手に削らないため）。

英字・略語の読み（DDI→ディーディーアイ など）は STEP 5 の読み方辞書で扱う。
"""
from __future__ import annotations

import difflib
import os
import re
import unicodedata
from typing import Any, Callable

KANJI_DIGITS = "〇一二三四五六七八九"
_KD = {c: str(i) for i, c in enumerate(KANJI_DIGITS)}
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
ORDINAL = ["一つ目", "二つ目", "三つ目", "四つ目", "五つ目", "六つ目", "七つ目", "八つ目", "九つ目", "十目"]
JA = r"぀-ヿ㐀-鿿々〆〇"


# ---------------------------------------------------------------- ルール整形
def kanji_numbers_to_arabic(text: str) -> str:
    """縦書きの漢数字（一九九七年、一〇〇点、二七〇〇）を算用数字にする。
    TTS は「一九九七」を「いちきゅうきゅうなな」と読みがちなため。
    「四六時中」「二三日」「七五三」のような語を壊さないよう、〇を含むか「年」が続く3桁以上だけを対象にする。"""
    def repl(m: re.Match) -> str:
        s = m.group(1)
        return "".join(_KD[c] for c in s) + (m.group(2) or "")

    text = re.sub(rf"([{KANJI_DIGITS}]*〇[{KANJI_DIGITS}]*)()", lambda m: repl(m) if len(m.group(1)) >= 2 else m.group(0), text)
    text = re.sub(rf"([{KANJI_DIGITS}]{{3,}})(年)", repl, text)
    text = re.sub(r"(?<![一-龥])〇(?=[点度円%割])", "0", text)  # 「〇点」
    return text


def _circled(m: re.Match) -> str:
    i = CIRCLED.index(m.group(1))
    return (ORDINAL[i] if i < len(ORDINAL) else f"{i + 1}つ目") + "、"


def rule_based(text: str) -> str:
    """読み上げの妨げになる記号を整え、聞いて分かる形にする。意味は変えない。"""
    # 丸数字は NFKC で「1」になってしまうので先に「一つ目、」へ
    t = re.sub(rf"([{CIRCLED}])\s*", _circled, text)
    t = unicodedata.normalize("NFKC", t)  # 全角英数・半角カナ・（）などをそろえる
    t = t.replace("\r", "")
    # 日本語の間に入った空白（OCR由来）を消す
    t = re.sub(rf"(?<=[{JA}、。」』）)]) +(?=[{JA}「『（(])", "", t)
    # 引用符 〝 〟 " “ ” → 「 」
    t = re.sub(r"[〝“\"]([^〝“”〟\"\n]{1,60})[〟”\"]", r"「\1」", t)
    t = re.sub(r"[〝〟“”\"]", "", t)
    # ダッシュ・三点リーダは間（ま）として読点に
    t = re.sub(r"[―─‐—–]{1,}", "、", t)
    t = re.sub(r"(?:…|･|・{2,}|\.{3,})+", "、", t)
    # 数式の記号
    t = re.sub(r"\s*×\s*", "かける", t)
    t = re.sub(r"(?<=[^\s=])\s*=\s*(?=[^\s=])", "イコール", t)
    t = kanji_numbers_to_arabic(t)
    # OCRの縦線などのゴミ
    t = re.sub(r"[|｜¦]+", "", t)
    # 括弧は読まれないので、中身の前後に間を入れる: 「GE(ゼネラル・エレクトリック)」→「GE、ゼネラル・エレクトリック、」
    t = re.sub(r"\(([^()\n]{1,40})\)", r"、\1、", t)
    # 読点の重複・行頭/句点直後の読点を整理
    t = re.sub(r"、{2,}", "、", t)
    t = re.sub(r"([。！？!?「『\n])、", r"\1", t)
    t = re.sub(r"^、", "", t, flags=re.M)
    t = re.sub(r"、([。」』！？!?])", r"\1", t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{2,}", "\n", t)
    return t.strip()


# ---------------------------------------------------------------- 生成AIによる整形（任意）
LLM_SYSTEM = """あなたは日本語オーディオブックの原稿編集者です。書籍の一節を、耳で聞いて分かる読み上げ原稿に整えます。

守ること:
- 内容を削らない・要約しない・付け足さない。著者の言い回しと語順はできるだけそのまま残す
- 変えてよいのは、目で読む前提の表記だけ（括弧書きの補足を「、つまり」などで文に入れる、箇条書きを「一つ目は」で始まる文にする、記号を言葉にする、数式や表を順に言葉で説明する）
- OCRの明らかな誤字（文脈から一意に分かるもの）は直してよい。確信がなければそのまま残す
- 英字の略語はそのまま残す（読み方は別の辞書で処理する）
- 段落は改行で区切る
- 整えた本文だけを出力する。前置き・説明・見出しは書かない"""


def _anthropic_rewriter(cfg: dict[str, Any]) -> Callable[[str, str], str]:
    import anthropic

    client = anthropic.Anthropic()
    model = cfg.get("llm", {}).get("model", "claude-haiku-5-5")

    def rewrite(heading: str, text: str) -> str:
        resp = client.messages.create(
            model=model,
            max_tokens=16000,
            system=[{"type": "text", "text": LLM_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": f"見出し: {heading}\n\n本文:\n{text}"}],
        )
        if resp.stop_reason != "end_turn":
            raise RuntimeError(f"生成AIの出力が完了しませんでした（stop_reason={resp.stop_reason}）")
        return "".join(b.text for b in resp.content if b.type == "text").strip()

    return rewrite


def make_rewriter(cfg: dict[str, Any]) -> Callable[[str, str], str] | None:
    provider = (cfg.get("llm") or {}).get("provider", "none")
    if provider in (None, "none"):
        return None
    if provider == "anthropic":
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            raise RuntimeError("llm.provider が anthropic ですが ANTHROPIC_API_KEY が .env にありません")
        return _anthropic_rewriter(cfg)
    raise ValueError(f"未対応の llm.provider: {provider}")


def similarity(a: str, b: str) -> float:
    """空白・句読点を除いた文字列の一致率（原文の内容が保たれているかの目安）。"""
    strip = lambda s: re.sub(r"[\s、。「」『』（）()・]", "", s)  # noqa: E731
    return difflib.SequenceMatcher(None, strip(a), strip(b), autojunk=False).ratio()


# ---------------------------------------------------------------- 原稿全体
def generate(m: dict[str, Any], cfg: dict[str, Any], rewriter: Callable[[str, str], str] | None = None,
             min_similarity: float = 0.85, only: set[str] | None = None,
             progress: Callable[[int, int, str], None] | None = None) -> dict[str, int]:
    """各節の audio_text を埋める。only に節IDを渡すとその節だけ作り直す。"""
    sections = [s for c in m["chapters"] for s in c["sections"] if only is None or s["section_id"] in only]
    stats = {"sections": 0, "llm": 0, "llm_rejected": 0, "llm_failed": 0, "empty": 0}
    for i, s in enumerate(sections, 1):
        if progress:
            progress(i, len(sections), s["section_id"])
        msgs = [x for x in s["validation"].get("messages", []) if not x.startswith("script:")]
        base = rule_based(s["original_text"])
        text = base
        if not base:
            stats["empty"] += 1
            msgs.append("script: 本文が空の節です（見出しだけ、または章扉の誤検出の可能性）")
        elif rewriter is not None:
            try:
                cand = rewriter(s["heading"], base)
                sim = similarity(base, cand)
                if sim >= min_similarity:
                    text = cand
                    stats["llm"] += 1
                else:
                    stats["llm_rejected"] += 1
                    msgs.append(f"script: 生成AIの整形は原文との一致率 {sim:.0%} で低いため採用せず、ルール整形を使用")
            except Exception as e:  # noqa: BLE001  1節の失敗で全体を止めない
                stats["llm_failed"] += 1
                msgs.append(f"script: 生成AIの整形に失敗したためルール整形を使用（{e}）")
        if text != s.get("audio_text") and s["audio"].get("status") == "done":
            s["audio"]["status"] = "stale"  # 文章が変わったので音声は作り直しが必要
        s["audio_text"] = text
        s["validation"]["messages"] = msgs
        stats["sections"] += 1
    return stats


def report_markdown(m: dict[str, Any], stats: dict[str, int], max_examples: int = 80) -> str:
    out = [f"# 読み上げ原稿レポート: {m.get('title', '')}", ""]
    total = sum(len(s["audio_text"]) for c in m["chapters"] for s in c["sections"])
    out.append(f"読み上げ文 合計 {total:,} 字（約{total / 330 / 60:.1f} 時間）。"
               f"節 {stats['sections']}、生成AIで整形 {stats['llm']}、不採用 {stats['llm_rejected']}、"
               f"失敗 {stats['llm_failed']}、本文なし {stats['empty']}")
    out.append("")
    for c in m["chapters"]:
        out.append(f"## {c['chapter_id']} {c['chapter_title']}")
        for s in c["sections"]:
            notes = [x[len('script: '):] for x in s["validation"]["messages"] if x.startswith("script:")]
            out.append(f"- {s['section_id']} {s['heading']}（{len(s['audio_text']):,}字）" + (f" ⚠ {' / '.join(notes)}" if notes else ""))
        out.append("")
    # 整形で変わった箇所の例（確認用）
    out.append("## 整形で変わった箇所の例")
    n = 0
    for c in m["chapters"]:
        for s in c["sections"]:
            a, b = s["original_text"], s["audio_text"]
            for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
                if op == "equal" or n >= max_examples:
                    continue
                ctx = a[max(0, i1 - 8):i1].replace("\n", " ")
                out.append(f"- {s['section_id']}: …{ctx}「{a[i1:i2]}」→「{b[j1:j2]}」")
                n += 1
    return "\n".join(out) + "\n"
