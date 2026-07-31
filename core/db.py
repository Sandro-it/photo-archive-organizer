"""
db.py — SQLite-індекс файлів фотоархіву.

Зберігає для кожного файлу: шлях, розмір, хеш (SHA-256), дату
зйомки (якщо визначена) і джерело цієї дати, а також базові
атрибути зображення (ширина/висота, чи є EXIF) — потрібні
пізніше для модуля пошуку дублікатів.
"""

import sqlite3
from pathlib import Path
from contextlib import contextmanager


SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    size INTEGER,
    sha256 TEXT,
    phash TEXT,
    width INTEGER,
    height INTEGER,
    has_exif INTEGER DEFAULT 0,
    date_value TEXT,          -- ISO-рядок дати, яку визначили для файлу
    date_source TEXT,         -- 'exif' | 'filename' | 'parent_folder' | 'unresolved'
    scanned_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_files_sha256 ON files(sha256);
CREATE INDEX IF NOT EXISTS idx_files_phash ON files(phash);
CREATE INDEX IF NOT EXISTS idx_files_date ON files(date_value);

CREATE TABLE IF NOT EXISTS operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    op_type TEXT,              -- 'move' | 'copy' | 'rename' | 'delete_to_trash'
    src_path TEXT,
    dst_path TEXT,
    done_at TEXT DEFAULT CURRENT_TIMESTAMP,
    batch_id TEXT              -- групує операції одного прогону для масового відкату
);
"""


def get_db_path(dest_root: Path) -> Path:
    """Індекс зберігається біля кореня призначення, окремо для кожного архіву."""
    return dest_root / ".photosort_index.sqlite3"


@contextmanager
def open_db(db_path: Path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_file(conn, **fields):
    cols = ", ".join(fields.keys())
    placeholders = ", ".join(f":{k}" for k in fields.keys())
    updates = ", ".join(f"{k}=excluded.{k}" for k in fields.keys() if k != "path")
    conn.execute(
        f"INSERT INTO files ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT(path) DO UPDATE SET {updates}",
        fields,
    )


def log_operation(conn, op_type: str, src_path: str, dst_path: str, batch_id: str):
    conn.execute(
        "INSERT INTO operations (op_type, src_path, dst_path, batch_id) VALUES (?, ?, ?, ?)",
        (op_type, src_path, dst_path, batch_id),
    )


def get_batch_operations(conn, batch_id: str):
    return conn.execute(
        "SELECT * FROM operations WHERE batch_id = ? ORDER BY id DESC", (batch_id,)
    ).fetchall()
