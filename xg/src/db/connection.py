"""
SQLite connection manager.
Handles schema initialization and provides a context-managed connection.

On Vercel (env var VERCEL=1) the DB is opened read-only — writes raise.
The pipeline runs in GitHub Actions and commits the updated db/xpts.db.
"""

import sqlite3
import os
from src.config import DB_PATH

_SCHEMA_PATH = os.path.join(os.path.dirname(DB_PATH), "schema.sql")
READ_ONLY = bool(os.environ.get("VERCEL"))


def get_connection() -> sqlite3.Connection:
    """Get a SQLite connection. Read-only on Vercel; read-write locally."""
    if READ_ONLY:
        uri = f"file:{DB_PATH}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create all tables from schema.sql if they don't exist."""
    if READ_ONLY:
        raise RuntimeError("init_db is unavailable in read-only Vercel mode")
    with open(_SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema = f.read()
    conn = get_connection()
    conn.executescript(schema)
    conn.close()
    print("[db] Schema initialized")


def query(sql: str, params: tuple = (), one: bool = False):
    """Run a SELECT query and return results as list of dicts (or one dict)."""
    conn = get_connection()
    try:
        cur = conn.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        return rows[0] if one and rows else rows if not one else None
    finally:
        conn.close()


def execute(sql: str, params: tuple = ()) -> int:
    """Run an INSERT/UPDATE/DELETE and return lastrowid."""
    if READ_ONLY:
        raise RuntimeError("execute is unavailable in read-only Vercel mode")
    conn = get_connection()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def execute_many(sql: str, param_list: list):
    """Run a batch INSERT/UPDATE."""
    if READ_ONLY:
        raise RuntimeError("execute_many is unavailable in read-only Vercel mode")
    conn = get_connection()
    try:
        conn.executemany(sql, param_list)
        conn.commit()
    finally:
        conn.close()
