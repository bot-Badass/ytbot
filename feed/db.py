"""Сховище прикорму: sqlite, одна родина = один профіль дитини на кількох дорослих."""
from __future__ import annotations

import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(__file__).resolve().parent.parent / "var" / "feed.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS family (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS member (
    user_id     INTEGER PRIMARY KEY,
    family_id   INTEGER NOT NULL REFERENCES family(id),
    name        TEXT NOT NULL DEFAULT '',
    added_at    INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS child (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    family_id   INTEGER NOT NULL REFERENCES family(id),
    name        TEXT NOT NULL,
    birth_date  TEXT NOT NULL,           -- YYYY-MM-DD
    sex         TEXT NOT NULL            -- girls | boys
);
CREATE TABLE IF NOT EXISTS invite (
    code        TEXT PRIMARY KEY,
    family_id   INTEGER NOT NULL REFERENCES family(id),
    created_at  INTEGER NOT NULL,
    used_by     INTEGER
);
CREATE TABLE IF NOT EXISTS meal (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id    INTEGER NOT NULL REFERENCES child(id),
    ts          INTEGER NOT NULL,
    kind        TEXT NOT NULL,           -- breakfast | lunch | dinner | snack
    note        TEXT NOT NULL DEFAULT '',
    by_user     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS meal_child_ts ON meal(child_id, ts);
CREATE TABLE IF NOT EXISTS meal_item (
    meal_id     INTEGER NOT NULL REFERENCES meal(id) ON DELETE CASCADE,
    code        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS meal_item_meal ON meal_item(meal_id);
CREATE TABLE IF NOT EXISTS intro (
    child_id    INTEGER NOT NULL REFERENCES child(id),
    code        TEXT NOT NULL,
    first_ts    INTEGER NOT NULL,
    last_ts     INTEGER NOT NULL,
    times       INTEGER NOT NULL DEFAULT 1,
    status      TEXT NOT NULL DEFAULT 'trying',   -- trying | ok | watch | avoid
    PRIMARY KEY (child_id, code)
);
CREATE TABLE IF NOT EXISTS reaction (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id    INTEGER NOT NULL REFERENCES child(id),
    code        TEXT NOT NULL,
    ts          INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    by_user     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS reaction_child ON reaction(child_id, ts);
CREATE TABLE IF NOT EXISTS measure (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id    INTEGER NOT NULL REFERENCES child(id),
    ts          INTEGER NOT NULL,
    weight_kg   REAL,
    height_cm   REAL,
    by_user     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS measure_child ON measure(child_id, ts);
CREATE TABLE IF NOT EXISTS kv (
    family_id   INTEGER NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    PRIMARY KEY (family_id, key)
);
CREATE TABLE IF NOT EXISTS nudge (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id    INTEGER NOT NULL,
    code        TEXT NOT NULL,
    due_ts      INTEGER NOT NULL,
    kind        TEXT NOT NULL,           -- reaction_check | three_day
    done        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS nudge_due ON nudge(done, due_ts);
CREATE TABLE IF NOT EXISTS pantry (
    family_id   INTEGER NOT NULL REFERENCES family(id),
    code        TEXT NOT NULL,
    added_ts    INTEGER NOT NULL,
    by_user     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (family_id, code)
);
"""

_conn: sqlite3.Connection | None = None


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.executescript(SCHEMA)
        _conn.commit()
    return _conn


def q(sql: str, args: Iterable[Any] = ()) -> list[sqlite3.Row]:
    return conn().execute(sql, tuple(args)).fetchall()


def q1(sql: str, args: Iterable[Any] = ()) -> sqlite3.Row | None:
    rows = q(sql, args)
    return rows[0] if rows else None


def run(sql: str, args: Iterable[Any] = ()) -> int:
    cur = conn().execute(sql, tuple(args))
    conn().commit()
    return cur.lastrowid


def now() -> int:
    return int(time.time())


# ─────────────────────── родина і дитина ───────────────────────


def member(user_id: int) -> sqlite3.Row | None:
    return q1("SELECT * FROM member WHERE user_id=?", (user_id,))


def create_family(user_id: int, name: str) -> int:
    family_id = run("INSERT INTO family(created_at) VALUES(?)", (now(),))
    run("INSERT INTO member(user_id, family_id, name, added_at) VALUES(?,?,?,?)",
        (user_id, family_id, name, now()))
    return family_id


def join_family(user_id: int, family_id: int, name: str) -> None:
    run("INSERT OR REPLACE INTO member(user_id, family_id, name, added_at) VALUES(?,?,?,?)",
        (user_id, family_id, name, now()))


def family_members(family_id: int) -> list[sqlite3.Row]:
    return q("SELECT * FROM member WHERE family_id=? ORDER BY added_at", (family_id,))


def new_invite(family_id: int) -> str:
    code = secrets.token_hex(3).upper()
    run("INSERT INTO invite(code, family_id, created_at) VALUES(?,?,?)", (code, family_id, now()))
    return code


def use_invite(code: str, user_id: int, name: str) -> int | None:
    row = q1("SELECT * FROM invite WHERE code=? AND used_by IS NULL", (code.strip().upper(),))
    if not row:
        return None
    join_family(user_id, row["family_id"], name)
    run("UPDATE invite SET used_by=? WHERE code=?", (user_id, row["code"]))
    return row["family_id"]


def add_child(family_id: int, name: str, birth_date: str, sex: str) -> int:
    return run("INSERT INTO child(family_id, name, birth_date, sex) VALUES(?,?,?,?)",
               (family_id, name, birth_date, sex))


def child_of(user_id: int) -> sqlite3.Row | None:
    return q1(
        "SELECT c.* FROM child c JOIN member m ON m.family_id=c.family_id "
        "WHERE m.user_id=? ORDER BY c.id LIMIT 1",
        (user_id,),
    )


# ─────────────────────── прийоми їжі ───────────────────────


def add_meal(child_id: int, kind: str, codes: list[str], by_user: int,
             note: str = "", ts: int | None = None) -> int:
    ts = ts or now()
    meal_id = run("INSERT INTO meal(child_id, ts, kind, note, by_user) VALUES(?,?,?,?,?)",
                  (child_id, ts, kind, note, by_user))
    for code in codes:
        run("INSERT INTO meal_item(meal_id, code) VALUES(?,?)", (meal_id, code))
        touch_intro(child_id, code, ts)
    return meal_id


def touch_intro(child_id: int, code: str, ts: int) -> bool:
    """Позначає продукт як спробуваний. True якщо це перше знайомство."""
    row = q1("SELECT * FROM intro WHERE child_id=? AND code=?", (child_id, code))
    if row:
        run("UPDATE intro SET last_ts=?, times=times+1 WHERE child_id=? AND code=?",
            (ts, child_id, code))
        return False
    run("INSERT INTO intro(child_id, code, first_ts, last_ts, times) VALUES(?,?,?,?,1)",
        (child_id, code, ts, ts))
    return True


def intro_map(child_id: int) -> dict[str, sqlite3.Row]:
    return {r["code"]: r for r in q("SELECT * FROM intro WHERE child_id=?", (child_id,))}


def set_status(child_id: int, code: str, status: str) -> None:
    run("UPDATE intro SET status=? WHERE child_id=? AND code=?", (status, child_id, code))


def meals_between(child_id: int, ts_from: int, ts_to: int) -> list[sqlite3.Row]:
    return q("SELECT * FROM meal WHERE child_id=? AND ts>=? AND ts<? ORDER BY ts",
             (child_id, ts_from, ts_to))


def meal_codes(meal_id: int) -> list[str]:
    return [r["code"] for r in q("SELECT code FROM meal_item WHERE meal_id=?", (meal_id,))]


def recent_codes(child_id: int, limit: int = 8) -> list[str]:
    rows = q(
        "SELECT code FROM intro WHERE child_id=? AND status!='avoid' "
        "ORDER BY times DESC, last_ts DESC LIMIT ?",
        (child_id, limit),
    )
    return [r["code"] for r in rows]


def delete_meal(meal_id: int) -> None:
    run("DELETE FROM meal_item WHERE meal_id=?", (meal_id,))
    run("DELETE FROM meal WHERE id=?", (meal_id,))


# ─────────────────────── реакції ───────────────────────


def add_reaction(child_id: int, code: str, kind: str, by_user: int, note: str = "") -> int:
    rid = run("INSERT INTO reaction(child_id, code, ts, kind, note, by_user) VALUES(?,?,?,?,?,?)",
              (child_id, code, now(), kind, note, by_user))
    run("UPDATE intro SET status='watch' WHERE child_id=? AND code=? AND status!='avoid'",
        (child_id, code))
    return rid


def reactions(child_id: int, limit: int = 30) -> list[sqlite3.Row]:
    return q("SELECT * FROM reaction WHERE child_id=? ORDER BY ts DESC LIMIT ?", (child_id, limit))


# ─────────────────────── вимірювання ───────────────────────


def add_measure(child_id: int, by_user: int, weight_kg: float | None = None,
                height_cm: float | None = None) -> int:
    return run("INSERT INTO measure(child_id, ts, weight_kg, height_cm, by_user) VALUES(?,?,?,?,?)",
               (child_id, now(), weight_kg, height_cm, by_user))


def measures(child_id: int, limit: int = 20) -> list[sqlite3.Row]:
    return q("SELECT * FROM measure WHERE child_id=? ORDER BY ts DESC LIMIT ?", (child_id, limit))


def last_measure(child_id: int, field: str) -> sqlite3.Row | None:
    return q1(f"SELECT * FROM measure WHERE child_id=? AND {field} IS NOT NULL "
              "ORDER BY ts DESC LIMIT 1", (child_id,))


# ─────────────────────── налаштування і нагадування ───────────────────────


def get_kv(family_id: int, key: str, default: str = "") -> str:
    row = q1("SELECT value FROM kv WHERE family_id=? AND key=?", (family_id, key))
    return row["value"] if row else default


def set_kv(family_id: int, key: str, value: str) -> None:
    run("INSERT INTO kv(family_id, key, value) VALUES(?,?,?) "
        "ON CONFLICT(family_id, key) DO UPDATE SET value=excluded.value",
        (family_id, key, value))


def add_nudge(child_id: int, code: str, due_ts: int, kind: str) -> None:
    exists = q1("SELECT id FROM nudge WHERE child_id=? AND code=? AND kind=? AND done=0",
                (child_id, code, kind))
    if not exists:
        run("INSERT INTO nudge(child_id, code, due_ts, kind) VALUES(?,?,?,?)",
            (child_id, code, due_ts, kind))


def due_nudges(ts: int) -> list[sqlite3.Row]:
    return q("SELECT * FROM nudge WHERE done=0 AND due_ts<=? ORDER BY due_ts", (ts,))


def close_nudge(nudge_id: int) -> None:
    run("UPDATE nudge SET done=1 WHERE id=?", (nudge_id,))


def close_nudges_for(child_id: int, code: str) -> None:
    run("UPDATE nudge SET done=1 WHERE child_id=? AND code=? AND done=0", (child_id, code))


def family_chats(family_id: int) -> list[int]:
    return [r["user_id"] for r in family_members(family_id)]


# ─────────────────────── холодильник ───────────────────────


def pantry(family_id: int) -> list[sqlite3.Row]:
    return q("SELECT * FROM pantry WHERE family_id=? ORDER BY added_ts", (family_id,))


def pantry_codes(family_id: int) -> list[str]:
    return [r["code"] for r in pantry(family_id)]


def pantry_add(family_id: int, codes: Iterable[str], by_user: int = 0) -> list[str]:
    """Кладе продукти в холодильник. Повертає ті, яких там ще не було."""
    have = set(pantry_codes(family_id))
    fresh = [c for c in dict.fromkeys(codes) if c not in have]
    for code in fresh:
        run("INSERT OR IGNORE INTO pantry(family_id, code, added_ts, by_user) VALUES(?,?,?,?)",
            (family_id, code, now(), by_user))
    return fresh


def pantry_remove(family_id: int, code: str) -> None:
    run("DELETE FROM pantry WHERE family_id=? AND code=?", (family_id, code))


def pantry_toggle(family_id: int, code: str, by_user: int = 0) -> bool:
    """True якщо продукт поклали, False якщо забрали."""
    if q1("SELECT code FROM pantry WHERE family_id=? AND code=?", (family_id, code)):
        pantry_remove(family_id, code)
        return False
    pantry_add(family_id, [code], by_user)
    return True


def pantry_clear(family_id: int) -> None:
    run("DELETE FROM pantry WHERE family_id=?", (family_id,))


def pantry_since(family_id: int, code: str) -> int:
    row = q1("SELECT added_ts FROM pantry WHERE family_id=? AND code=?", (family_id, code))
    return row["added_ts"] if row else 0
