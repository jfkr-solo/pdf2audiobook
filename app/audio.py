"""STEP 8: 節ごとの WAV を章ごと・全体の MP3 にまとめる。

- 章: 章タイトルの読み上げ → 節見出しの読み上げ → 節の本文 … の順に、間に無音を挟んでつなぐ
- FFmpeg で音量をそろえ（loudnorm）、MP3（モノラル）にして ID3 タグ（書名・章番号・書籍ID・作成日時）を付ける
- 出力: data/<BOOK>/audio/00_プロローグ.mp3、01_第1章 思いを実現させる.mp3 …、99_全章結合版.mp3
- 章の材料（節の音声・見出し・設定）のハッシュが前回と同じでファイルもあれば作り直さない

見出しの読み上げ音声は STEP 7 と同じエンジンで作り、cache/tts/heading_<ハッシュ>.wav に保存して使い回す。
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .tts.base import TTSEngine, TTSError
from .tts.synth import synthesize_section, text_hash

FRONT_BACK = re.compile(r"(プロローグ|エピローグ|まえがき|はじめに|序章|序文|あとがき|おわりに|終章|付録|解説)")
WHOLE_NAME = "99_全章結合版.mp3"


def chapter_label(c: dict[str, Any]) -> str:
    """読み上げ・ファイル名に使う章の名前。「第N章」が無い本章には付ける（プロローグ・あとがき などには付けない）。"""
    t = c["chapter_title"].strip()
    n = c.get("chapter_number")
    if not n or re.match(r"第\S+章", t) or FRONT_BACK.search(t):
        return t
    return f"第{n}章 {t}"


def file_name(c: dict[str, Any], index: int) -> str:
    n = c.get("chapter_number")
    safe = re.sub(r'[\\/:*?"<>|\s]+', " ", chapter_label(c)).strip()
    return f"{(n if n is not None else index):02d}_{safe}.mp3"


def build_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    t, a = cfg.get("tts") or {}, cfg.get("audio") or {}
    return {
        "section_pause_ms": int(t.get("section_pause_ms", 1500)),
        "chapter_pause_ms": int(t.get("chapter_pause_ms", 3000)),
        "heading_pause_ms": int(a.get("heading_pause_ms", 800)),
        "read_headings": bool(a.get("read_headings", True)),
        "bitrate": str(a.get("bitrate", "64k")),
        "loudnorm": str(a.get("loudnorm", "I=-16:TP=-1.5:LRA=11")),
        "sample_rate": int(a.get("sample_rate", 24000)),
    }


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


class Builder:
    def __init__(self, m: dict[str, Any], bdir: Path, settings: dict[str, Any],
                 engine: TTSEngine | None = None, voice: dict[str, Any] | None = None,
                 speakable: Callable[[str], str] | None = None):
        self.m, self.bdir, self.st = m, bdir, settings
        self.engine, self.voice = engine, voice or {}
        self.speakable = speakable or (lambda s: s)  # 見出しを読み上げ文に整える（辞書の適用など）
        self.rate = settings["sample_rate"]
        (bdir / "audio").mkdir(parents=True, exist_ok=True)
        (bdir / "cache" / "build").mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ 材料
    def heading_wav(self, text: str) -> Path | None:
        if not self.st["read_headings"] or not text.strip():
            return None
        if self.engine is None:
            raise TTSError("見出しを読み上げるには TTS エンジンが必要です（audio.read_headings: false で省略可）")
        spoken = self.speakable(text)
        f = self.bdir / "cache" / "tts" / f"heading_{text_hash(spoken, self.voice)}.wav"
        if not f.exists():
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(synthesize_section(self.engine, spoken, self.voice))
        return f

    def chapter_parts(self, c: dict[str, Any]) -> tuple[list[Path | int], list[str]]:
        """章の材料（WAV のパスと無音のミリ秒の列）と、音声の無い節の一覧。"""
        parts: list[Path | int] = []
        missing = []
        label = chapter_label(c)
        h = self.heading_wav(label)
        if h:
            parts += [h, self.st["heading_pause_ms"] * 2]
        for i, s in enumerate(c["sections"]):
            a = s["audio"]
            f = self.bdir / a["file"] if a.get("file") else None
            if a.get("status") != "done" or f is None or not f.exists():
                missing.append(s["section_id"])
                continue
            if i and parts and isinstance(parts[-1], Path):
                parts.append(self.st["section_pause_ms"])
            head = s["heading"].strip()
            if head and head not in (c["chapter_title"].strip(), label):  # 章タイトルと同じ見出しは2回読まない
                hw = self.heading_wav(head)
                if hw:
                    parts += [hw, self.st["heading_pause_ms"]]
            parts.append(f)
        return parts, missing

    def parts_hash(self, parts: list[Path | int], extra: Any = None) -> str:
        key = [p.name if isinstance(p, Path) else p for p in parts]
        enc = {k: self.st[k] for k in ("bitrate", "loudnorm", "sample_rate")}
        return hashlib.sha256(json.dumps([key, enc, extra], ensure_ascii=False).encode()).hexdigest()[:16]

    # ------------------------------------------------------------ 書き出し
    def write_wav(self, parts: list[Path | int], out: Path) -> float:
        """材料を1つの WAV に書き出す（1節ずつ読むので長い本でもメモリを使いすぎない）。秒数を返す。"""
        frames = 0
        with wave.open(str(out), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.rate)
            for p in parts:
                if isinstance(p, int):
                    n = int(self.rate * p / 1000)
                    w.writeframes(b"\x00\x00" * n)
                    frames += n
                    continue
                with wave.open(str(p)) as r:
                    if (r.getnchannels(), r.getsampwidth(), r.getframerate()) != (1, 2, self.rate):
                        raise TTSError(f"{p.name}: WAV の形式が違います（1ch・16bit・{self.rate}Hz が必要）")
                    n = r.getnframes()
                    w.writeframes(r.readframes(n))
                    frames += n
        return frames / self.rate

    def encode(self, wav: Path, mp3: Path, tags: dict[str, str]) -> None:
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav)]
        if self.st["loudnorm"]:
            cmd += ["-af", f"loudnorm={self.st['loudnorm']}"]
        cmd += ["-ar", str(self.rate), "-ac", "1", "-c:a", "libmp3lame", "-b:a", self.st["bitrate"], "-id3v2_version", "3"]
        for k, v in tags.items():
            cmd += ["-metadata", f"{k}={v}"]
        tmp = mp3.with_suffix(".part.mp3")
        r = subprocess.run(cmd + [str(tmp)], capture_output=True, text=True)
        if r.returncode != 0:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"FFmpeg が失敗しました: {r.stderr.strip()[-300:]}")
        tmp.replace(mp3)

    def tags(self, title: str, track: str, ids: str, now: str) -> dict[str, str]:
        return {"title": title, "album": self.m.get("title", ""), "artist": self.m.get("author") or "",
                "track": track, "date": now[:4], "comment": f"{self.m.get('book_id', '')} {ids} generated {now}"}

    def chapter_writer(self, parts: list[Path | int]) -> Callable[[Path, dict[str, str]], float]:
        def write(out: Path, tags: dict[str, str]) -> float:
            raw = self.bdir / "cache" / "build" / (out.stem + ".wav")
            try:
                dur = self.write_wav(parts, raw)
                self.encode(raw, out, tags)
            finally:
                raw.unlink(missing_ok=True)  # 中間の WAV は大きいので残さない
            return dur
        return write

    def whole_writer(self, mp3s: list[Path], durations: list[float]) -> Callable[[Path, dict[str, str]], float]:
        """章の MP3（音量はそろえ済み）を、間に無音を挟んでそのままつなぐ（再エンコードしないので速い）。"""
        def write(out: Path, tags: dict[str, str]) -> float:
            build = self.bdir / "cache" / "build"
            gap = build / f"silence_{self.st['chapter_pause_ms']}.mp3"
            r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                                f"anullsrc=r={self.rate}:cl=mono", "-t", str(self.st["chapter_pause_ms"] / 1000),
                                "-c:a", "libmp3lame", "-b:a", self.st["bitrate"], str(gap)], capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"FFmpeg が失敗しました: {r.stderr.strip()[-300:]}")
            lst = build / "whole.txt"
            files = [x for i, f in enumerate(mp3s) for x in ([gap] if i else []) + [f]]
            lst.write_text("".join(f"file '{f.resolve().as_posix()}'\n" for f in files), encoding="utf-8")
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                   "-map_metadata", "-1", "-c", "copy", "-id3v2_version", "3"]
            for k, v in tags.items():
                cmd += ["-metadata", f"{k}={v}"]
            tmp = out.with_suffix(".part.mp3")
            r = subprocess.run(cmd + [str(tmp)], capture_output=True, text=True)
            lst.unlink(missing_ok=True)
            if r.returncode != 0:
                tmp.unlink(missing_ok=True)
                raise RuntimeError(f"FFmpeg が失敗しました: {r.stderr.strip()[-300:]}")
            tmp.replace(out)
            return sum(durations) + self.st["chapter_pause_ms"] / 1000 * (len(mp3s) - 1)
        return write

    def _make(self, writer, rel: str, title: str, track: str, ids: str, a: dict[str, Any], h: str, force: bool) -> bool:
        """a（audio 欄）が同じハッシュで、ファイルもあれば作らない。作ったら True。"""
        out = self.bdir / rel
        if not force and a.get("status") == "done" and a.get("text_hash") == h and a.get("file") == rel and out.exists():
            return False
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        dur = writer(out, self.tags(title, track, ids, now))
        old = a.get("file")
        if old and old != rel and (self.bdir / old).exists():
            (self.bdir / old).unlink()  # 章の名前が変わったときの古いファイル
        a.clear()
        a.update(status="done", text_hash=h, file=rel, duration_sec=round(dur, 2), generated_at=now, error=None)
        return True

    # ------------------------------------------------------------ 全体
    def run(self, only: set[str] | None = None, force: bool = False, allow_missing: bool = False,
            whole: bool = True, progress: Callable[[int, int, str], None] | None = None) -> dict[str, Any]:
        chapters = self.m["chapters"]
        stats: dict[str, Any] = {"built": 0, "reused": 0, "failed": 0, "missing": [], "whole": "skipped"}
        hashes = []
        for i, c in enumerate(chapters):
            a = c.setdefault("audio", {"status": "not_started"})
            parts, missing = self.chapter_parts(c)
            stats["missing"] += missing
            ok = parts and (allow_missing or not missing)
            if ok:
                h = self.parts_hash(parts, chapter_label(c))
                hashes.append(h)
            if only is not None and c["chapter_id"] not in only:
                continue
            if progress:
                progress(i + 1, len(chapters), c["chapter_id"])
            if not ok:
                stats["failed"] += 1
                why = f"音声の無い節があります: {', '.join(missing[:5])}" if missing else "音声のある節がありません"
                a.update(status="failed", error=why + "（STEP 7 で作るか --allow-missing）")
                continue
            try:
                made = self._make(self.chapter_writer(parts), f"audio/{file_name(c, i)}", chapter_label(c), f"{i + 1}/{len(chapters)}",
                                  c["chapter_id"], a, h, force)
            except (TTSError, RuntimeError, OSError) as e:
                stats["failed"] += 1
                a.update(status="failed", error=str(e)[:300])
                continue
            stats["built" if made else "reused"] += 1
        if whole:
            wa = self.m.setdefault("audio", {"status": "not_started"})
            done = [c["audio"] for c in chapters if c["audio"].get("status") == "done" and c["audio"].get("file")]
            if stats["failed"] or len(hashes) != len(chapters) or len(done) != len(chapters):
                wa.update(status="failed", error="作れなかった章があるため全章結合版を作っていません")
                stats["whole"] = "failed"
            else:
                try:
                    writer = self.whole_writer([self.bdir / a["file"] for a in done], [a["duration_sec"] or 0 for a in done])
                    made = self._make(writer, f"audio/{WHOLE_NAME}", f"{self.m.get('title', '')}（全章）", "", "ALL", wa,
                                      self.parts_hash([], [hashes, self.st["chapter_pause_ms"]]), force)
                    stats["whole"] = "built" if made else "reused"
                except (TTSError, RuntimeError, OSError) as e:
                    wa.update(status="failed", error=str(e)[:300])
                    stats["whole"] = "failed"
        return stats


def report_markdown(m: dict[str, Any]) -> str:
    def fmt(sec):
        sec = int(sec or 0)
        return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"

    out = [f"# 音声ファイル: {m.get('title', '')}", "", "| ファイル | 長さ | 状態 |", "| --- | --- | --- |"]
    for c in m["chapters"] + ([{"audio": m["audio"]}] if m.get("audio") else []):
        a = c.get("audio") or {}
        name = Path(a["file"]).name if a.get("file") else c.get("chapter_title", "全章結合版")
        out.append(f"| {name} | {fmt(a.get('duration_sec'))} | {a.get('status')}{'：' + a['error'] if a.get('error') else ''} |")
    return "\n".join(out) + "\n"
