"""macOS 標準の say コマンド（無料・確認用）。話者は Kyoko / Otoya など（`say -v '?'` で一覧）。"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .base import TTSEngine, TTSError


class MacSay(TTSEngine):
    name = "macos_say"

    def available(self) -> tuple[bool, str]:
        if shutil.which("say"):
            return True, ""
        return False, "say コマンドがありません（macOS でのみ使えます）"

    def synthesize(self, text: str, settings: dict[str, Any]) -> bytes:
        rate = int(settings.get("sample_rate", 24000))
        # say の速さは 1分あたりの語数。日本語の標準はおよそ 175〜200
        wpm = int(185 * float(settings.get("speed", 1.0)))
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.wav"
            cmd = ["say", "-v", str(settings.get("speaker") or "Kyoko"), "-r", str(wpm),
                   "--file-format=WAVE", f"--data-format=LEI16@{rate}", "--channels=1", "-o", str(out), text]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0 or not out.exists():
                raise TTSError(f"say が失敗しました: {r.stderr.strip()[:200]}")
            return out.read_bytes()
