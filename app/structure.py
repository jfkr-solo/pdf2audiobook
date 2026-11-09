"""STEP 3: pages.json から章・節を組み立て、原稿の骨組み（manuscript.json）を作る。

- ページ順: スキャンでページが入れ替わっていても、読み取れたノンブル（印刷ページ番号）で並べ直す
- 章: PDFのしおり（目次）があれば最優先。無ければ柱（ページ余白の章名）の変化と「第N章」等の見出し
- 節: 本文より大きい文字の短い行（句点で終わらない）を見出しとみなす。2行見出しは結合
- 段落: 行頭の字下げで段落を区切り、ページをまたぐ段落はつなげる
推測で文字を補うことはせず、怪しい箇所は needs_review に残す。
"""
from __future__ import annotations

import re
import statistics
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from typing import Any

CHAPTER_RE = re.compile(r"^(第[0-9０-９一二三四五六七八九十百]+[章部編]|序章|終章|プロローグ|エピローグ|はじめに|まえがき|おわりに|あとがき|Chapter\s*\d+)")
FRONT_RE = re.compile(r"^(序章|プロローグ|はじめに|まえがき)")
NUMBERED_RE = re.compile(r"^(\d+(\.\d+)+|[0-9０-９]+[.．、]|[（(][0-9０-９一二三四五六七八九十]+[)）])\s*")
BULLET_RE = re.compile(r"^[・●○■□◆◇▶►•\-－※①②③④⑤⑥⑦⑧⑨⑩]")
END_PUNCT = tuple("。、，．」』）)!?！？")
KANJI_DIGITS = dict(zip("〇一二三四五六七八九", "0123456789"))


# ---------------------------------------------------------------- ページ順
def _similar(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a[:400], b[:400], autojunk=False).ratio()


def order_pages(pages: list[dict]) -> tuple[list[dict], list[str]]:
    """ノンブルが半分以上読めたら、それに沿って並べ直す。読めないページは同じ偶奇の
    近いページのずれ幅から補う（スキャン時の見開き入れ替わり対策）。"""
    notes: list[str] = []
    nums = [p.get("printed_page") for p in pages]
    if sum(n is not None for n in nums) < max(3, len(pages) // 2):
        return pages, notes

    # 各ページの「印刷番号 − PDF上の位置」= ずれ幅。誤読（桁の欠け・余分）は全体の中央値から
    # 大きく外れるので捨て、残りから近傍（同じ偶奇）の多数派のずれ幅を求める
    raw = [(i, n - i) for i, n in enumerate(nums) if n is not None]
    center = statistics.median(o for _, o in raw)
    good = {i: o for i, o in raw if abs(o - center) <= 10}

    def local_offset(i: int) -> int | None:
        for window in (8, 16, 32, 64):
            votes = Counter()
            for j, o in good.items():
                if j % 2 == i % 2 and abs(j - i) <= window:
                    votes[o] += 1.0 / (1 + abs(j - i))  # 近いページほど重く
            if votes:
                return votes.most_common(1)[0][0]
        return None

    def _subseq(short: str, long: str) -> bool:
        it = iter(long)
        return all(c in it for c in short)

    def repair(i: int, n: int) -> int | None:
        """誤読したノンブル（例: 二一三→「23」）を、近傍のずれ幅から作った候補のうち
        読めた数字を順に含むものに直す。候補が一つに決まらなければ None。"""
        votes = Counter()
        for j, o in good.items():
            if j % 2 == i % 2 and abs(j - i) <= 16:
                votes[o] += 1.0 / (1 + abs(j - i))
        # 縦書きの漢数字は「一一」と「二」を取り違えやすい（一一八→「二八」、一一二→「一一一一」）
        raw = str(n)
        variants = {raw}
        for a, b in (("2", "11"), ("11", "2")):
            for k in range(len(raw)):
                if raw.startswith(a, k):
                    variants.add(raw[:k] + b + raw[k + len(a):])
        exact = [(w, o) for o, w in votes.items() if str(i + o) in variants]
        fits = exact or [(w, o) for o, w in votes.items() if any(_subseq(v, str(i + o)) for v in variants)]
        if not fits:
            return None
        fits.sort(reverse=True)
        return i + fits[0][1]

    # 直せた誤読は確かな値として近傍の判定にも使う（誤読が続く区間で、離れたページの値に引きずられないように）
    repaired = {i: f - i for i, n in enumerate(nums) if i not in good and n is not None
                and (f := repair(i, n)) is not None}
    good.update(repaired)

    taken = {i + o for i, o in good.items()}

    def place(i: int) -> int | None:
        """ノンブルが読めないページの位置。見開きが入れ替わっている本では、章扉の前後で
        ずれ幅が変わり近傍の多数決が割れる。そのときは、PDF上の隣のページと番号が1違いになり、
        まだ使われていない番号を優先する。"""
        base = local_offset(i)
        neigh = [j + good[j] for j in (i - 1, i + 1) if j in good]
        if base is None or not neigh:
            return base
        def ok(o: int) -> bool:
            k = i + o
            return any(abs(k - n) == 1 for n in neigh) and k not in taken
        if ok(base):
            return base
        for window in (8, 16):
            votes = Counter()
            for j, o in good.items():
                if j % 2 == i % 2 and abs(j - i) <= window:
                    votes[o] += 1.0 / (1 + abs(j - i))
            if votes:
                alts = [o for o in votes if ok(o) and votes[o] >= votes[base] * 0.5]
                if alts:
                    return max(alts, key=lambda o: votes[o])
                break
        return base

    keyed = []
    for i, p in enumerate(pages):
        off = None
        if i not in good or i in repaired:
            off = repaired[i] if i in repaired else place(i)
        if i in good and i not in repaired:
            # 自分の読み取り値と近傍の多数派が食い違うなら近傍を優先
            neigh = local_offset(i)
            key = nums[i] if neigh is None else i + neigh
            if key != nums[i]:
                notes.append(f"p.{p['page']}: ノンブル読み取り値 {nums[i]} は前後と矛盾するため {key} とみなした")
        elif off is not None:
            key = i + off
            if nums[i] is not None:
                notes.append(f"p.{p['page']}: ノンブル読み取り値 {nums[i]} は誤読とみなし {key} とした")
        else:
            key = None
        p["book_page"] = key
        keyed.append((key if key is not None else 10**6 + i, i, p))
    keyed.sort(key=lambda t: (t[0], t[1]))
    ordered = [p for _, _, p in keyed]
    # 同じページを二重にスキャンしたもの（本文がほぼ同じ）は信頼度の高い方だけ残す
    deduped: list[dict] = []
    for p in ordered:
        prev = deduped[-1] if deduped else None
        if prev is not None and _similar(prev.get("text", ""), p.get("text", "")) >= 0.8:
            keep, drop = (prev, p) if (prev.get("ocr_mean_conf") or 0) >= (p.get("ocr_mean_conf") or 0) else (p, prev)
            deduped[-1] = keep
            notes.append(f"p.{drop['page']} は p.{keep['page']} と同じ内容の重複スキャンとみなし除外した")
            continue
        deduped.append(p)
    ordered = deduped
    moved = sum(1 for a, b in zip(ordered, pages) if a is not b)
    if moved:
        notes.append(f"ノンブルに従って {moved} ページの順序を入れ替えた")
    seen = Counter(p.get("book_page") for p in ordered if p.get("book_page") is not None)
    dups = [k for k, c in seen.items() if c > 1]
    if dups:
        notes.append(f"同じノンブルが重複: {dups[:10]}")
    return ordered, notes


def canonical_heads(heads: list[str | None], exclude: str = "", min_count: int = 3) -> dict[str, str]:
    """OCR の揺れ（「心を磨き」「必を磨き」「カー原理原則…」など）を、似た柱の中で
    最も多い表記にまとめる。出現が少ない柱や短すぎる断片は章名として使わない。"""
    counts = Counter(h for h in heads if h and len(h) >= 4 and h != exclude)
    reps: list[str] = []
    mapping: dict[str, str] = {}
    for h, _ in counts.most_common():
        best = max(reps, key=lambda r: _similar(h, r), default=None)
        if best is not None and (_similar(h, best) >= 0.6 or best in h):
            mapping[h] = best
        else:
            reps.append(h)
            mapping[h] = h
    cluster = Counter()
    for h, c in counts.items():
        cluster[mapping[h]] += c
    return {h: r for h, r in mapping.items() if cluster[r] >= min_count}


# ---------------------------------------------------------------- 行の分類
def _body_size(pages: list[dict]) -> float:
    sizes = [l["size"] for p in pages for l in p["lines"] if len(l["text"]) >= 10 and "in_table" not in l]
    return statistics.median(sizes) if sizes else 10.0


def _is_heading(line: dict, body: float, is_ocr: bool, base: float | None = None,
                vertical: bool = False) -> bool:
    t = line["text"].strip()
    if not (2 <= len(t) <= 30) or (is_ocr and line.get("ocr_conf", 0) < 70):
        return False
    # 縦書きの見出しは本文の行頭より上（天）にはみ出して組まれることが多い。
    # OCRエンジンによって文字サイズの測り方が違い大きさだけでは拾えないため、位置でも判定する。
    # 2行見出しの1行目は「、」、2行目は「」」で終わることがあるので句読点の除外より先に見る。
    # 1行目は本文よりやや小さい文字で組まれることがあるので大きさの条件はゆるめる
    if vertical and base is not None and line["bbox"][1] < base - body * 1.5 \
            and line["size"] >= body * 0.9 and not t.endswith("。"):
        return True
    if t.endswith(END_PUNCT):
        return False
    if CHAPTER_RE.match(t) and line["size"] >= body * 1.05:
        return True
    return line["size"] >= body * (1.2 if is_ocr else 1.15) or (line.get("bold") and line["size"] >= body)


def _indent_threshold(lines: list[dict], vertical: bool) -> float | None:
    starts = [l["bbox"][1] if vertical else l["bbox"][0] for l in lines if len(l["text"]) >= 10]
    return statistics.median(starts) if starts else None


def _starts_paragraph(line: dict, base: float | None, vertical: bool, body: float) -> bool:
    if base is None:
        return False
    start = line["bbox"][1] if vertical else line["bbox"][0]
    return start > base + body * 0.6


# ---------------------------------------------------------------- 章・節の組み立て
def _chapter_number(title: str, fallback: int) -> int:
    m = re.match(r"^第([0-9０-９]+|[一二三四五六七八九十]+)", title)
    if not m:
        return 0 if FRONT_RE.match(title) else fallback
    s = unicodedata.normalize("NFKC", m.group(1))
    if s.isdigit():
        return int(s)
    # 十を含む漢数字（例: 十二）
    if "十" in s:
        a, _, b = s.partition("十")
        return (int(KANJI_DIGITS.get(a, "1")) if a else 1) * 10 + (int(KANJI_DIGITS[b]) if b else 0)
    return int("".join(KANJI_DIGITS[c] for c in s))


def _clean_head(s: str | None) -> str | None:
    if not s:
        return None
    s = unicodedata.normalize("NFKC", s).strip(" 　-ー・.,。")
    return s if len(s) >= 2 else None


def build(pages_doc: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    pages, notes = order_pages(list(pages_doc["pages"]))
    body = _body_size(pages)
    toc = pages_doc.get("toc") or []
    toc_l1 = {t["page"]: t["title"] for t in toc if t["level"] == 1}
    toc_l2 = {(t["page"], unicodedata.normalize("NFKC", t["title"]).replace(" ", "")): t["title"]
              for t in toc if t["level"] >= 2}
    vertical_book = pages_doc.get("writing_mode") == "vertical"

    # 柱（章名）の多数決: 各ページの柱を前後で補い、短いノイズを除く
    book_title = pages_doc.get("title") or ""
    canon = canonical_heads([_clean_head(p.get("running_head")) for p in pages], exclude=book_title)

    chapters: list[dict] = []
    removed_noise: list[dict] = []

    def new_chapter(title: str, page: int) -> dict:
        ch = {"title": title, "sections": [], "start_page": page}
        chapters.append(ch)
        return ch

    def new_section(ch: dict, heading: str, page: int) -> dict:
        sec = {"heading": heading, "paragraphs": [], "pages": [], "page": page, "idx": cur_idx[0]}
        ch["sections"].append(sec)
        return sec

    ch = sec = None
    pending_heading: list[str] = []
    pending_size = [0.0]
    cur_idx = [0]
    front_lines: list[str] = []
    line_pos: dict[int, list] = {}  # ページ → [(読み順の位置, 節)]
    title_page_idx: list[int] = []  # 章扉と判断したページ

    def flush_pending(chap) -> None:
        """本文が続かなかった見出し（表紙の書名など）を処理する。"""
        nonlocal sec
        if not pending_heading:
            return
        if chap is None or (chap is chapters[0] and not chap["sections"] and chap["title"] == (book_title or "本文")):
            front_lines.extend(pending_heading)  # 表紙・前付け
        else:
            sec = new_section(chap, "".join(pending_heading), p["page"])
        pending_heading.clear()

    def norm_title(t: str) -> str:
        return unicodedata.normalize("NFKC", t).replace(" ", "").replace("　", "")
    for pidx, p in enumerate(pages):
        is_ocr = p["method"].startswith("ocr")
        vertical = p.get("writing_mode") == "vertical"
        lines = [l for l in p["lines"] if "in_table" not in l]
        if is_ocr:  # 裏写り・ゴミ（極端に小さい文字）は本文から外し、記録だけ残す
            keep = []
            for l in lines:
                no_japanese = not re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", l["text"])
                if l["size"] < body * 0.45 or (no_japanese and l.get("ocr_conf", 100) < 60):
                    removed_noise.append({"page": p["page"], "text": l["text"]})
                else:
                    keep.append(l)
            lines = keep
        base = _indent_threshold(lines, vertical)
        cur_idx[0] = pidx

        # 章の切り替え（しおり > 柱）
        head = canon.get(_clean_head(p.get("running_head")) or "")
        chap_title = toc_l1.get(p["page"]) or head
        if chap_title and not toc_l1 and ch is not None and chap_title != ch["title"] \
                and any(c["title"] == chap_title for c in chapters):
            # 一度終わった章の柱が再び出たのはページ順の乱れか誤読。章は切り替えない
            notes.append(f"p.{p['page']}: 柱「{chap_title}」が現在の章「{ch['title']}」の途中に出現（ページ順を要確認）")
            chap_title = None
        if chap_title and ch is not None and chap_title == ch["title"]:
            ch["last_head_idx"] = pidx
        if chap_title and (ch is None or chap_title != ch["title"]):
            flush_pending(ch)
            prev = ch
            ch = new_chapter(chap_title, p["page"])
            ch["last_head_idx"] = pidx
            sec = None
            # 章の扉・最初のページには柱が無いことが多い。前の章で柱が最後に出たページより
            # 後ろで始まった節は、新しい章の冒頭として移す
            if prev is not None and not toc_l1:
                cut = prev.get("last_head_idx", -1)
                # 章扉が見つかっていれば、扉より前に始まった節は前の章に残す（見開きの入れ替わり対策）
                doors = [i for i in title_page_idx if cut < i <= pidx]
                moving = [x for x in prev["sections"] if x["idx"] > cut and (not doors or x["idx"] >= doors[-1])]
                if moving:
                    prev["sections"] = [x for x in prev["sections"] if x["idx"] <= cut]
                    ch["sections"] = moving
                    sec = moving[-1]
                    ch["start_page"] = moving[0]["page"]
        if ch is None:
            ch = new_chapter(book_title or "本文", p["page"])

        for idx, l in enumerate(lines):
            text = l["text"].strip()
            if not text:
                continue
            norm = unicodedata.normalize("NFKC", text).replace(" ", "")
            if norm == norm_title(ch["title"]) and not ch["sections"]:
                flush_pending(None if len(chapters) == 1 else chapters[-2])
                continue  # 章タイトルそのものの行
            # 柱から章名が取れている本では、章扉の「第3章」や大きな章題の文字は章名と重複するので読まない
            if canon and not toc_l1 and (re.fullmatch(r"第[0-9０-９一二三四五六七八九十]+章", norm)
                                         or (is_ocr and l["size"] >= body * 2.2 and len(norm) <= 15)):
                title_page_idx.append(pidx)
                continue
            if (p["page"], norm) in toc_l2 or _is_heading(l, body, is_ocr, base, vertical):
                if CHAPTER_RE.match(text) and not toc_l1:
                    flush_pending(ch if ch["sections"] else None)
                    if ch["sections"] or norm_title(ch["title"]) != norm:
                        ch = new_chapter(text, p["page"])
                        sec = None
                    continue
                # 2行に分かれた見出しは結合。ただし番号付き見出しや文字サイズが違えば別の見出し
                if pending_heading and NUMBERED_RE.match(text):
                    flush_pending(ch)
                pending_heading.append(text)
                pending_size[0] = l["size"]
                continue
            if pending_heading:
                sec = new_section(ch, "".join(pending_heading), p["page"])
                pending_heading = []
            if sec is None:
                sec = new_section(ch, ch["title"], p["page"])
            starts = (not sec["paragraphs"]) or _starts_paragraph(l, base, vertical, body) or bool(BULLET_RE.match(text))
            if starts:
                sec["paragraphs"].append(text)
            else:
                sec["paragraphs"][-1] += text  # 日本語は改行位置で空白を入れずにつなぐ
            if p["page"] not in sec["pages"]:
                sec["pages"].append(p["page"])
            line_pos.setdefault(p["page"], []).append(((-l["bbox"][2]) if vertical else l["bbox"][1], sec))
    flush_pending(ch)
    if front_lines:
        notes.append(f"表紙・前付けとみなした見出し: {' / '.join(front_lines)}")

    # 図表は直前の本文行の節へ、ページ単位の要確認はそのページ最初の節へ割り当て
    last_sec = None
    for p in pages:
        entries = line_pos.get(p["page"])
        if not entries:  # 本文が取れなかったページ（未OCRなど）は直前の節に付ける
            if last_sec is not None:
                last_sec.setdefault("needs_review", []).extend(p["needs_review"])
                last_sec.setdefault("figures", []).extend(p["figures"])
                if p["page"] not in last_sec["pages"]:
                    last_sec["pages"].append(p["page"])
            continue
        last_sec = entries[-1][1]
        vertical = p.get("writing_mode") == "vertical"
        for f in p["figures"]:
            pos = (-f["bbox"][2]) if (vertical and f.get("bbox")) else (f.get("bbox") or [0, 0])[1]
            target = entries[0][1]
            for epos, esec in entries:
                if epos <= pos:
                    target = esec
            target.setdefault("figures", []).append(f)
        entries[0][1].setdefault("needs_review", []).extend(p["needs_review"])

    # manuscript 形式に変換
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    out_chapters = []
    n = 0
    for c in chapters:
        c["sections"] = [x for x in c["sections"] if x["paragraphs"] or x is not c["sections"][-1]]
        if not c["sections"]:
            continue
        num = _chapter_number(c["title"], n + 1)
        n = max(n, num)
        cid = f"CH{len(out_chapters) + 1:03d}"
        secs = []
        for si, s in enumerate(c["sections"], 1):
            figs = []
            for f in s.get("figures", []):
                f2 = {"page": f["page"], "kind": f["kind"]}
                if "bbox" in f:
                    f2["bbox"] = f["bbox"]
                if f.get("text"):
                    f2["description"] = f["text"]
                elif f.get("rows"):
                    f2["description"] = " / ".join("、".join(r) for r in f["rows"])
                figs.append(f2)
            secs.append({
                "section_id": f"{cid}-SEC{si:03d}",
                "heading": s["heading"],
                "source_pages": s["pages"] or [s["page"]],
                "original_text": "\n".join(s["paragraphs"]),
                "audio_text": "",
                "supplement_text": {"text": "", "ai_generated": True},
                "figures": figs,
                "needs_review": [{k: v for k, v in r.items() if k in ("page", "reason", "text")}
                                 for r in s.get("needs_review", [])],
                "validation": {"status": "pending", "char_coverage": None, "numbers_match": None, "messages": []},
                "audio": {"status": "not_started"},
            })
        out_chapters.append({"chapter_id": cid, "chapter_number": num, "chapter_title": c["title"],
                             "sections": secs, "audio": {"status": "not_started"}})

    manuscript = {
        "schema_version": "1.0",
        "book_id": pages_doc["book_id"],
        "title": pages_doc.get("title") or "無題",
        "author": pages_doc.get("author"),
        "language": "ja-JP",
        "source_pdf": pages_doc["source_pdf"],
        "page_count": pages_doc["page_count"],
        "created_at": now,
        "updated_at": now,
        "dictionary_files": ["dictionaries/common.json"],
        "chapters": out_chapters,
    }
    if removed_noise:
        notes.append(f"ノイズとして本文から外した行: {len(removed_noise)} 行（structure_report.md に一覧）")
    manuscript_notes = notes + [f"noise p.{r['page']}: {r['text']}" for r in removed_noise]
    return manuscript, manuscript_notes


def report_markdown(m: dict[str, Any], notes: list[str]) -> str:
    out = [f"# 構造化レポート: {m['title']}", ""]
    total = 0
    for c in m["chapters"]:
        chars = sum(len(s["original_text"]) for s in c["sections"])
        total += chars
        pages = [pg for s in c["sections"] for pg in s["source_pages"]]
        out.append(f"## {c['chapter_id']} {c['chapter_title']}（{len(c['sections'])}節・{chars:,}字・p.{min(pages)}〜{max(pages)}）")
        for s in c["sections"]:
            out.append(f"- {s['section_id']} {s['heading']}（p.{s['source_pages'][0]}〜{s['source_pages'][-1]}、{len(s['original_text']):,}字）")
        out.append("")
    out.insert(1, f"本文合計 {total:,} 字（読み上げ目安 約{total / 320 / 60:.1f} 時間）\n")
    if notes:
        out += ["## メモ", ""] + [f"- {n}" for n in notes]
    return "\n".join(out) + "\n"
