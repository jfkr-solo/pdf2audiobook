"""STEP 5: 読み方辞書。

共通辞書（dictionaries/common.json）と教材辞書（data/<BOOK_ID>/dictionary.json）を読み、
読み上げ文の英字・略語・難読語を読み（カタカナ・ひらがな）に置き換える。教材辞書が優先。

"yomi": true の項目は「表記はそのまま、読みだけ指定」する。読み上げ文（audio_text）は書き換えず、
音声合成のときに Google TTS なら SSML の <phoneme alphabet="yomigana">、他のエンジンなら読みの文字に置き換える
（漢字を仮名に書き換えるより、アクセントや区切りが自然なまま読みだけを直せるため）。
"pattern" に正規表現を書くと、文脈を限定できる（例: "(?<=生き物の)性" → 「さが」）。

辞書に無い英字は「未登録語」として一覧にし、大文字の略語なら一文字ずつの読み（DDI→ディーディーアイ）を
候補として教材辞書に書き足す（"auto": true 付き）。候補は人が確かめて直し、"auto" を消せば確定。
"""
from __future__ import annotations

import html
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
                merged[e.get("pattern") or e["surface"]] = e
    return list(merged.values())


def _pattern(surface: str, pattern: str | None = None) -> re.Pattern:
    if pattern:
        return re.compile(pattern)
    s = re.escape(surface)
    if re.match(r"[A-Za-z0-9]", surface):
        # 英数字の語は、前後が英数字でないときだけ置き換える（「DDI」の辞書で「KDDI」を壊さない）
        return re.compile(rf"(?<![A-Za-z0-9]){s}(?![A-Za-z0-9])")
    return re.compile(s)


def apply(text: str, entries: list[dict[str, Any]]) -> str:
    """長い表記から順に置き換える（「TCP/IP」を「TCP」「IP」より先に）。yomi の項目は合成時に使うのでここでは触らない。"""
    for e in sorted((e for e in entries if not e.get("yomi")), key=lambda e: -len(e["surface"])):
        text = _pattern(e["surface"], e.get("pattern")).sub(e["reading"], text)
    return text


def yomi_entries(entries: list[dict[str, Any]], text: str | None = None) -> list[dict[str, Any]]:
    """読みだけ指定する項目。text を渡すと、その文章に出てくるものだけ（音声の作り直し判定に使う）。"""
    ys = [e for e in entries if e.get("yomi")]
    if text is not None:
        ys = [e for e in ys if _pattern(e["surface"], e.get("pattern")).search(text)]
    return sorted(ys, key=lambda e: (e.get("pattern") or "", e["surface"]))


def _yomi_spans(text: str, entries: list[dict[str, Any]]) -> list[tuple[int, int, str]]:
    """重ならない (開始, 終了, 読み) の一覧。長い表記を優先。"""
    spans: list[tuple[int, int, str]] = []
    for e in sorted(entries, key=lambda e: -len(e["surface"])):
        for m in _pattern(e["surface"], e.get("pattern")).finditer(text):
            if m.end() > m.start() and all(m.end() <= a or m.start() >= b for a, b, _ in spans):
                spans.append((m.start(), m.end(), e["reading"]))
    return sorted(spans)


def apply_yomi(text: str, entries: list[dict[str, Any]], ssml: bool = False) -> str:
    """yomi の項目を当てる。ssml=True なら SSML 断片（エスケープ済み・<speak> なし）を返す。"""
    ys = [e for e in entries if e.get("yomi")]
    out, pos = [], 0
    esc = html.escape if ssml else (lambda x: x)
    for a, b, r in _yomi_spans(text, ys):
        out.append(esc(text[pos:a]))
        out.append(f'<phoneme alphabet="yomigana" ph="{html.escape(r)}">{html.escape(text[a:b])}</phoneme>' if ssml else r)
        pos = b
    out.append(esc(text[pos:]))
    return "".join(out)


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
