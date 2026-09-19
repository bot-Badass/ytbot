"""Вечірній підсумок: одна картка на родину за добу.

Раніше нагадування лишалось у базі відкритим, поки хтось не тапне кнопку, а скан
ходив кожні 10 хвилин - тому один огірок прилітав шість разів на годину кожному
дорослому, і так до ночі. Тепер усе, що назбиралось за добу, іде однією карткою
о 20:30, надіслане закривається одразу, а правило трьох днів закривається саме:
три доби без зафіксованої реакції - продукт освоєний, питати про це нічого.
"""
from __future__ import annotations

import logging
from datetime import time

from telegram import InlineKeyboardMarkup as M
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, ContextTypes

from . import catalog, db, suggest, ui

log = logging.getLogger("feed.reminders")

DIGEST_AT = time(20, 30, tzinfo=ui.TZ)
STALE_DAYS = 7      # недоставлене нагадування не висить у базі вічно
ACTIVE_DAYS = 3     # про порожній день пишемо лише тим, хто справді веде щоденник


def _remind_on(family_id: int) -> bool:
    return db.get_kv(family_id, "remind", "1") == "1"


async def _send(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, kb: M | None) -> bool:
    try:
        await context.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return True
    except TelegramError as exc:
        log.warning("підсумок не доставлено %s: %s", chat_id, exc)
        return False


def _split(child, now: int) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """Розкладає прострочені нагадування на «спитати» і «закрити самому»."""
    intro = db.intro_map(child["id"])
    ask: list[tuple[int, str]] = []
    mastered: list[tuple[int, str]] = []
    for nudge in db.due_nudges(now):
        if nudge["child_id"] != child["id"]:
            continue
        row = intro.get(nudge["code"])
        # watch / avoid - рішення вже прийнято, нагадувати нема про що
        if not row or row["status"] in ("watch", "avoid") or not catalog.product(nudge["code"]):
            db.close_nudge(nudge["id"])
            continue
        if nudge["kind"] == "three_day":
            mastered.append((nudge["id"], nudge["code"]))
        elif now - nudge["due_ts"] > STALE_DAYS * 86400:
            db.close_nudge(nudge["id"])
        else:
            ask.append((nudge["id"], nudge["code"]))
    done = {code for _, code in mastered}
    return [x for x in ask if x[1] not in done], mastered


async def evening_digest(context: ContextTypes.DEFAULT_TYPE) -> None:
    now = db.now()
    ts_from, ts_to, _ = ui.day_bounds()
    for child in db.q("SELECT * FROM child"):
        try:
            await _one(context, child, now, ts_from, ts_to)
        except Exception:  # одна крива родина не має гасити розсилку
            log.exception("підсумок для дитини %s не зібрався", child["id"])


async def _one(context: ContextTypes.DEFAULT_TYPE, child, now: int,
               ts_from: int, ts_to: int) -> None:
    ask, mastered = _split(child, now)

    if not _remind_on(child["family_id"]):
        for nid, _ in ask + mastered:
            db.close_nudge(nid)
        return

    # правило трьох днів закриваємо самі: три доби без реакції = продукт освоєний
    for nid, code in mastered:
        db.set_status(child["id"], code, "ok")
        db.close_nudges_for(child["id"], code)

    meals = db.meals_between(child["id"], ts_from, ts_to)
    recent = db.meals_between(child["id"], ts_from - ACTIVE_DAYS * 86400, ts_from)
    show_record = not meals and bool(recent)

    if not ask and not mastered and not show_record:
        return  # тиша - теж нормальний стан

    intro = db.intro_map(child["id"])
    can_next = not suggest.holding(intro, now)
    text, kb = ui.evening_digest(child, ask, [c for _, c in mastered], show_record, can_next)

    delivered = False
    for chat_id in db.family_chats(child["family_id"]):
        delivered |= await _send(context, chat_id, text, kb)
    if delivered:
        # надіслане закривається одразу: питання ставиться один раз, не по колу
        for nid, _ in ask:
            db.close_nudge(nid)


def install(app: Application) -> None:
    jq = app.job_queue
    if jq is None:
        log.warning("JobQueue недоступна - нагадувань не буде (постав APScheduler)")
        return
    jq.run_daily(evening_digest, time=DIGEST_AT, name="feed_evening")
    log.info("нагадування прикорму: один підсумок на родину о %s", DIGEST_AT.strftime("%H:%M"))
