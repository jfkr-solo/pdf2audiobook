"""設定ファイル（config.yaml）と秘密情報（.env）の読み込み。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """python-dotenv が無くても動く最小実装。既存の環境変数は上書きしない。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    cfg_path = Path(path) if path else ROOT / "config.yaml"
    with open(cfg_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    _load_dotenv(ROOT / ".env")
    cfg["_root"] = str(ROOT)
    return cfg


def data_dir(cfg: dict[str, Any]) -> Path:
    p = Path(cfg.get("data_dir", "data"))
    return p if p.is_absolute() else ROOT / p


def book_dir(cfg: dict[str, Any], book_id: str) -> Path:
    d = data_dir(cfg) / book_id
    d.mkdir(parents=True, exist_ok=True)
    return d
