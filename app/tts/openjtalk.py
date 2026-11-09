"""Open JTalk（pyopenjtalk-plus）による音声合成。無料・オフラインで、Linux のクラウド環境でも動く。
声は機械的なので、主に処理の確認用・下書き用。`pip install pyopenjtalk-plus` が必要。"""
from __future__ import annotations

from typing import Any

from .base import TTSEngine, TTSError, make_wav


class OpenJTalk(TTSEngine):
    name = "openjtalk"

    def available(self) -> tuple[bool, str]:
        try:
            import pyopenjtalk  # noqa: F401
        except ImportError:
            return False, "pyopenjtalk-plus が入っていません（pip install pyopenjtalk-plus）"
        return True, ""

    def synthesize(self, text: str, settings: dict[str, Any]) -> bytes:
        import numpy as np
        import pyopenjtalk

        rate = int(settings.get("sample_rate", 24000))
        try:
            x, sr = pyopenjtalk.tts(text, speed=float(settings.get("speed", 1.0)),
                                    half_tone=float(settings.get("pitch", 0.0)))
        except Exception as e:  # noqa: BLE001
            raise TTSError(f"Open JTalk が失敗しました: {e}") from e
        x = np.asarray(x, dtype=np.float64) * float(settings.get("volume", 1.0))
        if sr != rate:  # 48kHz → 設定のサンプリング周波数へ（線形補間）
            n = int(round(len(x) * rate / sr))
            x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)
        pcm = np.clip(x, -32768, 32767).astype("<i2").tobytes()
        return make_wav(pcm, rate)
