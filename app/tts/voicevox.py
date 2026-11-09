"""VOICEVOX エンジン（無料・高品質）。アプリかエンジン（Docker）を起動しておき、HTTP API で合成する。

speaker は話者のスタイルID（例: 13 = 青山龍星 ノーマル）。`GET /speakers` で一覧を確認できる。
音声を公開・配布するときは、話者ごとのクレジット表記と利用規約に従うこと。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .base import TTSEngine, TTSError


class Voicevox(TTSEngine):
    name = "voicevox"

    def __init__(self, url: str = "http://127.0.0.1:50021"):
        self.url = url.rstrip("/")

    def _req(self, path: str, params: dict, body: bytes | None = None, timeout: int = 120) -> bytes:
        req = urllib.request.Request(f"{self.url}{path}?{urllib.parse.urlencode(params)}", data=body or b"",
                                     method="POST", headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.URLError as e:
            raise TTSError(f"VOICEVOX に接続できません（{self.url}）: {e}") from None

    def available(self) -> tuple[bool, str]:
        try:
            with urllib.request.urlopen(f"{self.url}/version", timeout=3) as r:
                return True, r.read().decode().strip('"')
        except Exception as e:  # noqa: BLE001
            return False, f"VOICEVOX エンジンが {self.url} で動いていません（{e}）"

    def synthesize(self, text: str, settings: dict[str, Any]) -> bytes:
        speaker = int(settings.get("speaker") or 13)
        query = json.loads(self._req("/audio_query", {"text": text, "speaker": speaker}))
        query["speedScale"] = float(settings.get("speed", 1.0))
        query["pitchScale"] = float(settings.get("pitch", 0.0)) * 0.15  # 設定の -1〜1 を VOICEVOX の ±0.15 に
        query["volumeScale"] = float(settings.get("volume", 1.0))
        query["outputSamplingRate"] = int(settings.get("sample_rate", 24000))
        query["outputStereo"] = False
        query["prePhonemeLength"] = 0.05
        query["postPhonemeLength"] = 0.05
        return self._req("/synthesis", {"speaker": speaker}, json.dumps(query).encode())
