#!/usr/bin/env python3
"""Журнал дій @MalenkyiVsesvitBot: що натискають і що шукають.

    python3 tools/feed_events.py                   # зведення за 7 днів, без власників
    python3 tools/feed_events.py --days 30
    python3 tools/feed_events.py --user 354627345  # стрічка дій однієї людини
    python3 tools/feed_events.py --all             # разом з Max і D.

Власники (YT_USERS з .env) за замовчуванням виключені, бо D. дає більшість натискань
і затуляє поведінку решти.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from feed import db  # noqa: E402

TZ = ZoneInfo("Europe/Kyiv")


def _owners() -> set[int]:
    env = BASE / ".env"
    m = re.search(r"^YT_USERS=(.*)$", env.read_text(), re.M) if env.exists() else None
    return {int(x) for x in re.split(r"[,\s]+", m.group(1)) if x.isdigit()} if m else set()


def _dt(ts: int) -> str:
    return datetime.fromtimestamp(ts, TZ).strftime("%d.%m %H:%M")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--user", type=int)
    ap.add_argument("--all", action="store_true", help="не виключати власників")
    a = ap.parse_args()

    since = db.now() - a.days * 86400
    skip = set() if a.all or a.user else _owners()
    cond = "ts>=?" + (f" AND user_id NOT IN ({','.join(map(str, skip))})" if skip else "")
    args: list = [since]
    if a.user:
        cond += " AND user_id=?"
        args.append(a.user)

    if a.user:
        for r in db.q(f"SELECT * FROM event WHERE {cond} ORDER BY ts", args):
            extra = f"  «{r['data']}» → {r['hits']}" if r["kind"] == "search" else f"  {r['data']}"
            print(f"{_dt(r['ts'])}  {r['kind']:6} {r['action']:14}{extra}")
        return

    tot = db.q1(f"SELECT COUNT(*) c, COUNT(DISTINCT user_id) u FROM event WHERE {cond}", args)
    print(f"за {a.days} дн.: {tot['c']} дій від {tot['u']} людей"
          + (" (без власників)" if skip else ""))

    print("\nкнопки і команди (дій / людей):")
    for r in db.q(f"SELECT action, COUNT(*) c, COUNT(DISTINCT user_id) u FROM event "
                  f"WHERE {cond} AND kind!='search' GROUP BY action ORDER BY c DESC LIMIT 30", args):
        print(f"  {r['action']:16} {r['c']:5} / {r['u']}")

    print("\nлюди (дій, пошуків):")
    for r in db.q(f"SELECT user_id, COUNT(*) c, SUM(kind='search') s, MAX(ts) last FROM event "
                  f"WHERE {cond} GROUP BY user_id ORDER BY c DESC LIMIT 20", args):
        print(f"  {r['user_id']:<12} {r['c']:5} {r['s']:4}   останнє {_dt(r['last'])}")

    print("\nпошук без результату:")
    for r in db.q(f"SELECT data, COUNT(*) c FROM event WHERE {cond} AND kind='search' AND hits=0 "
                  f"GROUP BY lower(data) ORDER BY c DESC LIMIT 20", args):
        print(f"  {r['c']:3}× «{r['data']}»")

    print("\nпошук з результатом:")
    for r in db.q(f"SELECT data, COUNT(*) c FROM event WHERE {cond} AND kind='search' AND hits>0 "
                  f"GROUP BY lower(data) ORDER BY c DESC LIMIT 15", args):
        print(f"  {r['c']:3}× «{r['data']}»")


if __name__ == "__main__":
    main()
