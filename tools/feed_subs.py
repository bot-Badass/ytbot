#!/usr/bin/env python3
"""База підписників @MalenkyiVsesvitBot: статистика, вивантаження, розсилка.

    python3 tools/feed_subs.py stats
    python3 tools/feed_subs.py export [--out subs.csv]
    python3 tools/feed_subs.py broadcast --text msg.txt          # суха прогонка
    python3 tools/feed_subs.py broadcast --text msg.txt --send   # справжня розсилка

Розсилка за замовчуванням СУХА: показує, кому і що піде, і нічого не надсилає.
Хто заблокував бота, помічається blocked=1 і в наступну розсилку не потрапляє.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from feed import db  # noqa: E402

TZ = ZoneInfo("Europe/Kyiv")
RATE = 0.05          # 20 повідомлень на секунду - ліміт Telegram для розсилок


def _dt(ts: int) -> str:
    return datetime.fromtimestamp(ts, TZ).strftime("%d.%m.%Y %H:%M")


def cmd_stats() -> None:
    s = db.subscriber_stats()
    print(f"підписників:      {s['total']} (активних {s['active']}, заблокували {s['blocked']})")
    print(f"завели профіль:   {s['with_profile']} у {s['families']} родинах, дітей {s['children']}")
    print(f"заходили за добу: {s['day']}, за тиждень: {s['week']}")
    print(f"записів про їжу:  {s['meals']}")
    rows = db.q("SELECT source, COUNT(*) c FROM subscriber GROUP BY source ORDER BY c DESC")
    if any(r["source"] for r in rows):
        print("\nджерела (deep-link):")
        for r in rows:
            print(f"  {r['source'] or '(прямий вхід)':<20} {r['c']}")
    print("\nостанні 10:")
    for r in db.q("SELECT * FROM subscriber ORDER BY first_seen DESC LIMIT 10"):
        tag = " ⛔" if r["blocked"] else ""
        at = f"@{r['username']}" if r["username"] else "-"
        print(f"  {_dt(r['first_seen'])}  {r['user_id']:<12} {at:<18} {r['name'][:24]:<24}"
              f" візитів {r['hits']}{tag}")


def cmd_export(out: str) -> None:
    rows = db.subscribers(active_only=False)
    path = Path(out)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["user_id", "username", "name", "lang", "source",
                    "first_seen", "last_seen", "hits", "blocked", "has_profile"])
        for r in rows:
            has = bool(db.q1("SELECT 1 FROM member WHERE user_id=?", (r["user_id"],)))
            w.writerow([r["user_id"], r["username"], r["name"], r["lang"], r["source"],
                        _dt(r["first_seen"]), _dt(r["last_seen"]), r["hits"],
                        r["blocked"], int(has)])
    print(f"{len(rows)} рядків -> {path}")


async def cmd_broadcast(text: str, send: bool, only_profile: bool) -> None:
    from telegram import Bot
    from telegram.constants import ParseMode
    from telegram.error import Forbidden, TelegramError

    targets = db.subscribers(active_only=True)
    if only_profile:
        targets = [r for r in targets if db.q1("SELECT 1 FROM member WHERE user_id=?",
                                               (r["user_id"],))]
    print(f"{'РОЗСИЛКА' if send else 'СУХА ПРОГОНКА'}: {len(targets)} отримувач(ів)\n")
    print("-" * 60)
    print(text)
    print("-" * 60)
    if not send:
        for r in targets[:20]:
            print(f"  -> {r['user_id']} {r['name'][:30]}")
        if len(targets) > 20:
            print(f"  ... і ще {len(targets) - 20}")
        print("\nНічого не надіслано. Додай --send, щоб відправити насправді.")
        return

    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        for line in (BASE / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("BOT_TOKEN="):
                token = line.split("=", 1)[1].strip()
    if not token:
        raise SystemExit("BOT_TOKEN не знайдено")

    bot = Bot(token)
    ok = blocked = failed = 0
    async with bot:
        for r in targets:
            try:
                await bot.send_message(r["user_id"], text, parse_mode=ParseMode.HTML,
                                       disable_web_page_preview=True)
                ok += 1
            except Forbidden:
                db.mark_blocked(r["user_id"])
                blocked += 1
            except TelegramError as exc:
                failed += 1
                print(f"  {r['user_id']}: {exc}")
            await asyncio.sleep(RATE)
    print(f"\nнадіслано {ok}, заблокували бота {blocked}, помилок {failed}")


def _bot():
    from telegram import Bot
    env = {}
    for line in (BASE / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    token = os.environ.get("BOT_TOKEN", "").strip() or env.get("BOT_TOKEN", "")
    if not token:
        raise SystemExit("BOT_TOKEN не знайдено")
    # бот сидить на локальному Bot API: хмарний для нього вимкнено logOut-ом
    local = (os.environ.get("LOCAL_API_URL") or env.get("LOCAL_API_URL", "")).rstrip("/")
    if local:
        return Bot(token, base_url=f"{local}/bot", base_file_url=f"{local}/file/bot")
    return Bot(token)


def first_targets() -> list:
    """Родини з профілем, які не відмітили жодного продукту і ще не отримували це питання."""
    return db.q(
        "SELECT ch.id child_id, ch.name, ch.sex, f.id family_id, "
        "  (SELECT m.user_id FROM member m JOIN subscriber s ON s.user_id=m.user_id "
        "   WHERE m.family_id=f.id AND s.blocked=0 ORDER BY m.added_at LIMIT 1) user_id "
        "FROM family f JOIN child ch ON ch.family_id=f.id "
        "WHERE f.id<>1 "                                   # родина автора
        "  AND NOT EXISTS (SELECT 1 FROM intro WHERE child_id=ch.id) "
        "  AND NOT EXISTS (SELECT 1 FROM meal WHERE child_id=ch.id) "
        "  AND NOT EXISTS (SELECT 1 FROM kv WHERE family_id=f.id AND key='first_nudge') "
        "GROUP BY f.id ORDER BY RANDOM()")


def first_text(r) -> str:
    verb = "пробувала" if r["sex"] == "girls" else "пробував"
    name = str(r["name"]).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (f"{name} вже щось {verb}? Відмітьте одним тапом, "
            "і я підкажу, що вводити далі.")


async def cmd_first(limit: int, send: bool) -> None:
    from telegram import InlineKeyboardButton as B
    from telegram import InlineKeyboardMarkup as M
    from telegram.error import Forbidden, TelegramError

    targets = [r for r in first_targets() if r["user_id"]][:limit]
    print(f"{'РОЗСИЛКА' if send else 'СУХА ПРОГОНКА'}: «що вже їла», {len(targets)} родин\n")
    if not send:
        for r in targets[:10]:
            print(f"  -> {r['user_id']} | {first_text(r)}")
        print("\nНічого не надіслано. Додай --send, щоб відправити насправді.")
        return

    kb = M([[B("🥕 Відмітити, що вже їли", callback_data="f:first")]])
    ok = blocked = failed = 0
    async with _bot() as bot:
        for r in targets:
            try:
                await bot.send_message(r["user_id"], first_text(r), reply_markup=kb)
                ok += 1
            except Forbidden:
                db.mark_blocked(r["user_id"])
                blocked += 1
            except TelegramError as exc:
                failed += 1
                print(f"  {r['user_id']}: {exc}")
                await asyncio.sleep(RATE)
                continue
            # мітка по відправці, не по відповіді: питання ставиться один раз
            db.set_kv(r["family_id"], "first_nudge", str(db.now()))
            await asyncio.sleep(RATE)
    print(f"\nнадіслано {ok}, заблокували бота {blocked}, помилок {failed}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats")
    ex = sub.add_parser("export")
    ex.add_argument("--out", default="subs.csv")
    br = sub.add_parser("broadcast")
    br.add_argument("--text", required=True, help="файл з текстом (HTML Telegram)")
    br.add_argument("--send", action="store_true", help="справді надіслати")
    br.add_argument("--only-profile", action="store_true",
                    help="лише тим, хто завів профіль дитини")
    fn = sub.add_parser("first-nudge", help="питання «що вже їла» тим, хто не відмітив жодного продукту")
    fn.add_argument("--limit", type=int, default=50)
    fn.add_argument("--send", action="store_true", help="справді надіслати")
    args = ap.parse_args()

    if args.cmd == "stats":
        cmd_stats()
    elif args.cmd == "first-nudge":
        asyncio.run(cmd_first(args.limit, args.send))
    elif args.cmd == "export":
        cmd_export(args.out)
    else:
        body = Path(args.text).read_text(encoding="utf-8").strip()
        if not body:
            raise SystemExit("порожній текст розсилки")
        asyncio.run(cmd_broadcast(body, args.send, args.only_profile))


if __name__ == "__main__":
    main()
