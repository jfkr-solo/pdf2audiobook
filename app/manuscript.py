"""読み上げ原稿 JSON の保存・読み込み・Schema 検証（STEP 1 時点では検証のみ実装）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .config import ROOT

SCHEMA_PATH = ROOT / "schemas" / "manuscript.schema.json"


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate(doc: dict[str, Any]) -> list[str]:
    """エラーメッセージのリストを返す（空なら妥当）。"""
    v = Draft202012Validator(load_schema())
    errors = sorted(v.iter_errors(doc), key=lambda e: list(e.absolute_path))
    return [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}" for e in errors]


def save(doc: dict[str, Any], path: str | Path) -> None:
    errors = validate(doc)
    if errors:
        raise ValueError("原稿JSONがスキーマに適合しません:\n" + "\n".join(errors))
    p = Path(path)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)  # 書き込み途中で落ちても既存ファイルを壊さない


def load(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
