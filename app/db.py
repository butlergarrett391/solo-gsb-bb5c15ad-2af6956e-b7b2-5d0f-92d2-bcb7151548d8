"""SQLite 访问层：连接获取与建表初始化。"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = "data/lab.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    kind       TEXT NOT NULL CHECK (kind IN ('sample', 'pool')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS pool_recipes (
    pool_id       INTEGER NOT NULL REFERENCES batches (id) ON DELETE CASCADE,
    ingredient_id INTEGER NOT NULL REFERENCES batches (id),
    portions      INTEGER NOT NULL CHECK (portions > 0),
    PRIMARY KEY (pool_id, ingredient_id)
);

CREATE TABLE IF NOT EXISTS exclusion_rules (
    -- 禁配无序对归一为 (low, high)，主键天然阻止重复登记同一对
    low_sample_id  INTEGER NOT NULL REFERENCES batches (id),
    high_sample_id INTEGER NOT NULL REFERENCES batches (id),
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (low_sample_id, high_sample_id),
    CHECK (low_sample_id < high_sample_id)
);
"""


def database_path() -> str:
    """数据库文件路径，可通过环境变量 DATABASE_PATH 覆盖。"""
    return os.environ.get("DATABASE_PATH", DEFAULT_DB_PATH)


def connect() -> sqlite3.Connection:
    # check_same_thread=False：FastAPI/Starlette 的线程池可能让同一请求的
    # 依赖初始化与收尾落在不同工作线程上；每个请求使用独立连接，写事务再由
    # BEGIN IMMEDIATE 串行化，跨线程使用同一连接不存在并发安全问题
    conn = sqlite3.connect(database_path(), timeout=5.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # 写事务相互冲突时等待对方提交（而不是立即报 database is locked），
    # 配合 BEGIN IMMEDIATE 保证规则登记与混样创建串行化
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db() -> None:
    """建表（幂等），应用启动时调用。"""
    path = Path(database_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
