"""STEP 7: 節ごとの音声（WAV）を作る。

- SSML が使えるエンジン（Google）は段落ごとに合成し、段落の間に paragraph_pause_ms の無音を入れる。
  文ごとに分けて合成すると文頭で毎回イントネーションが仕切り直しになり、文の間も機械的になるため
  （段落の中の「。」「、」の間はエンジン自身が自然に取る）。読み方辞書の yomi 項目は <phoneme> で渡す
- それ以外のエンジンは文ごとに合成し、文の間に sentence_pause_ms の無音を入れてつなぐ
- 読み上げ文と音声設定のハッシュ（text_hash）が前回と同じで、ファイルも残っていれば作り直さない
- STEP 6 で failed の節は飛ばす（--force で作る）
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..reading_dict import apply_yomi, yomi_entries
from .base import TTSEngine, TTSError, concat, duration_sec

VOICE_KEYS = ("engine", "speaker", "speed", "pitch", "volume", "sentence_pause_ms", "paragraph_pause_ms", "sample_rate")
SSML_MAX_CHARS = 1200  # Google の上限は 5000 バイト。日本語1字3バイト＋タグの余裕


def voice_settings(cfg: dict[str, Any], engine: str | None = None) -> dict[str, Any]:
    t = dict(cfg.get("tts") or {})
    if engine:
        t["engine"] = engine
    t.setdefault("engine", "openjtalk")
    speakers = t.pop("speakers", None) or {}
    if t.get("speaker") is None and t["engine"] in speakers:
        t["speaker"] = speakers[t["engine"]]
    t["sample_rate"] = int((cfg.get("audio") or {}).get("sample_rate", 24000))
    if t.get("speaker") is not None:
        t["speaker"] = str(t["speaker"])
    return {k: t[k] for k in VOICE_KEYS if k in t}


def text_hash(text: str, settings: dict[str, Any], yomi: list[dict[str, Any]] | None = None) -> str:
    d: dict[str, Any] = {"text": text, "voice": {k: settings.get(k) for k in VOICE_KEYS}}
    used = yomi_entries(yomi or [], text)
    if used:  # この節に効く読み指定が変わったときだけ作り直す
        d["yomi"] = [[e["surface"], e.get("pattern"), e["reading"]] for e in used]
    key = json.dumps(d, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def split_sentences(text: str, max_chars: int = 150) -> list[str]:
    """句点・改行で文に分ける。長すぎる文は読点で分ける（TTS が途中で切れたり平板になったりするのを防ぐ）。"""
    out: list[str] = []
    for para in text.split("\n"):
        for sent in re.findall(r"[^。！？!?]+[。！？!?」』）)]*|[^。！？!?]+$", para):
            sent = sent.strip()
            while len(sent) > max_chars:
                cut = sent.rfind("、", 0, max_chars)
                cut = cut + 1 if cut > 0 else max_chars
                out.append(sent[:cut])
                sent = sent[cut:].strip()
            if sent and re.search(r"[\w぀-鿿]", sent):
                out.append(sent)
    return out


def split_paragraphs(text: str, max_chars: int = SSML_MAX_CHARS) -> list[str]:
    """段落（改行）ごとに分ける。長すぎる段落は文の切れ目で分ける。"""
    out: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for sent in split_sentences(para):
            if cur and len(cur) + len(sent) > max_chars:
                out.append(cur)
                cur = ""
            cur += sent
        if cur:
            out.append(cur)
    return out


def to_ssml(text: str, yomi: list[dict[str, Any]] | None = None) -> str:
    body = apply_yomi(text, yomi, ssml=True) if yomi else html.escape(text)
    return f"<speak>{body}</speak>"


def synthesize_section(engine: TTSEngine, text: str, settings: dict[str, Any], retries: int = 2,
                       yomi: list[dict[str, Any]] | None = None) -> bytes:
    if getattr(engine, "ssml", False):
        units = [to_ssml(p, yomi) for p in split_paragraphs(text)]
        pause = int(settings.get("paragraph_pause_ms", 700))
    else:
        units = split_sentences(apply_yomi(text, yomi) if yomi else text)
        pause = int(settings.get("sentence_pause_ms", 400))
    parts = []
    for unit in units:
        for attempt in range(retries + 1):
            try:
                parts.append(engine.synthesize(unit, settings))
                break
            except TTSError:
                if attempt == retries:
                    raise
                time.sleep(1 + attempt)
    if not parts:
        raise TTSError("読み上げる文がありません")
    return concat(parts, pause, int(settings.get("sample_rate", 24000)))


def run(m: dict[str, Any], bdir: Path, engine: TTSEngine, settings: dict[str, Any],
        only: set[str] | None = None, force: bool = False, limit: int | None = None,
        progress: Callable[[int, int, str], None] | None = None,
        yomi: list[dict[str, Any]] | None = None) -> dict[str, int]:
    out_dir = bdir / "cache" / "tts"
    out_dir.mkdir(parents=True, exist_ok=True)
    m["voice_settings"] = {k: v for k, v in settings.items() if k != "sample_rate"}
    targets = [s for c in m["chapters"] for s in c["sections"] if only is None or s["section_id"] in only]
    stats = {"done": 0, "reused": 0, "skipped": 0, "failed": 0}
    for i, s in enumerate(targets, 1):
        if limit is not None and stats["done"] >= limit:
            break
        if progress:
            progress(i, len(targets), s["section_id"])
        a = s["audio"]
        h = text_hash(s["audio_text"], settings, yomi)
        f = out_dir / f"{s['section_id']}_{h}.wav"
        if not force and a.get("status") == "done" and a.get("text_hash") == h and a.get("file") and (bdir / a["file"]).exists():
            stats["reused"] += 1
            continue
        if not force and s["validation"].get("status") == "failed":
            stats["skipped"] += 1
            a.update(status="failed", error="STEP 6 の検証で failed のため音声を作っていません")
            continue
        try:
            wav = synthesize_section(engine, s["audio_text"], settings, yomi=yomi)
        except (TTSError, ValueError, OSError) as e:
            stats["failed"] += 1
            a.update(status="failed", error=str(e)[:300], text_hash=None)
            continue
        f.write_bytes(wav)
        old = a.get("file")
        if old and old != str(f.relative_to(bdir)) and (bdir / old).exists():
            (bdir / old).unlink()  # 古い版の音声は消す（同じ節のハッシュ違い）
        a.update(status="done", text_hash=h, file=str(f.relative_to(bdir)), duration_sec=round(duration_sec(wav), 2),
                 generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), error=None)
        stats["done"] += 1
    return stats
