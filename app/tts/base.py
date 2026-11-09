"""交換可能な TTS エンジンの共通部分。

どのエンジンも synthesize(text, settings) で「モノラル16bit PCM の WAV（bytes）」を返す。
サンプルレートは settings["sample_rate"]（既定 24000Hz）にそろえるので、そのまま連結できる。
"""
from __future__ import annotations

import io
import wave
from typing import Any


class TTSError(RuntimeError):
    pass


class TTSEngine:
    name = "base"

    def synthesize(self, text: str, settings: dict[str, Any]) -> bytes:  # pragma: no cover
        raise NotImplementedError

    def available(self) -> tuple[bool, str]:
        """使える状態か（コマンドやサーバーがあるか）と、その説明。"""
        return True, ""


def wav_params(data: bytes) -> tuple[int, int, int, bytes]:
    """(チャンネル数, サンプル幅, サンプルレート, PCM) を返す。"""
    with wave.open(io.BytesIO(data)) as w:
        return w.getnchannels(), w.getsampwidth(), w.getframerate(), w.readframes(w.getnframes())


def make_wav(pcm: bytes, rate: int, channels: int = 1, width: int = 2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def silence(ms: int, rate: int, width: int = 2) -> bytes:
    return b"\x00" * (int(rate * ms / 1000) * width)


def concat(parts: list[bytes], pause_ms: int, rate: int) -> bytes:
    """WAV の列を、間に無音を挟んでつなぐ。形式が違う WAV が来たらエラーにする（音が壊れるのを防ぐ）。"""
    pcm = []
    for i, p in enumerate(parts):
        ch, width, r, frames = wav_params(p)
        if (ch, width, r) != (1, 2, rate):
            raise TTSError(f"WAV の形式が違います（{ch}ch, {width * 8}bit, {r}Hz。期待は 1ch, 16bit, {rate}Hz）")
        if i:
            pcm.append(silence(pause_ms, rate))
        pcm.append(frames)
    return make_wav(b"".join(pcm), rate)


def duration_sec(data: bytes) -> float:
    with wave.open(io.BytesIO(data)) as w:
        return w.getnframes() / w.getframerate()


def get_engine(name: str, cfg: dict[str, Any] | None = None) -> TTSEngine:
    if name == "macos_say":
        from .macos_say import MacSay
        return MacSay()
    if name == "voicevox":
        from .voicevox import Voicevox
        return Voicevox((cfg or {}).get("tts", {}).get("voicevox_url", "http://127.0.0.1:50021"))
    if name == "openjtalk":
        from .openjtalk import OpenJTalk
        return OpenJTalk()
    if name == "google":
        from .google import GoogleTTS
        return GoogleTTS()
    raise ValueError(f"未対応の TTS エンジン: {name}")
