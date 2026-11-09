"""STEP 7: 節ごとの音声（WAV）を作る。

- 読み上げ文を文ごとに分けて合成し、文の間に sentence_pause_ms の無音を入れてつなぐ
- 読み上げ文と音声設定のハッシュ（text_hash）が前回と同じで、ファイルも残っていれば作り直さない
- STEP 6 で failed の節は飛ばす（--force で作る）
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .base import TTSEngine, TTSError, concat, duration_sec

VOICE_KEYS = ("engine", "speaker", "speed", "pitch", "volume", "sentence_pause_ms", "sample_rate")


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


def text_hash(text: str, settings: dict[str, Any]) -> str:
    key = json.dumps({"text": text, "voice": {k: settings.get(k) for k in VOICE_KEYS}}, ensure_ascii=False, sort_keys=True)
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


def synthesize_section(engine: TTSEngine, text: str, settings: dict[str, Any], retries: int = 2) -> bytes:
    parts = []
    for sent in split_sentences(text):
        for attempt in range(retries + 1):
            try:
                parts.append(engine.synthesize(sent, settings))
                break
            except TTSError:
                if attempt == retries:
                    raise
                time.sleep(1 + attempt)
    if not parts:
        raise TTSError("読み上げる文がありません")
    return concat(parts, int(settings.get("sentence_pause_ms", 400)), int(settings.get("sample_rate", 24000)))


def run(m: dict[str, Any], bdir: Path, engine: TTSEngine, settings: dict[str, Any],
        only: set[str] | None = None, force: bool = False, limit: int | None = None,
        progress: Callable[[int, int, str], None] | None = None) -> dict[str, int]:
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
        h = text_hash(s["audio_text"], settings)
        f = out_dir / f"{s['section_id']}_{h}.wav"
        if not force and a.get("status") == "done" and a.get("text_hash") == h and a.get("file") and (bdir / a["file"]).exists():
            stats["reused"] += 1
            continue
        if not force and s["validation"].get("status") == "failed":
            stats["skipped"] += 1
            a.update(status="failed", error="STEP 6 の検証で failed のため音声を作っていません")
            continue
        try:
            wav = synthesize_section(engine, s["audio_text"], settings)
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
