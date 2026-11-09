"""STEP 10: 本の一覧・章ごとの再生・処理状況を見る Web 画面。

起動: uvicorn app.web.main:create_app --factory --host 0.0.0.0 --port 8000
環境変数 APP_PASSWORD を設定すると、ベーシック認証（ユーザー名は何でもよい）で保護する。
外部に公開するときは必ず設定する（書籍の音声は私的使用の範囲に留めるため）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from xml.sax.saxutils import escape, quoteattr

from ..audio import chapter_label
from ..config import data_dir, load_config

HERE = Path(__file__).resolve().parent


def fmt_duration(sec: float | None) -> str:
    sec = int(sec or 0)
    h, m = divmod(sec // 60, 60)
    return f"{h}時間{m:02d}分" if h else f"{m}分"


def load_books(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    books = []
    for f in sorted(data_dir(cfg).glob("*/manuscript.json")):
        try:
            m = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        books.append(summarize(m, f.parent))
    return books


def summarize(m: dict[str, Any], bdir: Path) -> dict[str, Any]:
    chapters = []
    for c in m["chapters"]:
        a = c.get("audio") or {}
        f = bdir / a["file"] if a.get("file") else None
        chapters.append({"id": c["chapter_id"], "title": chapter_label(c), "sections": len(c["sections"]),
                         "file": f.name if a.get("status") == "done" and f and f.exists() else None,
                         "duration": a.get("duration_sec"), "duration_text": fmt_duration(a.get("duration_sec"))})
    secs = [s for c in m["chapters"] for s in c["sections"]]
    count = lambda key, val: sum((s.get(key) or {}).get("status") == val for s in secs)  # noqa: E731
    cover = next((f.name for f in (bdir / "cover.jpg", bdir / "cover.png") if f.exists()), None)
    whole = m.get("audio") or {}
    playable = [c for c in chapters if c["file"]]
    total = sum(c["duration"] or 0 for c in playable)
    return {
        "id": m["book_id"], "title": m.get("title", m["book_id"]), "author": m.get("author") or "",
        "chapters": chapters, "playable": len(playable), "total_text": fmt_duration(total),
        "whole_file": Path(whole["file"]).name if whole.get("status") == "done" and whole.get("file") else None,
        "voice": (m.get("voice_settings") or {}).get("engine", ""),
        "cover": cover,
        "hue": int(hashlib.md5(m["book_id"].encode()).hexdigest()[:4], 16) % 360,  # 表紙画像が無いときの色
        "total": total,
        "files": [{"file": c["file"], "title": c["title"], "duration": c["duration"] or 0} for c in playable],
        "status": {
            "sections": len(secs),
            "text": {k: count("validation", k) for k in ("passed", "warning", "failed", "pending")},
            "audio": {k: count("audio", k) for k in ("done", "stale", "failed", "not_started")},
        },
    }


def feed_token(password: str) -> str:
    """RSS とその音声の URL に付ける合言葉。ポッドキャストアプリはパスワード入力が苦手なため。
    APP_FEED_TOKEN があればそれを、無ければパスワードから作る（パスワードを変えれば無効になる）。"""
    return os.environ.get("APP_FEED_TOKEN") or hmac.new(password.encode(), b"pdf2audiobook-feed", hashlib.sha256).hexdigest()[:32]


def rss_xml(m: dict[str, Any], bdir: Path, base_url: str, token: str) -> str:
    """章を1話ずつにしたポッドキャスト RSS（itunes:type serial で1章から順に並ぶ）。"""
    from datetime import datetime, timedelta
    from email.utils import format_datetime
    from urllib.parse import quote

    q = f"?t={token}" if token else ""
    base = base_url.rstrip("/")
    try:
        start = datetime.fromisoformat(m.get("created_at") or "")
    except ValueError:
        start = datetime(2026, 1, 1).astimezone()
    if start.tzinfo is None:
        start = start.astimezone()
    title = m.get("title", m["book_id"])
    author = m.get("author") or ""
    items = []
    for i, c in enumerate(m["chapters"], 1):
        a = c.get("audio") or {}
        f = bdir / a["file"] if a.get("file") else None
        if a.get("status") != "done" or f is None or not f.exists():
            continue
        url = f"{base}/media/{quote(m['book_id'])}/{quote(f.name)}{q}"
        items.append(f"""    <item>
      <title>{escape(chapter_label(c))}</title>
      <guid isPermaLink="false">{escape(f"{m['book_id']}-{c['chapter_id']}-{a.get('text_hash') or ''}")}</guid>
      <enclosure url={quoteattr(url)} length="{f.stat().st_size}" type="audio/mpeg"/>
      <pubDate>{format_datetime(start + timedelta(hours=i))}</pubDate>
      <itunes:episode>{i}</itunes:episode>
      <itunes:duration>{int(a.get("duration_sec") or 0)}</itunes:duration>
    </item>""")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>{escape(title)}</title>
    <link>{escape(f"{base}/books/{m['book_id']}")}</link>
    <description>{escape(f"{title}{'（' + author + '）' if author else ''} の読み上げ音声。私的利用のみ。")}</description>
    <language>ja</language>
    <itunes:author>{escape(author)}</itunes:author>
    <itunes:type>serial</itunes:type>
    <itunes:block>Yes</itunes:block>
    <itunes:explicit>false</itunes:explicit>
{chr(10).join(items)}
  </channel>
</rss>
"""


def create_app(cfg: dict[str, Any] | None = None) -> FastAPI:
    cfg = cfg or load_config()
    app = FastAPI(title="pdf2audiobook", docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    password = os.environ.get("APP_PASSWORD", "")
    token = feed_token(password) if password else ""

    @app.middleware("http")
    async def basic_auth(request: Request, call_next):
        if not password or request.url.path == "/healthz":
            return await call_next(request)
        path = request.url.path
        if path.startswith(("/feed/", "/media/")) and secrets.compare_digest(
                request.query_params.get("t", "").encode(), token.encode()):
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        ok = False
        if auth.lower().startswith("basic "):
            try:
                _, _, pw = base64.b64decode(auth[6:]).decode().partition(":")
                ok = secrets.compare_digest(pw.encode(), password.encode())
            except (ValueError, UnicodeDecodeError):
                ok = False
        if not ok:
            return Response("認証が必要です", status_code=401, headers={"WWW-Authenticate": 'Basic realm="pdf2audiobook"'})
        return await call_next(request)

    def book_or_404(book_id: str) -> tuple[dict[str, Any], Path]:
        if not book_id.isalnum():
            raise HTTPException(404)
        bdir = data_dir(cfg) / book_id
        f = bdir / "manuscript.json"
        if not f.exists():
            raise HTTPException(404, "本が見つかりません")
        return summarize(json.loads(f.read_text(encoding="utf-8")), bdir), bdir

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return templates.TemplateResponse(request, "index.html", {"books": load_books(cfg)})

    @app.get("/books/{book_id}", response_class=HTMLResponse)
    def book(request: Request, book_id: str):
        b, _ = book_or_404(book_id)
        return templates.TemplateResponse(request, "book.html", {"b": b})

    @app.get("/books/{book_id}/status", response_class=HTMLResponse)
    def status(request: Request, book_id: str):
        b, bdir = book_or_404(book_id)
        jobs = []
        db = Path(cfg["_root"]) / cfg.get("db_path", "data/pdf2audiobook.db")
        if db.exists():
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            jobs = [dict(r) for r in conn.execute(
                "SELECT step, status, message, started_at FROM jobs WHERE book_id=? ORDER BY id DESC LIMIT 20", (book_id,))]
            conn.close()
        reports = [p.name for p in sorted(bdir.glob("*_report.md"))]
        feed = f"{public_base(request)}/feed/{book_id}.xml" + (f"?t={token}" if token else "")
        return templates.TemplateResponse(request, "status.html", {"b": b, "jobs": jobs, "reports": reports, "feed": feed})

    def public_base(request: Request) -> str:
        # リバースプロキシの内側でも正しい URL を出せるよう、APP_BASE_URL があれば優先する
        return os.environ.get("APP_BASE_URL") or str(request.base_url).rstrip("/")

    @app.get("/feed/{book_id}.xml")
    def feed(request: Request, book_id: str):
        _, bdir = book_or_404(book_id)
        m = json.loads((bdir / "manuscript.json").read_text(encoding="utf-8"))
        return Response(rss_xml(m, bdir, public_base(request), token), media_type="application/rss+xml; charset=utf-8")

    @app.get("/cover/{book_id}")
    def cover(book_id: str):
        b, bdir = book_or_404(book_id)
        if not b["cover"]:
            raise HTTPException(404)
        return FileResponse(bdir / b["cover"])

    @app.get("/media/{book_id}/{name}")
    def media(book_id: str, name: str):
        _, bdir = book_or_404(book_id)
        f = (bdir / "audio" / name).resolve()
        if f.parent != (bdir / "audio").resolve() or f.suffix != ".mp3" or not f.exists():
            raise HTTPException(404)
        return FileResponse(f, media_type="audio/mpeg")  # Range 要求に対応（シーク・続きから再生）

    return app

