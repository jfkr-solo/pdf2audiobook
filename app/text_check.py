"""STEP 6: 読み上げ文（audio_text）を原文（original_text）と照合し、各節の validation を埋める。

- 文字カバー率: 原文の漢字・かなが、読み上げ文に同じ順で残っている割合。
  読み上げ文では記号・数字・英字が読みや算用数字に変わるので、漢字とかなだけで比べる
- 数値の一致: 原文の数（漢数字は算用数字にそろえる）が読み上げ文にすべて残っているか
- 章・節の順序: 節IDの連番と、原文ページが大きく逆戻りしていないか

passed（問題なし）/ warning（人が確かめる）/ failed（音声にする前に直す）を節ごとに付ける。
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from collections import Counter
from typing import Any

from .script_gen import kanji_numbers_to_arabic

JA_CHARS = re.compile(r"[ぁ-ゖァ-ヺ㐀-鿿々〆]")
# 辞書の読み（カタカナ）や数字の読み替えで増える文字は比較から外す
KATAKANA = re.compile(r"[ァ-ヺ]")

PASS_COVERAGE = 0.95
FAIL_COVERAGE = 0.85


def _ja(s: str, drop_katakana: bool) -> str:
    # 漢数字の数（一九九七）は読み上げ文で算用数字になるので、比べる前にそろえる
    chars = JA_CHARS.findall(kanji_numbers_to_arabic(unicodedata.normalize("NFKC", s)))
    if drop_katakana:
        chars = [c for c in chars if not KATAKANA.fullmatch(c)]
    return "".join(chars)


def char_coverage(original: str, audio: str) -> float:
    """原文の漢字・ひらがな（カタカナは辞書の読みと混ざるので除く）のうち、読み上げ文に順番どおり残った割合。"""
    a, b = _ja(original, True), _ja(audio, True)
    if not a:
        return 1.0
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return sum(blk.size for blk in sm.get_matching_blocks()) / len(a)


def numbers(text: str) -> Counter:
    # 丸数字（①）は読み上げ文で「一つ目」になるので数値として数えない
    t = re.sub(r"[\u2460-\u2473]", "", text)
    t = kanji_numbers_to_arabic(unicodedata.normalize("NFKC", t))
    return Counter(re.findall(r"\d+(?:\.\d+)?", t))


def check_section(s: dict[str, Any]) -> dict[str, Any]:
    msgs = [x for x in s["validation"].get("messages", []) if not x.startswith("check:")]
    orig, audio = s.get("original_text", ""), s.get("audio_text", "")
    if not audio.strip():
        status = "failed" if orig.strip() else "warning"
        msgs.append("check: 読み上げ文が空です")
        return {"status": status, "char_coverage": 0.0 if orig.strip() else None, "numbers_match": None, "messages": msgs}
    cov = round(char_coverage(orig, audio), 3)
    want, got = numbers(orig), numbers(audio)
    missing = want - got
    nums_ok = not missing
    if cov < FAIL_COVERAGE:
        status = "failed"
        msgs.append(f"check: 原文の文字のうち読み上げ文に残ったのは {cov:.0%} です（{FAIL_COVERAGE:.0%} 未満）")
    elif cov < PASS_COVERAGE or not nums_ok:
        status = "warning"
        if cov < PASS_COVERAGE:
            msgs.append(f"check: 文字カバー率 {cov:.0%}（{PASS_COVERAGE:.0%} 未満）")
    else:
        status = "passed"
    if not nums_ok:
        msgs.append("check: 原文の数値が読み上げ文にありません: " + "、".join(sorted(missing.elements())[:10]))
    # OCRで信頼度の低かった語のうち、漢字・かなを含むもの（記号だけの語は読みに影響しない）
    weak = [f"p.{r['page']}「{w}」" for r in s.get("needs_review") or [] if "低信頼" in r.get("reason", "")
            for w in r.get("text", "").split("、") if JA_CHARS.search(w)]
    if weak:
        if status == "passed":
            status = "warning"
        msgs.append(f"check: OCRの読み取りが不確かな語: {'、'.join(weak[:10])}（原本で確認）")
    return {"status": status, "char_coverage": cov, "numbers_match": nums_ok, "messages": msgs}


def check_order(m: dict[str, Any]) -> list[str]:
    notes = []
    for ci, c in enumerate(m["chapters"], 1):
        if c["chapter_id"] != f"CH{ci:03d}":
            notes.append(f"章IDが連番になっていません: {c['chapter_id']}（{ci}番目）")
        last_page = 0
        for si, s in enumerate(c["sections"], 1):
            if s["section_id"] != f"{c['chapter_id']}-SEC{si:03d}":
                notes.append(f"節IDが連番になっていません: {s['section_id']}")
            pages = s.get("source_pages") or []
            if pages and last_page and min(pages) < last_page - 2:
                notes.append(f"{s['section_id']}: 原文ページ {min(pages)} が前の節（p.{last_page}）より前に戻っています")
            if pages:
                last_page = max(last_page, max(pages))
    return notes


def run(m: dict[str, Any]) -> dict[str, Any]:
    stats = Counter()
    for c in m["chapters"]:
        for s in c["sections"]:
            s["validation"] = check_section(s)
            stats[s["validation"]["status"]] += 1
    return {"status": dict(stats), "order": check_order(m)}


def report_markdown(m: dict[str, Any], result: dict[str, Any], schema_errors: list[str]) -> str:
    st = result["status"]
    out = [f"# 原稿の検証レポート: {m.get('title', '')}", "",
           f"節ごとの判定: 問題なし {st.get('passed', 0)}、要確認 {st.get('warning', 0)}、要修正 {st.get('failed', 0)}",
           f"スキーマ検証: {'OK' if not schema_errors else f'NG（{len(schema_errors)} 件）'}",
           f"章・節の順序: {'OK' if not result['order'] else f'{len(result['order'])} 件の指摘'}", ""]
    covs = [s["validation"]["char_coverage"] for c in m["chapters"] for s in c["sections"]
            if s["validation"].get("char_coverage") is not None]
    if covs:
        out.append(f"文字カバー率: 最小 {min(covs):.1%}、平均 {sum(covs) / len(covs):.1%}")
        out.append("")
    bad = [(s, c) for c in m["chapters"] for s in c["sections"] if s["validation"]["status"] != "passed"]
    if bad:
        out += ["## 要確認・要修正の節", ""]
        for s, c in bad:
            msgs = [x[len("check: "):] for x in s["validation"]["messages"] if x.startswith("check:")]
            out.append(f"- **{s['validation']['status']}** {s['section_id']} {s['heading']}: {' / '.join(msgs)}")
        out.append("")
    if result["order"]:
        out += ["## 章・節の順序", ""] + [f"- {n}" for n in result["order"]] + [""]
    if schema_errors:
        out += ["## スキーマ検証のエラー", ""] + [f"- {e}" for e in schema_errors[:50]] + [""]
    return "\n".join(out) + "\n"
