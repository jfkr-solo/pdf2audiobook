"""STEP 9: できた音声を確かめ、問題のある節だけ作り直す。

節ごとの WAV について:
- 話す速さ: 読み上げ文の文字数 ÷ 再生時間。本全体の中央値から大きく外れる節は、文が抜けた・余計な音が入った疑い
- 長い無音: 文の間（sentence_pause_ms）よりずっと長い無音があれば、途中で合成が途切れた疑い
- 末尾の途切れ: 最後の数十ミリ秒にまだ大きな音があれば、文の途中で切れた疑い
- 音割れ: 最大値に張り付いたサンプルの割合
- 無音だけ・ファイルが無い・読み上げ文が変わった（ハッシュ違い）

章の MP3 は、ファイルがあるか、長さが記録と合うかを ffprobe で確かめる。
failed の節は audio.status を stale にでき、tts → build で作り直す。
"""
from __future__ import annotations

import json
import re
import statistics
import subprocess
import wave
from pathlib import Path
from typing import Any

import numpy as np

from .tts.synth import text_hash

SPOKEN = re.compile(r"[぀-ヿ㐀-鿿々〆A-Za-z0-9]")
DEFAULTS = {
    "rate_warn": [0.75, 1.35],   # 中央値に対する話す速さの比（この外は warning）
    "rate_fail": [0.5, 2.0],     # この外は failed
    "max_silence_sec": 3.0,      # 節の途中でこれより長い無音は warning
    "silence_db": -50,           # これより小さい音を無音とみなす（dBFS）
    "tail_ms": 30,               # 末尾の途切れを見る長さ
    "tail_ratio": 0.3,           # 末尾の最大音量が節全体の最大音量のこの割合を超えたら warning
    "clip_ratio": 0.001,         # 音割れサンプルの割合の上限
}


def settings(cfg: dict[str, Any]) -> dict[str, Any]:
    return {**DEFAULTS, **(cfg.get("audio_check") or {})}


def spoken_chars(text: str) -> int:
    return len(SPOKEN.findall(text))


def read_pcm(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path)) as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise ValueError(f"{path.name}: 1ch・16bit の WAV ではありません")
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64), w.getframerate()


def analyze(x: np.ndarray, rate: int, st: dict[str, Any]) -> dict[str, Any]:
    """WAV の数値（長さ、最長の無音、末尾の音量、音割れ）を出す。"""
    dur = len(x) / rate if rate else 0.0
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    frame = max(1, int(rate * 0.02))
    n = len(x) // frame
    rms = np.sqrt(np.mean(x[: n * frame].reshape(n, frame) ** 2, axis=1)) if n else np.zeros(0)
    thr = 32768 * 10 ** (st["silence_db"] / 20)
    silent = rms < thr
    # 先頭と末尾の無音は除いて、途中の最長の無音を数える
    voiced = np.flatnonzero(~silent)
    longest = 0.0
    if len(voiced):
        inner = np.concatenate(([0], silent[voiced[0]: voiced[-1] + 1].astype(np.int8), [0]))
        edges = np.flatnonzero(np.diff(inner))  # 無音の始まりと終わりが交互に並ぶ
        runs = edges[1::2] - edges[0::2]
        longest = float(runs.max()) * frame / rate if len(runs) else 0.0
    tail = x[-max(1, int(rate * st["tail_ms"] / 1000)):] if len(x) else x
    tail_peak = float(np.max(np.abs(tail))) if len(tail) else 0.0
    return {"duration": dur, "peak": peak, "voiced": bool(len(voiced)), "longest_silence": round(longest, 2),
            "tail_ratio": round(tail_peak / peak, 3) if peak else 0.0,
            "clip_ratio": float(np.mean(np.abs(x) >= 32767)) if len(x) else 0.0}


def check_sections(m: dict[str, Any], bdir: Path, st: dict[str, Any], voice: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """節ごとの判定の一覧（section_id, status, rate, messages）。音声の無い節は not_started として返す。"""
    rows = []
    for c in m["chapters"]:
        for s in c["sections"]:
            a = s["audio"]
            row = {"section_id": s["section_id"], "heading": s["heading"], "status": "passed", "rate": None, "messages": []}
            rows.append(row)
            f = bdir / a["file"] if a.get("file") else None
            if a.get("status") not in ("done", "stale") or f is None:
                row["status"] = "not_started"
                row["messages"].append(f"音声がありません（{a.get('status')}）")
                continue
            if not f.exists():
                row["status"] = "failed"
                row["messages"].append(f"ファイルがありません: {a['file']}")
                continue
            if a.get("status") == "stale":
                row["status"] = "failed"
                row["messages"].append("作り直し待ちです（stale）")
            if voice is not None and a.get("text_hash") != text_hash(s["audio_text"], voice):
                row["status"] = "failed"
                row["messages"].append("読み上げ文か声の設定が音声を作った後に変わっています")
            x, rate = read_pcm(f)
            r = analyze(x, rate, st)
            row.update(r)
            n = spoken_chars(s["audio_text"])
            row["rate"] = n / (r["duration"] / 60) if r["duration"] > 0 else None
            if not r["voiced"]:
                row["status"] = "failed"
                row["messages"].append("無音しかありません")
            if r["longest_silence"] > st["max_silence_sec"]:
                _warn(row, f"途中に {r['longest_silence']:.1f} 秒の無音があります")
            if r["tail_ratio"] > st["tail_ratio"]:
                _warn(row, "末尾で音が途切れているかもしれません")
            if r["clip_ratio"] > st["clip_ratio"]:
                _warn(row, f"音割れ {r['clip_ratio']:.2%}")
    rates = [r["rate"] for r in rows if r["rate"]]
    med = statistics.median(rates) if rates else 0
    for r in rows:
        if not r["rate"] or not med:
            continue
        r["rate_ratio"] = round(r["rate"] / med, 2)
        lo, hi = st["rate_fail"]
        wlo, whi = st["rate_warn"]
        what = "遅すぎます（余計な音や長い間が入った疑い）" if r["rate_ratio"] < 1 else "速すぎます（文が抜けた疑い）"
        if not lo <= r["rate_ratio"] <= hi:
            r["status"] = "failed"
            r["messages"].append(f"話す速さが中央値の {r['rate_ratio']:.2f} 倍で{what}")
        elif not wlo <= r["rate_ratio"] <= whi:
            _warn(r, f"話す速さが中央値の {r['rate_ratio']:.2f} 倍で{what}")
    for r in rows:
        r["median_rate"] = round(med, 1)
    return rows


def _warn(row: dict[str, Any], msg: str) -> None:
    if row["status"] == "passed":
        row["status"] = "warning"
    row["messages"].append(msg)


def probe_duration(f: Path) -> float | None:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(f)],
                       capture_output=True, text=True)
    try:
        return float(json.loads(r.stdout)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError):
        return None


def check_files(m: dict[str, Any], bdir: Path) -> list[str]:
    """章ごとの MP3 と全章結合版が、記録どおりにあるか。"""
    notes = []
    targets = [(c["chapter_id"], c.get("audio") or {}) for c in m["chapters"]] + [("全章結合版", m.get("audio") or {})]
    for name, a in targets:
        if a.get("status") != "done" or not a.get("file"):
            notes.append(f"{name}: MP3 がまだありません（{a.get('status', 'not_started')}）")
            continue
        f = bdir / a["file"]
        if not f.exists():
            notes.append(f"{name}: ファイルがありません: {a['file']}")
            continue
        d = probe_duration(f)
        if d is None:
            notes.append(f"{name}: MP3 を読めません: {a['file']}")
        elif a.get("duration_sec") and abs(d - a["duration_sec"]) > max(1.0, a["duration_sec"] * 0.005):
            notes.append(f"{name}: 長さが記録と違います（記録 {a['duration_sec']:.1f} 秒、実際 {d:.1f} 秒）")
    for c in m["chapters"]:
        a = c.get("audio") or {}
        if a.get("status") == "done" and a.get("generated_at"):
            newer = [s["section_id"] for s in c["sections"]
                     if (s["audio"].get("generated_at") or "") > a["generated_at"]]
            if newer:
                notes.append(f"{c['chapter_id']}: 章の MP3 より新しい節の音声があります（{', '.join(newer[:5])}）。build で作り直してください")
    return notes


def mark_stale(m: dict[str, Any], ids: set[str]) -> int:
    n = 0
    for c in m["chapters"]:
        for s in c["sections"]:
            if s["section_id"] in ids and s["audio"].get("status") == "done":
                s["audio"]["status"] = "stale"
                n += 1
    return n


def report_markdown(m: dict[str, Any], rows: list[dict[str, Any]], file_notes: list[str]) -> str:
    cnt = {k: sum(r["status"] == k for r in rows) for k in ("passed", "warning", "failed", "not_started")}
    med = rows[0]["median_rate"] if rows else 0
    out = [f"# 音声の品質確認: {m.get('title', '')}", "",
           f"節ごとの判定: 問題なし {cnt['passed']}、要確認 {cnt['warning']}、要作り直し {cnt['failed']}、音声なし {cnt['not_started']}",
           f"話す速さの中央値: 1分あたり {med:.0f} 字", ""]
    bad = [r for r in rows if r["status"] != "passed"]
    if bad:
        out += ["## 要確認・要作り直しの節", "", "| 節 | 判定 | 速さ（中央値比） | 内容 |", "| --- | --- | --- | --- |"]
        for r in bad:
            ratio = f"{r.get('rate_ratio', ''):}" if r.get("rate_ratio") else ""
            out.append(f"| {r['section_id']} {r['heading']} | {r['status']} | {ratio} | {' / '.join(r['messages'])} |")
        out.append("")
    out += ["## 章の MP3", ""] + ([f"- {n}" for n in file_notes] if file_notes else ["- すべて記録どおりにあります"]) + [""]
    return "\n".join(out) + "\n"
