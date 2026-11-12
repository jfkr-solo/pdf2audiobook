"""Google Cloud Text-to-Speech（クラウド・高品質・有料。毎月の無料枠あり）。

.env に GOOGLE_TTS_API_KEY（Cloud Text-to-Speech API を有効にしたプロジェクトの APIキー）を書く。
speaker は音声名（例: ja-JP-Neural2-B）。
"""
from __future__ import annotations

import base64
import math
import json
import os
import urllib.error
import urllib.request
from typing import Any

from .base import TTSEngine, TTSError

URL = "https://texttospeech.googleapis.com/v1/text:synthesize"


class GoogleTTS(TTSEngine):
    name = "google"
    ssml = True  # "<speak>" で始まる文章は SSML として送る

    def available(self) -> tuple[bool, str]:
        if os.environ.get("GOOGLE_TTS_API_KEY"):
            return True, ""
        return False, "GOOGLE_TTS_API_KEY が .env にありません"

    def synthesize(self, text: str, settings: dict[str, Any]) -> bytes:
        key = os.environ.get("GOOGLE_TTS_API_KEY")
        if not key:
            raise TTSError("GOOGLE_TTS_API_KEY が .env にありません")
        body = {
            "input": {"ssml": text} if text.lstrip().startswith("<speak>") else {"text": text},
            "voice": {"languageCode": "ja-JP", "name": str(settings.get("speaker") or "ja-JP-Neural2-B")},
            "audioConfig": {
                "audioEncoding": "LINEAR16",
                "sampleRateHertz": int(settings.get("sample_rate", 24000)),
                "speakingRate": float(settings.get("speed", 1.0)),
                "pitch": float(settings.get("pitch", 0.0)) * 10,  # -1〜1 を ±10 半音に
                "volumeGainDb": 20 * math.log10(max(float(settings.get("volume", 1.0)), 0.01)),
            },
        }
        req = urllib.request.Request(f"{URL}?key={key}", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                res = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise TTSError(f"Google TTS HTTP {e.code}: {e.read().decode(errors='replace')[:200]}") from None
        return base64.b64decode(res["audioContent"])  # LINEAR16 は WAV ヘッダー付きで返る
