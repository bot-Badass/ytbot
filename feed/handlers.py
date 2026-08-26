"""Хендлери прикорму. Весь стан кнопок у callback_data з префіксом f:."""
from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timedelta

from telegram import InlineKeyboardButton as B
from telegram import InlineKeyboardMarkup as M
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ApplicationHandlerStop, ContextTypes

from . import catalog, db, growth, ui

log = logging.getLogger("feed")

DATE_RE = re.compile(r"^\s*(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})\s*$")
ISO_RE = re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*$")
NUM_RE = re.compile(r"^\s*(\d{1,3}(?:[.,]\d{1,2})?)\s*$")


# ─────────────────────────── допоміжне ───────────────────────────


async def _show(update: Update, text: str, kb: M | None = None) -> None:
    """Малює екран: редагує повідомлення з кнопкою або шле нове."""
    query = update.callback_query
    if query:
        try:
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb,
                                          disable_web_page_preview=True)
            return
        except TelegramError as exc:
            if "not modified" in str(exc).lower():
                return
    chat = update.effective_chat
    await chat.send_message(text, parse_mode=ParseMode.HTML, reply_markup=kb,
                            disable_web_page_preview=True)


def _who(update: Update) -> str:
    u = update.effective_user
    return (u.first_name or u.username or str(u.id)) if u else "?"


def _draft(context: ContextTypes.DEFAULT_TYPE) -> list[str]:
    return context.user_data.setdefault("draft", [])


def _guess_kind() -> str:
    h = datetime.now(ui.TZ).hour
    return "breakfast" if h < 11 else "lunch" if h < 15 else "dinner" if h < 21 else "snack"


def _parse_date(text: str) -> date | None:
    m = DATE_RE.match(text)
    if m:
        d, mo, y = (int(x) for x in m.groups())
    else:
        m = ISO_RE.match(text)
        if not m:
            return None
        y, mo, d = (int(x) for x in m.groups())
    try:
        value = date(y, mo, d)
    except ValueError:
        return None
    if value > date.today() or value < date.today() - timedelta(days=365 * 6):
        return None
    return value


# ─────────────────────────── онбординг ───────────────────────────


ONBOARD_TEXT = (
    "👋 Це помічник з прикорму.\n\n"
    "Веде щоденник прийомів їжі, тримає бібліотеку продуктів із безпечною подачею за віком, "
    "фіксує реакції, рахує перцентилі зростання за ВООЗ і нагадує про правило трьох днів.\n\n"
    "Почнемо з профілю дитини."
)

PRIVATE_TEXT = (
    "Цей помічник з прикорму приватний.\n\n"
    "Якщо тебе запросили - надішли код запрошення. "
    "Завантаження з YouTube працює як раніше: кинь посилання або набери /yt."
)


def _whitelist() -> set[int]:
    raw = os.environ.get("ALLOWED_USERS", "").replace(",", " ")
    return {int(x) for x in raw.split() if x.strip().lstrip("-").isdigit()}


def may_create(update: Update) -> bool:
    """Створити нову родину може або будь-хто (список порожній), або свій."""
    allow = _whitelist()
    return not allow or (update.effective_user and update.effective_user.id in allow)


async def onboard(update: Update) -> None:
    if may_create(update):
        kb = M([[B("👶 Створити профіль дитини", callback_data="f:setup")],
                [B("🔗 У мене є код запрошення", callback_data="f:joinask")]])
        await _show(update, ONBOARD_TEXT, kb)
        return
    kb = M([[B("🔗 У мене є код запрошення", callback_data="f:joinask")]])
    await _show(update, PRIVATE_TEXT, kb)


async def ensure(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Повертає (member, child) або None і показує потрібний екран онбордингу."""
    user = update.effective_user
    m = db.member(user.id)
    if not m:
        await onboard(update)
        return None
    child = db.child_of(user.id)
    if not child:
        context.user_data["await"] = {"k": "child_name"}
        await _show(update, "Як звати дитину?")
        return None
    return m, child


# ─────────────────────────── команди ───────────────────────────


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("await", None)
    got = await ensure(update, context)
    if not got:
        return
    _, child = got
    text, kb = ui.home(child, db.intro_map(child["id"]))
    await _show(update, text, kb)


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"Твій Telegram ID: <code>{update.effective_user.id}</code>",
                                    parse_mode=ParseMode.HTML)


# ─────────────────────────── текстовий ввід ───────────────────────────


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ловить тільки те, на що бот справді чекає. Інакше пропускає далі (ytbot)."""
    pending = context.user_data.get("await")
    if not pending:
        return
    text = (update.message.text or "").strip()
    kind = pending["k"]
    user = update.effective_user

    if kind == "child_name":
        name = text[:40]
        if not name:
            await update.message.reply_text("Напиши імʼя текстом.")
            raise ApplicationHandlerStop
        context.user_data["await"] = {"k": "birth", "name": name}
        await update.message.reply_text(
            f"Дата народження {name}? Формат: 15.01.2026")
        raise ApplicationHandlerStop

    if kind == "birth":
        value = _parse_date(text)
        if not value:
            await update.message.reply_text("Не зрозумів дату. Напиши як 15.01.2026.")
            raise ApplicationHandlerStop
        context.user_data["await"] = None
        context.user_data["birth"] = value.isoformat()
        context.user_data["name"] = pending.get("name") or (db.child_of(user.id) or {})["name"]
        kb = M([[B("👧 Дівчинка", callback_data="f:sex:girls"),
                 B("👦 Хлопчик", callback_data="f:sex:boys")]])
        await update.message.reply_text(
            f"{growth.age_text(value)}. Стать потрібна для таблиць ВООЗ:", reply_markup=kb)
        raise ApplicationHandlerStop

    if kind == "editbirth":
        value = _parse_date(text)
        if not value:
            await update.message.reply_text("Не зрозумів дату. Напиши як 15.01.2026.")
            raise ApplicationHandlerStop
        child = db.child_of(user.id)
        db.run("UPDATE child SET birth_date=? WHERE id=?", (value.isoformat(), child["id"]))
        context.user_data.pop("await", None)
        child = db.child_of(user.id)
        t, kb = ui.home(child, db.intro_map(child["id"]))
        await update.message.reply_text(f"Оновив: {growth.age_text(value)}.")
        await update.message.reply_text(t, parse_mode=ParseMode.HTML, reply_markup=kb)
        raise ApplicationHandlerStop

    if kind == "join":
        family_id = db.use_invite(text, user.id, _who(update))
        context.user_data.pop("await", None)
        if not family_id:
            await update.message.reply_text("Код не підійшов. Перевір або попроси новий.")
            raise ApplicationHandlerStop
        child = db.child_of(user.id)
        if not child:
            context.user_data["await"] = {"k": "child_name"}
            await update.message.reply_text("Приєднав. Як звати дитину?")
            raise ApplicationHandlerStop
        t, kb = ui.home(child, db.intro_map(child["id"]))
        await update.message.reply_text(f"Готово, ти в родині. Профіль: {child['name']}.")
        await update.message.reply_text(t, parse_mode=ParseMode.HTML, reply_markup=kb)
        raise ApplicationHandlerStop

    child = db.child_of(user.id)
    if not child:
        context.user_data.pop("await", None)
        raise ApplicationHandlerStop

    if kind in ("weight", "height"):
        m = NUM_RE.match(text.replace(",", "."))
        if not m:
            await update.message.reply_text("Треба число. Наприклад: 8.4")
            raise ApplicationHandlerStop
        value = float(m.group(1))
        context.user_data.pop("await", None)
        if kind == "weight":
            if not 1 <= value <= 40:
                await update.message.reply_text("Вага поза розумними межами. Спробуй ще раз.")
                raise ApplicationHandlerStop
            db.add_measure(child["id"], user.id, weight_kg=value)
        else:
            if not 30 <= value <= 130:
                await update.message.reply_text("Зріст поза розумними межами. Спробуй ще раз.")
                raise ApplicationHandlerStop
            db.add_measure(child["id"], user.id, height_cm=value)
        t, kb = ui.growth_screen(child)
        await update.message.reply_text(t, parse_mode=ParseMode.HTML, reply_markup=kb)
        raise ApplicationHandlerStop

    if kind == "mnote":
        db.run("UPDATE meal SET note=? WHERE id=? AND child_id=?",
               (text[:300], pending["id"], child["id"]))
        context.user_data.pop("await", None)
        t, kb = ui.diary(child)
        await update.message.reply_text(t, parse_mode=ParseMode.HTML, reply_markup=kb)
        raise ApplicationHandlerStop

    if kind == "rxnote":
        db.run("UPDATE reaction SET note=? WHERE id=(SELECT MAX(id) FROM reaction "
               "WHERE child_id=? AND code=?)", (text[:300], child["id"], pending["code"]))
        context.user_data.pop("await", None)
        t, kb = ui.card(child, pending["code"], db.intro_map(child["id"]))
        await update.message.reply_text("Нотатку збережено.")
        await update.message.reply_text(t, parse_mode=ParseMode.HTML, reply_markup=kb)
        raise ApplicationHandlerStop

    context.user_data.pop("await", None)


# ─────────────────────────── збереження прийому ───────────────────────────


def _save_meal(child, codes: list[str], kind: str, user_id: int) -> list[str]:
    intro_before = db.intro_map(child["id"])
    fresh = [c for c in codes if c not in intro_before]
    db.add_meal(child["id"], kind, codes, user_id)
    now = db.now()
    for code in fresh:
        db.add_nudge(child["id"], code, _tonight_ts(), "reaction_check")
        db.add_nudge(child["id"], code, now + 3 * 86400, "three_day")
    return fresh


def _tonight_ts() -> int:
    now = datetime.now(ui.TZ)
    target = now.replace(hour=20, minute=30, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return int(target.timestamp())


# ─────────────────────────── роутер кнопок ───────────────────────────


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data = query.data
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else "home"
    user = update.effective_user

    # дії, доступні до створення профілю
    if action == "setup":
        await query.answer()
        if not may_create(update):
            return await onboard(update)
        context.user_data["await"] = {"k": "child_name"}
        db.member(user.id) or db.create_family(user.id, _who(update))
        await _show(update, "Як звати дитину?")
        return
    if action == "joinask":
        await query.answer()
        context.user_data["await"] = {"k": "join"}
        await _show(update, "Надішли код запрошення (6 символів).")
        return
    if action == "sex":
        await query.answer()
        m = db.member(user.id) or {"family_id": db.create_family(user.id, _who(update))}
        name = context.user_data.get("name") or "Малюк"
        birth = context.user_data.get("birth")
        if not birth:
            context.user_data["await"] = {"k": "birth", "name": name}
            await _show(update, "Спершу дата народження. Формат: 15.01.2026")
            return
        db.add_child(m["family_id"], name, birth, parts[2])
        context.user_data.pop("birth", None)
        child = db.child_of(user.id)
        text, kb = ui.home(child, db.intro_map(child["id"]))
        await _show(update, f"Профіль створено: {child['name']}, {growth.age_text(ui.birth_date(child))}.")
        await update.effective_chat.send_message(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    got = await ensure(update, context)
    if not got:
        await query.answer()
        return
    member, child = got
    cid = child["id"]
    intro = db.intro_map(cid)
    await query.answer()

    if action == "home":
        context.user_data["draft"] = []
        context.user_data.pop("await", None)
        return await _show(update, *ui.home(child, intro))

    if action == "new":
        context.user_data["draft"] = []
        return await _show(update, *ui.draft(child, [], intro, "fav"))

    if action == "new_keep":
        return await _show(update, *ui.draft(child, _draft(context), intro, "fav"))

    if action == "grp":
        return await _show(update, *ui.draft(child, _draft(context), intro, parts[2]))

    if action == "sel":
        code = parts[2]
        d = _draft(context)
        if code in d:
            d.remove(code)
        elif len(d) >= 6:
            await query.answer("У тарілці вже 6 продуктів", show_alert=True)
        else:
            d.append(code)
        group = catalog.product(code)["group"] if catalog.product(code) else None
        return await _show(update, *ui.draft(child, d, intro, group))

    if action == "save":
        d = _draft(context)
        if not d:
            return await _show(update, *ui.draft(child, d, intro, "fav"))
        if len(parts) == 2:
            return await _show(update, *ui.pick_kind(d))
        kind = parts[2]
        fresh = _save_meal(child, d, kind, user.id)
        context.user_data["draft"] = []
        return await _show(update, *ui.saved(child, d, kind, fresh))

    if action == "quick":
        code = parts[2]
        fresh = _save_meal(child, [code], _guess_kind(), user.id)
        return await _show(update, *ui.saved(child, [code], _guess_kind(), fresh))

    if action == "lib":
        return await _show(update, *ui.library(child, intro))

    if action == "lg":
        return await _show(update, *ui.group_list(child, parts[2], intro))

    if action == "p":
        return await _show(update, *ui.card(child, parts[2], intro))

    if action == "react":
        return await _show(update, *ui.react_menu(parts[2]))

    if action == "rx":
        code, kind = parts[2], parts[3]
        db.add_reaction(cid, code, kind, user.id)
        db.close_nudges_for(cid, code)
        return await _show(update, *ui.react_saved(code, kind))

    if action == "rxnote":
        context.user_data["await"] = {"k": "rxnote", "code": parts[2]}
        return await _show(update, "Напиши нотатку одним повідомленням.")

    if action == "st":
        code, status = parts[2], parts[3]
        if code not in intro:
            db.touch_intro(cid, code, db.now())
        db.set_status(cid, code, status)
        db.close_nudges_for(cid, code)
        return await _show(update, *ui.card(child, code, db.intro_map(cid)))

    if action == "rxlog":
        return await _show(update, *ui.reactions_log(child))

    if action == "plates":
        band = parts[2] if len(parts) > 2 else None
        return await _show(update, *ui.plates_screen(child, band))

    if action == "pl":
        return await _show(update, *ui.plate_card(child, parts[2], intro))

    if action == "plu":
        plate = catalog.plate(parts[2])
        context.user_data["draft"] = list(plate["items"])[:6]
        return await _show(update, *ui.pick_kind(context.user_data["draft"]))

    if action == "rec":
        return await _show(update, *ui.recipes_screen(child))

    if action == "r":
        return await _show(update, *ui.recipe_card(child, parts[2]))

    if action == "ru":
        recipe = catalog.recipe(parts[2])
        context.user_data["draft"] = list(recipe["products"])[:6]
        return await _show(update, *ui.pick_kind(context.user_data["draft"]))

    if action == "diary":
        offset = int(parts[2]) if len(parts) > 2 else 0
        return await _show(update, *ui.diary(child, max(offset, -365)))

    if action == "mdel":
        db.delete_meal(int(parts[2]))
        return await _show(update, *ui.diary(child))

    if action == "mnote":
        context.user_data["await"] = {"k": "mnote", "id": int(parts[2])}
        return await _show(update, "Напиши нотатку до цього прийому одним повідомленням.")

    if action == "stats":
        return await _show(update, *ui.stats(child, intro))

    if action == "grow":
        return await _show(update, *ui.growth_screen(child))

    if action == "gw":
        context.user_data["await"] = {"k": "weight"}
        return await _show(update, "Вага в кілограмах. Наприклад: 8.4")

    if action == "gh":
        context.user_data["await"] = {"k": "height"}
        return await _show(update, "Зріст у сантиметрах. Наприклад: 68.5")

    if action == "set":
        remind = db.get_kv(member["family_id"], "remind", "1") == "1"
        return await _show(update, *ui.settings(child, member["family_id"], remind))

    if action == "rem":
        cur = db.get_kv(member["family_id"], "remind", "1") == "1"
        db.set_kv(member["family_id"], "remind", "0" if cur else "1")
        return await _show(update, *ui.settings(child, member["family_id"], not cur))

    if action == "inv":
        code = db.new_invite(member["family_id"])
        text = ("🔗 <b>Код запрошення</b>\n\n"
                f"<code>{code}</code>\n\n"
                f"Перешли цей код другому дорослому. Хай відкриє бота, натисне "
                f"«У мене є код запрошення» і надішле його. Код одноразовий.")
        return await _show(update, text, M([[B("‹ Налаштування", callback_data="f:set")]]))

    if action == "editbirth":
        context.user_data["await"] = {"k": "editbirth"}
        return await _show(update, "Нова дата народження. Формат: 15.01.2026")

    if action == "nudge":
        # відповідь на нагадування: f:nudge:<id>:<ok|react>
        nid, choice = int(parts[2]), parts[3]
        row = db.q1("SELECT * FROM nudge WHERE id=?", (nid,))
        db.close_nudge(nid)
        if not row:
            return await _show(update, *ui.home(child, intro))
        if choice == "ok":
            db.set_status(cid, row["code"], "ok")
            return await _show(update, *ui.card(child, row["code"], db.intro_map(cid)))
        return await _show(update, *ui.react_menu(row["code"]))

    return await _show(update, *ui.home(child, intro))


async def on_free_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Будь-який інший текст: показуємо головну, а не мовчимо."""
    if not db.member(update.effective_user.id) and not may_create(update):
        return
    got = await ensure(update, context)
    if not got:
        return
    _, child = got
    text, kb = ui.home(child, db.intro_map(child["id"]))
    await update.message.reply_text(
        "Тут керування кнопками. Посилання на YouTube теж працює, як раніше.",
        reply_markup=None)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
