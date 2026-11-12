"""Google TTS が漢字をどう読んでいるかを、形態素解析（SudachiPy）の読みと突き合わせて洗い出す。

Google TTS は読み（仮名）を返さないが、<phoneme alphabet="yomigana"> で読みを指定した音声が
指定なしの音声とバイト単位で同じなら「Google もその読みで読んでいる」と分かる。これを使って
  1. 文ごとに、漢字の語すべてに Sudachi の読みを指定した音声と、指定なしの音声を比べる
  2. 違えば二分探索で、読みが食い違う語を特定する
レポートの語は「Sudachi が正しくて Google が誤り」か「Sudachi が誤り」のどちらか。人が見て、
Google が誤っている語だけ教材辞書（data/<BOOK>/dictionary.json）に "yomi": true で登録する。

  pip install sudachipy sudachidict_core
  python scripts/check_readings.py BOOK001 [--limit 100] [--workers 4]
  python scripts/check_readings.py BOOK001 --try "思いを描く" "描く" えがく かく   # Google の読みを候補から当てる

API は1文あたり2回以上呼ぶ（本1冊で約1万回・数十万字）。Google の毎分のリクエスト上限に当たると待って再試行する。
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import book_dir, load_config  # noqa: E402
from app.tts.base import TTSError  # noqa: E402
from app.tts.google import GoogleTTS  # noqa: E402

KAN = re.compile(r"[一-龥々〆0-9]")
DIG = ["", "いち", "に", "さん", "よん", "ご", "ろく", "なな", "はち", "きゅう"]
ENGINE = GoogleTTS()
VOICE = {"speaker": "ja-JP-Neural2-B", "sample_rate": 24000}


def _u10000(n: int) -> str:
    out = ""
    for v, name, spec in [(1000, "せん", {1: "せん", 3: "さんぜん", 8: "はっせん"}),
                          (100, "ひゃく", {1: "ひゃく", 3: "さんびゃく", 6: "ろっぴゃく", 8: "はっぴゃく"}),
                          (10, "じゅう", {1: "じゅう"})]:
        q, n = divmod(n, v)
        if q:
            out += spec.get(q) or DIG[q] + name
    return out + DIG[n]


def num2kana(n: int) -> str:
    if n == 0:
        return "ぜろ"
    out = ""
    for v, name in [(10 ** 12, "ちょう"), (10 ** 8, "おく"), (10 ** 4, "まん")]:
        q, n = divmod(n, v)
        if q:
            out += _u10000(q) + name
    return out + _u10000(n)


def hira(s: str) -> str:
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s)


class Reader:
    def __init__(self):
        from sudachipy import dictionary, tokenizer
        self.tok = dictionary.Dictionary(dict="core").create()
        self.lock = threading.Lock()  # Sudachi のトークナイザーはスレッド間で共有できない
        self.mode = tokenizer.Tokenizer.SplitMode.C

    def tokens(self, s: str) -> list[tuple[str, str | None]]:
        out = []
        with self.lock:
            ms = list(self.tok.tokenize(s, self.mode))
        for t in ms:
            su = t.surface()
            if not KAN.search(su) or re.search(r"[A-Za-z]", su):
                out.append((su, None))
            elif su.isdigit():
                out.append((su, num2kana(int(su))))
            else:
                out.append((su, "わたし" if su == "私" else hira(t.reading_form())))
        return out


def _wrap(s: str, r: str) -> str:
    return f'<phoneme alphabet="yomigana" ph="{html.escape(r)}">{html.escape(s)}</phoneme>'


def synth_hash(ssml: str) -> str:
    for attempt in range(6):
        try:
            return hashlib.md5(ENGINE.synthesize(ssml, VOICE)).hexdigest()
        except TTSError as e:
            if "429" not in str(e) or attempt == 5:
                raise
            time.sleep(5 * (attempt + 1))
    raise AssertionError


def ssml_tokens(toks, only) -> str:
    return "<speak>" + "".join(_wrap(su, r) if r and i in only else html.escape(su) for i, (su, r) in enumerate(toks)) + "</speak>"


def check_sentence(reader: Reader, s: str) -> list[tuple[str, str, str]]:
    toks = reader.tokens(s)
    idx = [i for i, (_, r) in enumerate(toks) if r]
    if not idx:
        return []
    plain = synth_hash("<speak>" + html.escape(s) + "</speak>")
    if synth_hash(ssml_tokens(toks, set(idx))) == plain:
        return []

    def bisect(ids):
        if len(ids) == 1:
            return ids
        h = len(ids) // 2
        return [j for part in (ids[:h], ids[h:]) if synth_hash(ssml_tokens(toks, set(part))) != plain for j in bisect(part)]
    ctx = lambda i: "".join(t[0] for t in toks[max(0, i - 4):i]) + "[" + toks[i][0] + "]" + "".join(t[0] for t in toks[i + 1:i + 4])  # noqa: E731
    return [(toks[i][0], toks[i][1], ctx(i)) for i in bisect(idx)]


def try_readings(sentence: str, span: str, cands: list[str]) -> list[str]:
    i = sentence.find(span)
    plain = synth_hash("<speak>" + html.escape(sentence) + "</speak>")
    return [c for c in cands if synth_hash("<speak>" + html.escape(sentence[:i]) + _wrap(span, c) +
                                           html.escape(sentence[i + len(span):]) + "</speak>") == plain]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("book_id")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--try", nargs="+", dest="try_", metavar=("文脈", "語"), help="文脈の文字列 語 候補の読み...")
    args = ap.parse_args()
    cfg = load_config()
    bdir = book_dir(cfg, args.book_id)
    m = json.loads((bdir / "manuscript.json").read_text(encoding="utf-8"))
    text = "\n".join(s["audio_text"] for c in m["chapters"] for s in c["sections"])
    sents = [x.strip() for x in re.findall(r"[^。！？\n]+[。！？」』]*", text) if KAN.search(x)]
    if args.try_:
        ctx, span, *cands = args.try_
        s = next((x for x in sents if ctx in x), None)
        if s is None:
            print("文脈が見つかりません", file=sys.stderr)
            return 1
        print(f"{span}: Google の読み = {try_readings(s, span, cands) or '候補になし'}")
        return 0
    sents = sents[: args.limit]
    reader = Reader()
    found: Counter = Counter()
    examples: dict = {}
    with ThreadPoolExecutor(args.workers) as ex:
        for n, bad in enumerate(ex.map(lambda s: check_sentence(reader, s), sents), 1):
            for su, r, c in bad:
                found[(su, r)] += 1
                examples.setdefault((su, r), c)
            print(f"\r  {n}/{len(sents)}", end="", file=sys.stderr, flush=True)
    print(file=sys.stderr)
    out = ["# 読みの食い違い（Google と Sudachi）", "", f"{len(sents)} 文を確認、食い違い {len(found)} 語", "",
           "| 語 | Sudachi の読み | 回数 | 例 |", "| --- | --- | --- | --- |"]
    out += [f"| {su} | {r} | {n} | {examples[(su, r)]} |" for (su, r), n in found.most_common()]
    (bdir / "reading_check_report.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"レポート: {bdir / 'reading_check_report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
