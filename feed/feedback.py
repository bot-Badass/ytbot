"""Зворотний звʼязок: людина пише автору, автор відповідає reply-ем, бот пересилає.

Три входи: кнопка «💬 Написати автору» (інструкція, налаштування, /feedback),
пошук без результатів, схожий на питання (такий текст раніше губився в журналі
пошуків), і одне питання «як вам бот?» на третій день після профілю.
Кожен відгук лежить у таблиці feedback; повідомлення автору запамʼятовує свій
message_id, тому reply на нього знаходить, кому відповідати.
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
import re
from datetime import time

from telegram import InlineKeyboardButton as B
from telegram import InlineKeyboardMarkup as M
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import Application, ApplicationHandlerStop, ContextTypes

from . import db, growth, ui

log = logging.getLogger("feed.feedback")

ASK_AT = time(12, 0, tzinfo=ui.TZ)
ASK_AFTER_DAYS = 3      # питаємо на третій день після профілю
ASK_ACTIVE_DAYS = 7     # тих, хто зник довше, не смикаємо
TEXT_LIMIT = 2000

WRITE_BTN = B("💬 Написати автору", callback_data="f:fb")
PROMPT = ("💬 Напишіть одним повідомленням: питання, побажання, що незручно або "
          "чого не вистачає. Я читаю кожне і відповідаю сюди ж.")


def owners() -> list[int]:
    raw = os.environ.get("FEEDBACK_TO", "")
    return [int(x) for x in re.split(r"[,\s]+", raw) if x.strip().isdigit()]


def looks_like_message(text: str) -> bool:
    """Пошук без результатів, схожий на звернення, а не на назву продукту."""
    t = text.strip()
    return "?" in t or len(t.split()) >= 3


def _about(user_id: int) -> str:
    """Підпис для автора: хто пише, звідки прийшов, скільки дитині."""
    sub = db.q1("SELECT * FROM subscriber WHERE user_id=?", (user_id,))
    who = ""
    if sub:
        who = html.escape(sub["name"] or "")
        if sub["username"]:
            who += f" @{html.escape(sub['username'])}"
        if sub["source"]:
            who += f" · {html.escape(sub['source'])}"
    child = db.child_of(user_id)
    if child:
        who += f" · {html.escape(child['name'])}, {growth.age_text(ui.birth_date(child))}"
    return who.strip() or str(user_id)


async def submit(context: ContextTypes.DEFAULT_TYPE, user_id: int, text: str,
                 kind: str = "msg", rating: str = "") -> bool:
    """Зберігає відгук і шле автору. False - автору не дійшло (але в базі лежить)."""
    text = text.strip()[:TEXT_LIMIT]
    fid = db.run("INSERT INTO feedback(ts, user_id, kind, rating, text) VALUES(?,?,?,?,?)",
                 (db.now(), user_id, kind, rating, text))
    head = {"msg": "💬", "search": "🔎", "rate": "👍" if rating == "up" else "👎"}.get(kind, "💬")
    body = f"{head} <b>Відгук #{fid}</b> · {_about(user_id)}"
    if text:
        body += f"\n\n{html.escape(text)}"
    body += "\n\n<i>Reply на це повідомлення - відповідь піде людині.</i>"
    sent = False
    for chat_id in owners():
        try:
            msg = await context.bot.send_message(chat_id, body, parse_mode="HTML")
            db.run("INSERT INTO feedback_msg(chat_id, message_id, feedback_id) VALUES(?,?,?)",
                   (chat_id, msg.message_id, fid))
            sent = True
        except TelegramError as exc:
            log.warning("відгук #%s не доставлено автору %s: %s", fid, chat_id, exc)
    if not owners():
        log.warning("FEEDBACK_TO порожній - відгук #%s лише в базі", fid)
    return sent


async def on_owner_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Автор відповів reply-ем на відгук - пересилаємо людині. Решту пропускаємо далі."""
    msg = update.message
    user = update.effective_user
    if not msg or not msg.reply_to_message or not user or user.id not in owners():
        return
    row = db.q1("SELECT f.* FROM feedback_msg m JOIN feedback f ON f.id=m.feedback_id "
                "WHERE m.chat_id=? AND m.message_id=?",
                (msg.chat_id, msg.reply_to_message.message_id))
    if not row:
        return
    text = (msg.text or "").strip()
    try:
        await context.bot.send_message(
            row["user_id"], f"💬 <b>Відповідь автора бота</b>\n\n{html.escape(text)}",
            parse_mode="HTML", reply_markup=M([[B("✍️ Відповісти", callback_data="f:fb")],
                                               [B("🏠 Головна", callback_data="f:home")]]))
        db.run("UPDATE feedback SET answered_ts=? WHERE id=?", (db.now(), row["id"]))
        await msg.reply_text(f"✅ Надіслано (відгук #{row['id']}).")
    except TelegramError as exc:
        if "blocked" in str(exc).lower() or "forbidden" in str(exc).lower():
            db.run("UPDATE subscriber SET blocked=1 WHERE user_id=?", (row["user_id"],))
        await msg.reply_text(f"⚠️ Не дійшло: {exc}")
    raise ApplicationHandlerStop


def ask_markup() -> M:
    return M([[B("👍 Подобається", callback_data="f:rate:up"),
               B("👎 Щось не так", callback_data="f:rate:down")],
              [WRITE_BTN]])


async def ask_once(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Одне питання «як вам бот?» на третій день. Хто вже отримав - більше не питаємо."""
    now = db.now()
    rows = db.q(
        "SELECT m.user_id FROM member m JOIN family f ON f.id=m.family_id "
        "JOIN subscriber s ON s.user_id=m.user_id "
        "WHERE f.created_at<=? AND s.last_seen>=? AND s.blocked=0 "
        # питаємо лише тих, хто справді щось відмітив: текст «ведете щоденник» іншим бреше
        # (21.10 питання пішло 492 людям, ~80% з них не мали жодного запису)
        # і перший запис продукту теж має бути ≥ASK_AFTER_DAYS тому: 31.10 питання «ведете вже
        # кілька днів» пішло мамі, яка записала перший продукт за 15 хвилин до того
        "AND EXISTS (SELECT 1 FROM child c JOIN intro i ON i.child_id=c.id "
        "            WHERE c.family_id=m.family_id AND i.first_ts<=?) "
        "AND m.user_id NOT IN (SELECT user_id FROM feedback WHERE kind='ask')",
        (now - ASK_AFTER_DAYS * 86400, now - ASK_ACTIVE_DAYS * 86400,
         now - ASK_AFTER_DAYS * 86400))
    # своя родина автора (Max і D.) не питається: це не користувачі, а ті, хто робить бот
    own = {r["user_id"] for o in owners() for r in db.q(
        "SELECT user_id FROM member WHERE family_id=(SELECT family_id FROM member WHERE user_id=?)",
        (o,))} | set(owners())
    asked = 0
    for r in rows:
        uid = r["user_id"]
        if uid in own:
            continue
        # мітка ставиться ДО відправки: питання задається один раз, навіть якщо не дійшло
        db.run("INSERT INTO feedback(ts, user_id, kind) VALUES(?,?, 'ask')", (now, uid))
        try:
            await context.bot.send_message(
                uid, "Ви ведете щоденник прикорму вже кілька днів. Як вам бот? "
                     "Одна кнопка - і я знатиму, що покращити.",
                reply_markup=ask_markup())
            asked += 1
            await asyncio.sleep(0.05)   # ліміт Telegram ~30 повідомлень/с
        except TelegramError as exc:
            if "blocked" in str(exc).lower() or "forbidden" in str(exc).lower():
                db.run("UPDATE subscriber SET blocked=1 WHERE user_id=?", (uid,))
            log.warning("питання про відгук не дійшло %s: %s", uid, exc)
    if asked:
        log.info("питання «як вам бот?» надіслано %s людям", asked)


def install(app: Application) -> None:
    if app.job_queue is not None:
        app.job_queue.run_daily(ask_once, time=ASK_AT, name="feed_ask_feedback")
