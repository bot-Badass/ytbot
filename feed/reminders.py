"""Нагадування: правило трьох днів, вечірня перевірка реакції, тихий день без записів."""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta

from telegram import InlineKeyboardButton as B
from telegram import InlineKeyboardMarkup as M
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, ContextTypes

from . import catalog, db, ui

log = logging.getLogger("feed.reminders")

SCAN_SECONDS = 600


def _child(child_id: int):
    return db.q1("SELECT * FROM child WHERE id=?", (child_id,))


def _remind_on(family_id: int) -> bool:
    return db.get_kv(family_id, "remind", "1") == "1"


async def _send(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, kb: M | None) -> bool:
    try:
        await context.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return True
    except TelegramError as exc:
        log.warning("нагадування не доставлено %s: %s", chat_id, exc)
        return False


async def scan_nudges(context: ContextTypes.DEFAULT_TYPE) -> None:
    for nudge in db.due_nudges(db.now()):
        child = _child(nudge["child_id"])
        if not child:
            db.close_nudge(nudge["id"])
            continue
        if not _remind_on(child["family_id"]):
            db.close_nudge(nudge["id"])
            continue
        row = db.q1("SELECT * FROM intro WHERE child_id=? AND code=?",
                    (child["id"], nudge["code"]))
        if not row or row["status"] in ("watch", "avoid"):
            db.close_nudge(nudge["id"])
            continue

        label = catalog.label(nudge["code"])
        if nudge["kind"] == "reaction_check":
            text = (f"🔔 Сьогодні {child['name']} вперше пробувала {label}.\n\n"
                    "Як пройшло? Висип, червоні щоки, живіт, стілець - усе, що впало в око.")
        else:
            days = max(1, (db.now() - row["first_ts"]) // 86400)
            text = (f"🔔 {label} у меню вже {days} дн. без зафіксованих реакцій.\n\n"
                    "Позначити продукт освоєним? Тоді можна вводити наступний новий.")
        kb = M([[B("✅ Все добре", callback_data=f"f:nudge:{nudge['id']}:ok"),
                 B("⚠️ Була реакція", callback_data=f"f:nudge:{nudge['id']}:react")]])

        delivered = False
        for chat_id in db.family_chats(child["family_id"]):
            delivered |= await _send(context, chat_id, text, kb)
        if not delivered:
            db.close_nudge(nudge["id"])


async def evening_check(context: ContextTypes.DEFAULT_TYPE) -> None:
    ts_from, ts_to, _ = ui.day_bounds()
    for child in db.q("SELECT * FROM child"):
        if not _remind_on(child["family_id"]):
            continue
        if db.meals_between(child["id"], ts_from, ts_to):
            continue
        text = (f"🔔 За сьогодні по {child['name']} немає жодного запису.\n\n"
                "Якщо їли - додай зараз, поки памʼятаєш. Якщо був день на молоці, "
                "просто пропусти це повідомлення.")
        kb = M([[B("🍽 Записати", callback_data="f:new")],
                [B("🔕 Не нагадувати", callback_data="f:rem")]])
        for chat_id in db.family_chats(child["family_id"]):
            await _send(context, chat_id, text, kb)


def install(app: Application) -> None:
    jq = app.job_queue
    if jq is None:
        log.warning("JobQueue недоступна - нагадувань не буде (постав APScheduler)")
        return
    jq.run_repeating(scan_nudges, interval=SCAN_SECONDS, first=60, name="feed_nudges")
    jq.run_daily(evening_check, time=time(21, 0, tzinfo=ui.TZ), name="feed_evening")
    log.info("нагадування прикорму увімкнено: скан кожні %s с, вечірня перевірка 21:00", SCAN_SECONDS)
