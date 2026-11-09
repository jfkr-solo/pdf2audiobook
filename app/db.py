"""SQLite による教材・処理履歴の管理（後で Supabase に差し替え可能な最小API）。"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    book_id     TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    author      TEXT,
    source_pdf  TEXT NOT NULL,
    page_count  INTEGER,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id     TEXT NOT NULL REFERENCES books(book_id),
    step        TEXT NOT NULL,          -- extract / structure / script / tts / build
    target      TEXT,                   -- 章・節ID（全体なら NULL）
    status      TEXT NOT NULL,          -- running / done / failed
    message     TEXT,
    started_at  TEXT NOT NULL,
    finished_at TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def connect(path: str | Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def next_book_id(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT book_id FROM books ORDER BY book_id DESC LIMIT 1").fetchone()
    n = int(row["book_id"][4:]) + 1 if row else 1
    return f"BOOK{n:03d}"


def upsert_book(conn, book_id, title, source_pdf, page_count=None, author=None) -> None:
    conn.execute(
        """INSERT INTO books(book_id,title,author,source_pdf,page_count,created_at)
           VALUES(?,?,?,?,?,?)
           ON CONFLICT(book_id) DO UPDATE SET title=excluded.title, author=excluded.author,
             source_pdf=excluded.source_pdf, page_count=excluded.page_count""",
        (book_id, title, author, str(source_pdf), page_count, now()),
    )
    conn.commit()


def start_job(conn, book_id, step, target=None) -> int:
    cur = conn.execute(
        "INSERT INTO jobs(book_id,step,target,status,started_at) VALUES(?,?,?,?,?)",
        (book_id, step, target, "running", now()),
    )
    conn.commit()
    return cur.lastrowid


def finish_job(conn, job_id, status="done", message=None) -> None:
    conn.execute(
        "UPDATE jobs SET status=?, message=?, finished_at=? WHERE id=?",
        (status, message, now(), job_id),
    )
    conn.commit()
