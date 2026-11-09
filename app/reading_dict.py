"""STEP 5: 読み方辞書。

共通辞書（dictionaries/common.json）と教材辞書（data/<BOOK_ID>/dictionary.json）を読み、
読み上げ文の英字・略語・難読語を読み（カタカナ・ひらがな）に置き換える。教材辞書が優先。

辞書に無い英字は「未登録語」として一覧にし、大文字の略語なら一文字ずつの読み（DDI→ディーディーアイ）を
候補として教材辞書に書き足す（"auto": true 付き）。候補は人が確かめて直し、"auto" を消せば確定。
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

LETTERS = {
    "A": "エー", "B": "ビー", "C": "シー", "D": "ディー", "E": "イー", "F": "エフ", "G": "ジー",
    "H": "エイチ", "I": "アイ", "J": "ジェー", "K": "ケー", "L": "エル", "M": "エム", "N": "エヌ",
    "O": "オー", "P": "ピー", "Q": "キュー", "R": "アール", "S": "エス", "T": "ティー", "U": "ユー",
    "V": "ブイ", "W": "ダブリュー", "X": "エックス", "Y": "ワイ", "Z": "ゼット",
}
DIGITS = {"0": "ゼロ", "1": "ワン", "2": "ツー", "3": "スリー", "4": "フォー", "5": "ファイブ",
          "6": "シックス", "7": "セブン", "8": "エイト", "9": "ナイン"}
LATIN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[./+&'-][A-Za-z0-9]+)*")


def load(paths: list[str | Path]) -> list[dict[str, Any]]:
    """辞書ファイルを順に読み、同じ表記は後のファイルで上書きする。存在しないファイルは無視。"""
    merged: dict[str, dict[str, Any]] = {}
    for p in paths:
        p = Path(p)
        if not p.exists():
            continue
        for e in json.loads(p.read_text(encoding="utf-8")).get("entries", []):
            if e.get("surface") and e.get("reading"):
                merged[e["surface"]] = e
    return list(merged.values())


def _pattern(surface: str) -> re.Pattern:
    s = re.escape(surface)
    if re.match(r"[A-Za-z0-9]", surface):
        # 英数字の語は、前後が英数字でないときだけ置き換える（「DDI」の辞書で「KDDI」を壊さない）
        return re.compile(rf"(?<![A-Za-z0-9]){s}(?![A-Za-z0-9])")
    return re.compile(s)


def apply(text: str, entries: list[dict[str, Any]]) -> str:
    """長い表記から順に置き換える（「TCP/IP」を「TCP」「IP」より先に）。"""
    for e in sorted(entries, key=lambda e: -len(e["surface"])):
        text = _pattern(e["surface"]).sub(e["reading"], text)
    return text


def spell_out(token: str) -> str | None:
    """英大文字（と数字）の略語を一文字ずつ読む。普通の英単語（Kyocera など）は推測しない。"""
    if not re.fullmatch(r"[A-Za-z0-9]+", token):
        return None
    if not (token.isupper() or (token.islower() and len(token) <= 3)):  # au, ido などの短い略語も対象
        return None
    return "".join(LETTERS.get(c.upper()) or DIGITS[c] for c in token)


def unknown_terms(texts: list[str]) -> Counter:
    """辞書適用後の文章に残った英字の語と出現回数。"""
    c: Counter = Counter()
    for t in texts:
        c.update(LATIN_RE.findall(t))
    return c


def suggest(book_dict_path: Path, unknown: Counter) -> list[dict[str, Any]]:
    """未登録語の読み候補を教材辞書に書き足す。既に登録済み（auto を含む）の語は触らない。"""
    doc = {"entries": []}
    if book_dict_path.exists():
        doc = json.loads(book_dict_path.read_text(encoding="utf-8"))
    have = {e["surface"] for e in doc.get("entries", [])}
    added = []
    for term, n in unknown.most_common():
        if term in have:
            continue
        reading = spell_out(term)
        entry = {"surface": term, "reading": reading or "", "auto": True, "count": n}
        if not reading:
            entry["note"] = "読みを推測できません。reading に読みを書いてください"
        doc.setdefault("entries", []).append(entry)
        added.append(entry)
    doc.setdefault("description", "この教材だけの読み方。auto: true は自動の候補なので、確かめて直したら auto を消す")
    book_dict_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return added


def report_markdown(title: str, unknown: Counter, entries: list[dict[str, Any]], dict_path: Path) -> str:
    auto = [e for e in entries if e.get("auto")]
    out = [f"# 読み方辞書レポート: {title}", "",
           f"教材辞書: `{dict_path}`（登録 {len(entries)} 語、うち自動候補 {len(auto)} 語）", ""]
    if auto:
        out += ["## 確認が必要な自動候補", "", "| 表記 | 読み（候補） | 出現 |", "| --- | --- | --- |"]
        out += [f"| {e['surface']} | {e['reading'] or '（未入力）'} | {e.get('count', '')} |" for e in auto]
        out.append("")
    if unknown:
        out += ["## 辞書を適用しても残った英字", ""]
        out += [f"- {t}（{n}回）" for t, n in unknown.most_common()]
    else:
        out.append("辞書を適用した後の読み上げ文に英字は残っていません。")
    return "\n".join(out) + "\n"
