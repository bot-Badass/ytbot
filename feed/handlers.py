"""Хендлери прикорму. Весь стан кнопок у callback_data з префіксом f:."""
from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, time, timedelta

from telegram import InlineKeyboardButton as B
from telegram import InlineKeyboardMarkup as M
from telegram import ReplyKeyboardRemove, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ApplicationHandlerStop, ContextTypes

from . import catalog, db, growth, search, suggest, ui

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


def track(update: Update, source: str = "") -> None:
    """Кожен, хто торкнувся бота, потрапляє в базу підписників."""
    try:
        db.touch_subscriber(update.effective_user, source)
    except Exception as exc:          # база підписників не має ламати відповідь
        log.warning("не вдалось записати підписника: %s", exc)


def _who(update: Update) -> str:
    u = update.effective_user
    return (u.first_name or u.username or str(u.id)) if u else "?"


def _draft(context: ContextTypes.DEFAULT_TYPE) -> list[str]:
    return context.user_data.setdefault("draft", [])


def _rare(family_id: int) -> bool:
    """Чи показувати екзотику. Прапорець спільний на родину, живе між сесіями."""
    return db.get_kv(family_id, "rare", "0") == "1"


# коли ставимо час для запису за минулий день: реального ми не знаємо,
# а «13:00» у щоденнику читається краще, ніж момент, коли мама згадала
MEAL_HOUR = {"breakfast": 9, "lunch": 13, "dinner": 19, "snack": 16}


def _meal_day(context: ContextTypes.DEFAULT_TYPE) -> date | None:
    iso = context.user_data.get("day")
    if not iso:
        return None
    try:
        return date.fromisoformat(iso)
    except ValueError:
        return None


def _meal_ts(context: ContextTypes.DEFAULT_TYPE, kind: str) -> int | None:
    """None означає «зараз» - тоді add_meal сам поставить поточний час."""
    day = _meal_day(context)
    if not day or day == date.today():
        return None
    moment = datetime.combine(day, time(MEAL_HOUR.get(kind, 12), 0), ui.TZ)
    return int(moment.timestamp())


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
    "👋 <b>Це помічник з прикорму.</b>\n\n"
    "Веде щоденник їжі, тримає бібліотеку продуктів із безпечною подачею за віком, "
    "фіксує реакції, рахує зростання за таблицями ВООЗ і нагадує про правило трьох днів.\n\n"
    "Ще складає тарілки з того, що є в холодильнику, і рахує покупки на тиждень.\n\n"
    "Безкоштовно, без підписки і реклами. Налаштування займає хвилину: "
    "імʼя дитини, дата народження, стать.\n\n"
    "<i>Довідка на основі рекомендацій ВООЗ, AAP і EFSA. Не замінює педіатра.</i>"
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
                [B("🔗 У мене є код запрошення", callback_data="f:joinask")],
                [B("ℹ️ Що вміє бот", callback_data="f:guide")]])
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
    track(update, " ".join(context.args or [])[:64])
    context.user_data.pop("await", None)
    got = await ensure(update, context)
    if not got:
        return
    _, child = got
    text, kb = ui.home(child, db.intro_map(child["id"]))
    await _show(update, text, kb)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    track(update, "help")
    await _show(update, *ui.guide("start"))


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"Твій Telegram ID: <code>{update.effective_user.id}</code>",
                                    parse_mode=ParseMode.HTML)


# ─────────────────────────── текстовий ввід ───────────────────────────


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ловить тільки те, на що бот справді чекає. Інакше пропускає далі (ytbot)."""
    pending = context.user_data.get("await")
    if not pending:
        return
    track(update)
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
            await update.message.reply_text(
                "Код не підійшов. Він одноразовий і діє добу - попроси новий.")
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

    if kind == "have":
        context.user_data.pop("await", None)
        found = suggest.parse_products(text)
        if not found:
            await update.message.reply_text(
                "Не впізнав жодного продукту. Напиши простіше, наприклад: морква і курка.")
            context.user_data["await"] = {"k": "have"}
            raise ApplicationHandlerStop
        context.user_data["found"] = found
        months = ui.months_of(child)
        intro = db.intro_map(child["id"])
        plates = suggest.complete(months, intro, found, 3)
        t_, kb = ui.have(child, found, plates, intro)
        await update.message.reply_text(t_, parse_mode=ParseMode.HTML, reply_markup=kb)
        raise ApplicationHandlerStop

    if kind == "fridge":
        context.user_data.pop("await", None)
        found = suggest.parse_products(text, limit=20)
        if not found:
            context.user_data["await"] = {"k": "fridge"}
            await update.message.reply_text(
                "Не впізнав жодного продукту. Напиши простіше, наприклад: морква, гречка.")
            raise ApplicationHandlerStop
        added = db.pantry_add(child["family_id"], found, user.id)
        t_, kb = ui.fridge_added(added, [c for c in found if c not in added])
        await update.message.reply_text(t_, parse_mode=ParseMode.HTML, reply_markup=kb)
        raise ApplicationHandlerStop

    if kind == "rfind":
        context.user_data.pop("await", None)
        res = search.find(text)
        if not res["codes"] and not res["recipes"]:
            context.user_data["await"] = {"k": "rfind"}
            await update.message.reply_text(
                "Нічого не знайшов. Напиши простіше, наприклад: курка, гарбуз або сирники.")
            raise ApplicationHandlerStop
        if res["codes"] and not res["recipes"]:
            near = catalog.recipes_with(_same_shelf(res["codes"]))
            t_, kb = ui.recipes_found(child, res["codes"], [], near)
        else:
            context.user_data["found"] = res["codes"]
            t_, kb = ui.search_result(child, text, res["codes"], res["recipes"],
                                      db.intro_map(child["id"]))
        await update.message.reply_text(t_, parse_mode=ParseMode.HTML, reply_markup=kb)
        raise ApplicationHandlerStop

    if kind == "mealdate":
        value = _parse_date(text)
        if not value:
            await update.message.reply_text("Не зрозумів дату. Напиши як 03.09.2026.")
            raise ApplicationHandlerStop
        context.user_data.pop("await", None)
        context.user_data["day"] = value.isoformat()
        d = _draft(context)
        if not d:
            t, kb = ui.home(child, db.intro_map(child["id"]))
            await update.message.reply_text(
                f"Записую за {ui.day_label(value)}. Обери, що було в тарілці.")
            await update.message.reply_text(t, parse_mode=ParseMode.HTML, reply_markup=kb)
            raise ApplicationHandlerStop
        t, kb = ui.pick_kind(d, value)
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


def _save_meal(child, codes: list[str], kind: str, user_id: int,
               ts: int | None = None) -> list[str]:
    intro_before = db.intro_map(child["id"])
    fresh = [c for c in codes if c not in intro_before]
    db.add_meal(child["id"], kind, codes, user_id, ts=ts)
    now = db.now()
    for code in fresh:
        db.add_nudge(child["id"], code, _tonight_ts(), "reaction_check")
        db.add_nudge(child["id"], code, now + 3 * 86400, "three_day")
    return fresh


def _same_shelf(codes: list[str]) -> list[str]:
    """Сусіди по полиці: якщо рецепта з куркою немає, індичка це найближче."""
    keys = {catalog.key_of(c) for c in codes}
    return [p["code"] for p in catalog.products().values()
            if catalog.key_of(p["code"]) in keys and p["code"] not in codes]


def _tonight_ts() -> int:
    now = datetime.now(ui.TZ)
    target = now.replace(hour=20, minute=30, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return int(target.timestamp())


async def _record_and_continue(update: Update, context: ContextTypes.DEFAULT_TYPE,
                               child, codes: list[str], kind: str, fresh: list[str],
                               ts: int | None = None) -> None:
    """Запис про їжу лишається в чаті назавжди, навігація йде окремим повідомленням.

    Раніше і запис, і наступний екран жили в одному повідомленні, тому «Головна»
    затирала те, що дитина щойно з'їла. Тепер редагування зупиняється на записі:
    у нього немає кнопок, отже перезаписати його нічим.
    """
    record = ui.meal_record(child, codes, kind, ts)
    query = update.callback_query
    posted = False
    if query:
        try:
            await query.edit_message_text(record, parse_mode=ParseMode.HTML, reply_markup=None)
            posted = True
        except TelegramError as exc:
            log.warning("не вдалось перетворити картку на запис: %s", exc)
    if not posted:
        await update.effective_chat.send_message(record, parse_mode=ParseMode.HTML)

    text, kb = ui.after_save(child, db.intro_map(child["id"]), fresh)
    await update.effective_chat.send_message(text, parse_mode=ParseMode.HTML, reply_markup=kb,
                                             disable_web_page_preview=True)


# ─────────────────────────── роутер кнопок ───────────────────────────


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    track(update)
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
    if action == "guide":
        await query.answer()
        return await _show(update, *ui.guide(parts[2] if len(parts) > 2 else "start"))

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
    fid = child["family_id"]
    intro = db.intro_map(cid)
    show_rare = _rare(fid)
    await query.answer()

    if action == "home":
        context.user_data["draft"] = []
        context.user_data.pop("await", None)
        context.user_data.pop("day", None)
        return await _show(update, *ui.home(child, intro))

    if action == "new":
        context.user_data["draft"] = []
        # починаємо з холодильника: майже завжди дитині дають те, що вдома вже є
        tab = "fridge" if db.pantry_codes(fid) else "fav"
        context.user_data["grp"] = tab
        return await _show(update, *ui.draft(child, [], intro, tab, show_rare))

    if action == "new_keep":
        tab = context.user_data.get("grp", "fav")
        return await _show(update, *ui.draft(child, _draft(context), intro, tab, show_rare))

    if action == "grp":
        context.user_data["grp"] = parts[2]
        return await _show(update, *ui.draft(child, _draft(context), intro, parts[2], show_rare))

    if action == "sel":
        code = parts[2]
        d = _draft(context)
        if code in d:
            d.remove(code)
        elif len(d) >= 6:
            await query.answer("У тарілці вже 6 продуктів", show_alert=True)
        else:
            d.append(code)
        # лишаємось на тій самій вкладці: інакше вибір з холодильника викидав у «Овочі»
        tab = context.user_data.get("grp") or catalog.key_of(code)
        return await _show(update, *ui.draft(child, d, intro, tab, show_rare))

    if action == "save":
        d = _draft(context)
        if not d:
            return await _show(update, *ui.draft(child, d, intro, "fav"))
        if len(parts) == 2:
            return await _show(update, *ui.pick_kind(d, _meal_day(context)))
        kind = parts[2]
        ts = _meal_ts(context, kind)
        fresh = _save_meal(child, d, kind, user.id, ts)
        context.user_data["draft"] = []
        context.user_data.pop("day", None)
        return await _record_and_continue(update, context, child, d, kind, fresh, ts)

    if action == "day":
        d = _draft(context)
        choice = parts[2] if len(parts) > 2 else "0"
        if choice == "ask":
            context.user_data["await"] = {"k": "mealdate"}
            return await _show(update, ui.MEAL_DATE_PROMPT)
        back = date.today() - timedelta(days=int(choice))
        context.user_data["day"] = back.isoformat()
        if not d:
            return await _show(update, *ui.draft(child, d, intro,
                                                 context.user_data.get("grp", "fav"), show_rare))
        return await _show(update, *ui.pick_kind(d, back))

    if action == "quick":
        code, kind = parts[2], _guess_kind()
        fresh = _save_meal(child, [code], kind, user.id)
        return await _record_and_continue(update, context, child, [code], kind, fresh)

    if action == "fr":
        context.user_data.pop("await", None)
        stock = db.pantry_codes(fid)
        since = {r["code"]: r["added_ts"] for r in db.pantry(fid)}
        return await _show(update, *ui.fridge(child, stock, since, intro))

    if action == "fradd":
        key = parts[2] if len(parts) > 2 else "veg"
        return await _show(update, *ui.fridge_pick(child, key, db.pantry_codes(fid),
                                                   intro, show_rare))

    if action == "frt":
        code = parts[2]
        put = db.pantry_toggle(fid, code, user.id)
        await query.answer("Поклав у холодильник" if put else "Забрав з холодильника")
        return await _show(update, *ui.fridge_pick(child, catalog.key_of(code),
                                                   db.pantry_codes(fid), intro, show_rare))

    if action == "frdel":
        stock = db.pantry_codes(fid)
        if not stock:
            return await _show(update, *ui.fridge(child, stock, {}, intro))
        return await _show(update, *ui.fridge_remove(child, stock))

    if action == "frx":
        db.pantry_remove(fid, parts[2])
        stock = db.pantry_codes(fid)
        if not stock:
            return await _show(update, *ui.fridge(child, stock, {}, intro))
        return await _show(update, *ui.fridge_remove(child, stock))

    if action == "frclear":
        db.pantry_clear(fid)
        return await _show(update, *ui.fridge(child, [], {}, intro))

    if action == "frtext":
        context.user_data["await"] = {"k": "fridge"}
        return await _show(update, ui.FRIDGE_TEXT_PROMPT)

    if action == "frgen":
        stock = db.pantry_codes(fid)
        context.user_data["plate_back"] = "f:frgen"
        plates = suggest.stock_plates(ui.months_of(child), intro, stock, 6)
        return await _show(update, *ui.fridge_plates(child, plates, stock))

    if action == "frbuy":
        stock = db.pantry_codes(fid)
        items = suggest.shopping(ui.months_of(child), intro, stock)
        context.user_data["buy"] = [c for c, _ in items]
        return await _show(update, *ui.fridge_shopping(child, items, stock, intro))

    if action == "frbuyall":
        db.pantry_add(fid, context.user_data.get("buy", []), user.id)
        context.user_data.pop("buy", None)
        stock = db.pantry_codes(fid)
        since = {r["code"]: r["added_ts"] for r in db.pantry(fid)}
        return await _show(update, *ui.fridge(child, stock, since, intro))

    if action == "frput":
        codes = context.user_data.get("found", [])
        added = db.pantry_add(fid, codes, user.id)
        return await _show(update, *ui.fridge_added(added, [c for c in codes if c not in added]))

    if action == "rare":
        db.set_kv(fid, "rare", "0" if show_rare else "1")
        show_rare = not show_rare
        where = parts[2] if len(parts) > 2 else "lib"
        key = parts[3] if len(parts) > 3 else "veg"
        if where == "lg":
            return await _show(update, *ui.group_list(child, key, intro, show_rare))
        if where == "grp":
            return await _show(update, *ui.draft(child, _draft(context), intro, key, show_rare))
        if where == "fradd":
            return await _show(update, *ui.fridge_pick(child, key, db.pantry_codes(fid),
                                                       intro, show_rare))
        if where == "set":
            remind = db.get_kv(fid, "remind", "1") == "1"
            return await _show(update, *ui.settings(child, fid, remind, show_rare))
        return await _show(update, *ui.library(child, intro, show_rare))

    if action == "next":
        stock = db.pantry_codes(fid)
        hold = suggest.holding(intro, db.now())
        items = [] if hold else suggest.next_products(ui.months_of(child), intro, stock)
        context.user_data["next"] = [c for c, _ in items]
        return await _show(update, *ui.next_up(child, intro, items, hold, stock))

    if action == "nxfr":
        codes = context.user_data.get("next", [])
        added = db.pantry_add(fid, codes, user.id)
        return await _show(update, *ui.fridge_added(added, [c for c in codes if c not in added]))

    if action == "lib":
        return await _show(update, *ui.library(child, intro, show_rare))

    if action == "lg":
        return await _show(update, *ui.group_list(child, parts[2], intro, show_rare))

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
        return await _show(update, *ui.pick_kind(context.user_data["draft"], _meal_day(context)))

    if action == "gen":
        context.user_data["plate_back"] = "f:gen"
        plates = suggest.generate(ui.months_of(child), intro, 6)
        return await _show(update, *ui.generated(child, plates))

    if action == "gp":
        codes = suggest.decode(parts[2])
        if not codes:
            return await _show(update, *ui.plates_screen(child))
        back = context.user_data.get("plate_back", "f:gen")
        return await _show(update, *ui.gen_plate_card(child, codes, intro, back))

    if action == "gu":
        codes = suggest.decode(parts[2])[:6]
        context.user_data["draft"] = codes
        return await _show(update, *ui.pick_kind(codes, _meal_day(context)))

    if action == "have":
        context.user_data["await"] = {"k": "have"}
        return await _show(update, ui.HAVE_PROMPT)

    if action == "hv":
        found = context.user_data.get("found", [])
        if not found:
            context.user_data["await"] = {"k": "have"}
            return await _show(update, ui.HAVE_PROMPT)
        plates = suggest.complete(ui.months_of(child), intro, found, 3)
        return await _show(update, *ui.have(child, found, plates, intro))

    if action == "rec":
        show_all = len(parts) > 2 and parts[2] == "all"
        return await _show(update, *ui.recipes_screen(child, show_all, db.pantry_codes(fid)))

    if action == "rfind":
        context.user_data["await"] = {"k": "rfind"}
        return await _show(update, ui.RECIPE_FIND_PROMPT)

    if action == "rfridge":
        return await _show(update, *ui.recipes_from_fridge(child, db.pantry_codes(fid)))

    if action == "r":
        return await _show(update, *ui.recipe_card(child, parts[2], db.pantry_codes(fid)))

    if action == "ru":
        recipe = catalog.recipe(parts[2])
        context.user_data["draft"] = list(recipe["products"])[:6]
        return await _show(update, *ui.pick_kind(context.user_data["draft"], _meal_day(context)))

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
        remind = db.get_kv(fid, "remind", "1") == "1"
        return await _show(update, *ui.settings(child, fid, remind, show_rare))

    if action == "rem":
        cur = db.get_kv(fid, "remind", "1") == "1"
        db.set_kv(fid, "remind", "0" if cur else "1")
        return await _show(update, *ui.settings(child, fid, not cur, show_rare))

    if action == "inv":
        code = db.new_invite(member["family_id"])
        text = ("🔗 <b>Код запрошення</b>\n\n"
                f"<code>{code}</code>\n\n"
                f"Перешли цей код другому дорослому. Хай відкриє бота, натисне "
                f"«У мене є код запрошення» і надішле його.\n\n"
                f"Код одноразовий і діє добу. Не викладай його публічно: "
                f"той, хто його введе, побачить щоденник дитини.")
        return await _show(update, text, M([[B("‹ Налаштування", callback_data="f:set")]]))

    if action == "privacy":
        return await _show(update, *ui.privacy(child))

    if action == "wipe":
        if len(parts) == 2:
            return await _show(update, *ui.wipe_confirm(child))
        db.wipe_family(member["family_id"])
        context.user_data.clear()
        log.info("родина %s видалила свої дані", member["family_id"])
        return await _show(update,
                           "🗑 Дані видалено. Профіль, щоденник, реакції, виміри і "
                           "холодильник стерті.\n\nЯкщо захочеш почати наново - /start.")

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
    track(update)
    if not db.member(update.effective_user.id) and not may_create(update):
        return
    got = await ensure(update, context)
    if not got:
        return
    _, child = got
    intro = db.intro_map(child["id"])
    query = update.message.text or ""
    res = search.find(query)
    if res["codes"] or res["recipes"]:
        context.user_data["found"] = res["codes"]
        text, kb = ui.search_result(child, query, res["codes"], res["recipes"], intro)
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return
    text, kb = ui.home(child, intro)
    await update.message.reply_text(
        "Керування кнопками. Ще можна просто написати продукт або страву - наприклад "
        "«гарбуз» чи «гарбузовий суп» - і я покажу рецепти й підкажу тарілку. "
        "Посилання на YouTube працює як раніше.",
        reply_markup=ReplyKeyboardRemove())
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
