#!/usr/bin/env python3
"""Офлайн-прогін усіх екранів прикорму на тимчасовій базі.

Підміняє Telegram-обʼєкти качиними двійниками і проходить сценарій:
онбординг → конструктор тарілки → бібліотека → реакція → тарілки → рецепти →
щоденник → зростання → налаштування → нагадування.
Запуск: python3 tools/feed_smoke.py
"""
from __future__ import annotations

import asyncio
import html.parser
import json
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from feed import db  # noqa: E402

db.DB_PATH = Path(tempfile.mkdtemp()) / "smoke.sqlite3"

from feed import catalog, growth, handlers, suggest, ui  # noqa: E402
from telegram.ext import ApplicationHandlerStop  # noqa: E402

ALLOWED_TAGS = {"b", "i", "u", "s", "code", "pre", "a", "tg-spoiler", "br"}
errors: list[str] = []
screens = 0


class Checker(html.parser.HTMLParser):
    def __init__(self, where: str):
        super().__init__()
        self.where = where
        self.stack: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED_TAGS:
            errors.append(f"{self.where}: тег <{tag}> Telegram не приймає")
        elif tag != "br":
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag == "br":
            return
        if not self.stack or self.stack.pop() != tag:
            errors.append(f"{self.where}: незакритий/зайвий </{tag}>")


def check_text(where: str, text: str) -> None:
    global screens
    screens += 1
    if len(text) > 4096:
        errors.append(f"{where}: {len(text)} символів, ліміт Telegram 4096")
    p = Checker(where)
    p.feed(text)
    if p.stack:
        errors.append(f"{where}: не закрито {p.stack}")


widest = ("", 0, 0)


def check_kb(where: str, kb) -> None:
    global widest
    if kb is None or not hasattr(kb, "inline_keyboard"):
        return
    buttons = 0
    for row in kb.inline_keyboard:
        for btn in row:
            buttons += 1
            if btn.callback_data and len(btn.callback_data.encode()) > 64:
                errors.append(f"{where}: callback_data завелика: {btn.callback_data}")
    size = len(json.dumps(kb.to_dict(), ensure_ascii=False).encode())
    if buttons > widest[1]:
        widest = (where, buttons, size)
    if buttons > 100:
        errors.append(f"{where}: {buttons} кнопок, Telegram стільки не намалює")
    if size > 8000:
        errors.append(f"{where}: клавіатура {size} байт, ліміт близько 8 КБ")


class Bot:
    def __init__(self):
        self.sent: list[str] = []

    async def send_message(self, chat_id, text, **kw):
        check_text("bot.send", text)
        check_kb("bot.send", kw.get("reply_markup"))
        self.sent.append(text)


class Chat:
    def __init__(self, bot, cid):
        self.bot, self.id = bot, cid

    async def send_message(self, text, **kw):
        check_text("chat.send", text)
        check_kb("chat.send", kw.get("reply_markup"))
        self.bot.sent.append(text)


class Message:
    def __init__(self, bot, text=""):
        self.bot, self.text = bot, text

    async def reply_text(self, text, **kw):
        check_text("reply", text)
        check_kb("reply", kw.get("reply_markup"))
        self.bot.sent.append(text)


class Query:
    def __init__(self, bot, data):
        self.bot, self.data = bot, data
        self.last = ""

    async def answer(self, text=None, show_alert=False):
        return None

    async def edit_message_text(self, text, **kw):
        check_text(f"edit[{self.data}]", text)
        check_kb(f"edit[{self.data}]", kw.get("reply_markup"))
        self.last = text
        self.bot.sent.append(text)


class User:
    def __init__(self, uid, name):
        self.id, self.first_name, self.username = uid, name, name.lower()


class Update:
    def __init__(self, bot, user, *, data=None, text=None):
        self.callback_query = Query(bot, data) if data else None
        self.message = Message(bot, text) if text is not None else None
        self.effective_user = user
        self.effective_chat = Chat(bot, user.id)


class Context:
    def __init__(self, bot, args=None):
        self.bot = bot
        self.args = args or []
        self.user_data: dict = {}


async def main() -> None:
    bot = Bot()
    max_u = User(101, "Max")
    wife = User(202, "Olia")
    ctx = Context(bot)
    ctx_wife = Context(bot)

    async def press(data, user=max_u, c=None):
        await handlers.on_button(Update(bot, user, data=data), c or ctx)

    async def say(text, user=max_u, c=None):
        try:
            await handlers.on_text(Update(bot, user, text=text), c or ctx)
        except ApplicationHandlerStop:
            pass

    # ── інструкція доступна ще до профілю ──
    await handlers.cmd_start(Update(bot, max_u, text="/start"), ctx)
    await press("f:guide")
    for key in ui.GUIDE_ORDER:
        await press(f"f:guide:{key}")
    await handlers.cmd_help(Update(bot, max_u, text="/help"), ctx)
    assert db.q1("SELECT user_id FROM subscriber WHERE user_id=?", (max_u.id,)), \
        "підписник не потрапив у базу"

    # ── онбординг ──
    await press("f:setup")
    await say("Аліса")
    await say("15.01.2026")
    await press("f:sex:girls")
    child = db.child_of(max_u.id)
    assert child and child["name"] == "Аліса", "профіль не створився"
    print(f"профіль: {child['name']}, {growth.age_text(ui.birth_date(child))}, {child['sex']}")

    # ── конструктор тарілки ──
    await press("f:new")
    for group in ["fav"] + catalog.GROUP_ORDER:
        await press(f"f:grp:{group}")
    for code in ["garbuz", "grechka", "indychka", "oliia"]:
        await press(f"f:sel:{code}")
    await press("f:sel:oliia")   # зняти
    await press("f:sel:oliia")   # повернути
    await press("f:save")
    await press("f:save:lunch")
    meals = db.meals_between(child["id"], 0, db.now() + 1)
    assert len(meals) == 1, f"прийомів {len(meals)}, очікував 1"
    record = next((s for s in bot.sent if s.startswith("✅ <b>")), None)
    assert record, "запис про прийом їжі не потрапив у чат"
    await press("f:home")
    assert record in bot.sent, "запис зник після навігації"
    assert not any(s.startswith("✅ <b>") and "Головна" in s for s in bot.sent)
    print("запис у чаті:", record.replace("<b>", "").replace("</b>", "").split(chr(10))[0])
    assert set(db.meal_codes(meals[0]["id"])) == {"garbuz", "grechka", "indychka", "oliia"}
    assert len(db.intro_map(child["id"])) == 4
    nudges = db.due_nudges(db.now() + 10 * 86400)
    assert len(nudges) == 8, f"нагадувань {len(nudges)}, очікував 8 (4 продукти × 2)"
    print(f"прийом записано, введено {len(db.intro_map(child['id']))} продуктів, "
          f"нагадувань {len(nudges)}")

    # ── ліміт 6 продуктів ──
    await press("f:new")
    for code in list(catalog.products())[:8]:
        await press(f"f:sel:{code}")
    assert len(ctx.user_data["draft"]) == 6, ctx.user_data["draft"]
    await press("f:home")
    assert ctx.user_data["draft"] == []
    print("ліміт тарілки 6 продуктів працює")

    # ── бібліотека: кожна група і КОЖНА картка ──
    await press("f:lib")
    for group in catalog.GROUP_ORDER:
        await press(f"f:lg:{group}")
    for code in catalog.products():
        await press(f"f:p:{code}")
    print(f"картки продуктів: {len(catalog.products())} перевірено")

    # ── реакції ──
    await press("f:react:garbuz")
    for kind, _ in catalog.REACTIONS:
        await press(f"f:rx:garbuz:{kind}")
    assert db.intro_map(child["id"])["garbuz"]["status"] == "watch"
    await press("f:rxnote:garbuz")
    await say("зʼявився невеликий висип на щоках")
    rx = db.reactions(child["id"])
    assert rx and rx[0]["note"], "нотатка до реакції не збереглася"
    await press("f:st:garbuz:ok")
    assert db.intro_map(child["id"])["garbuz"]["status"] == "ok"
    await press("f:rxlog")
    print(f"реакцій записано: {len(rx)}, статуси перемикаються")

    # ── тарілки і рецепти ──
    for band, _, _, _ in catalog.AGE_BANDS:
        await press(f"f:plates:{band}")
    for p in catalog.plates():
        await press(f"f:pl:{p['id']}")
    await press("f:plu:p01")
    assert ctx.user_data["draft"] == catalog.plate("p01")["items"]
    await press("f:save:dinner")
    await press("f:rec")
    for r in catalog.recipes():
        await press(f"f:r:{r['id']}")
    await press("f:ru:r02")
    await press("f:save:snack")
    print(f"тарілок {len(catalog.plates())}, рецептів {len(catalog.recipes())} перевірено")

    # ── генератор варіантів і «у мене вже є» ──
    from feed import suggest
    await press("f:gen")
    gen = suggest.generate(ui.months_of(child), db.intro_map(child["id"]), 6)
    assert gen, "генератор не зібрав жодної тарілки"
    for pl in gen:
        await press(f"f:gp:{suggest.encode(pl)}")
    await press(f"f:gu:{suggest.encode(gen[0])}")
    await press("f:save:dinner")
    await press("f:have")
    await say("морква і курка")
    await say("хтозна що")                       # має попросити ще раз
    await say("яблуко, йогурт")
    # найдовша можлива callback_data для згенерованої тарілки
    longest = max((suggest.encode(pl) for pl in suggest.generate(24, {}, 120)), key=len)
    payload = len(f"f:gu:{longest}".encode())
    assert payload <= 64, f"callback_data {payload} байт: {longest}"
    print(f"генератор: {len(gen)} варіантів, найдовша callback_data {payload}/64 байт")
    # вільний текст із продуктами веде в добір, а не на головну
    upd = Update(bot, max_u, text="у нас є гречка та індичка")
    await handlers.on_free_text(upd, ctx)
    assert "У тебе вже є" in bot.sent[-1], bot.sent[-1]
    print("вільний текст «гречка та індичка» -> добір тарілки")

    # ── холодильник ──
    fid = db.member(max_u.id)["family_id"]
    await press("f:fr")
    await press("f:frbuy")            # порожній холодильник: список покупок з нуля
    assert ctx.user_data["buy"], "список покупок порожній"
    await press("f:frbuyall")
    assert db.pantry_codes(fid), "покупки не потрапили в холодильник"
    await press("f:fradd")
    for key in catalog.nav_keys():
        await press(f"f:fradd:{key}")
    was = "kabachok" in db.pantry_codes(fid)
    await press("f:frt:kabachok")
    assert ("kabachok" in db.pantry_codes(fid)) is not was, "тап не перемкнув продукт"
    await press("f:frt:kabachok")
    assert ("kabachok" in db.pantry_codes(fid)) is was, "повторний тап не повернув як було"
    await press("f:frtext")
    await say("морква, гречка, індичка, олія, йогурт")
    assert {"morkva", "grechka", "indychka", "oliia", "yogurt"} <= set(db.pantry_codes(fid))
    await say("хтозна що", )                 # має попросити ще раз
    ctx.user_data.pop("await", None)
    stock = db.pantry_codes(fid)
    fr_plates = suggest.stock_plates(ui.months_of(child), db.intro_map(child["id"]), stock, 6)
    assert fr_plates, "з повного холодильника не зібралась жодна тарілка"
    assert all(set(pl) <= set(stock) for pl in fr_plates), "тарілка взяла те, чого немає вдома"
    await press("f:frgen")
    for pl in fr_plates:
        await press(f"f:gp:{suggest.encode(pl)}")
    await press("f:frbuy")
    await press("f:frdel")
    await press("f:frx:morkva")
    assert "morkva" not in db.pantry_codes(fid)
    await press("f:have")
    await say("банан і кефір")
    await press("f:frput")
    assert "banan" in db.pantry_codes(fid), "«у мене вже є» не поклало в холодильник"
    print(f"холодильник: {len(db.pantry_codes(fid))} продуктів, тарілок з нього {len(fr_plates)}, "
          f"список покупок працює")
    await press("f:frclear")
    assert not db.pantry_codes(fid)
    await press("f:frtext")
    await say("гарбуз, кіноа, лосось, авокадо")

    # ── щоденник ──
    await press("f:diary")
    await press("f:diary:-1")
    await press("f:diary:-30")
    await press("f:stats")
    meals = db.meals_between(child["id"], 0, db.now() + 1)
    await press(f"f:mnote:{meals[0]['id']}")
    await say("їла добре, попросила ще")
    assert db.q1("SELECT note FROM meal WHERE id=?", (meals[0]["id"],))["note"]
    before = len(db.meals_between(child["id"], 0, db.now() + 1))
    await press(f"f:mdel:{meals[-1]['id']}")
    assert len(db.meals_between(child["id"], 0, db.now() + 1)) == before - 1
    print("щоденник: нотатка і видалення працюють")

    # ── зростання ──
    await press("f:grow")
    await press("f:gw")
    await say("8.4")
    await press("f:gh")
    await say("68,5")
    await press("f:gw")
    await say("сто кіло")           # має відхилити
    await say("8.5")
    assert len(db.measures(child["id"])) == 3, db.measures(child["id"])
    await press("f:grow")
    print("зростання: 3 виміри, перцентилі рахуються")

    # ── налаштування і друга доросла ──
    await press("f:set")
    await press("f:rem")
    assert db.get_kv(db.member(max_u.id)["family_id"], "remind") == "0"
    await press("f:rem")
    await press("f:inv")
    code = db.q1("SELECT code FROM invite WHERE used_by IS NULL")["code"]
    await handlers.cmd_start(Update(bot, wife, text="/start"), ctx_wife)
    await press("f:joinask", wife, ctx_wife)
    await say(code, wife, ctx_wife)
    assert db.child_of(wife.id)["id"] == child["id"], "дружина не бачить ту саму дитину"
    await press("f:new", wife, ctx_wife)
    await press("f:sel:banan", wife, ctx_wife)
    await press("f:save:breakfast", wife, ctx_wife)
    assert db.q1("SELECT by_user FROM meal ORDER BY id DESC LIMIT 1")["by_user"] == wife.id
    assert sorted(db.family_chats(db.member(max_u.id)["family_id"])) == [101, 202]
    print("спільний профіль: обидва дорослі пишуть в один щоденник")

    # ── нагадування ──
    db.run("UPDATE nudge SET due_ts=? WHERE done=0", (db.now() - 10,))
    from feed import reminders
    await reminders.scan_nudges(Context(bot))
    await reminders.evening_check(Context(bot))
    open_nudges = db.due_nudges(db.now())
    nid = db.q1("SELECT id FROM nudge WHERE done=0 LIMIT 1")
    if nid:
        await press(f"f:nudge:{nid['id']}:ok")
        assert db.q1("SELECT done FROM nudge WHERE id=?", (nid["id"],))["done"] == 1
    print(f"нагадування: розіслано, відповідь закриває нагадування")

    # ── редагування дати ──
    await press("f:editbirth")
    await say("01.02.2026")
    assert db.child_of(max_u.id)["birth_date"] == "2026-02-01"
    await press("f:home")

    # ── база підписників ──
    s = db.subscriber_stats()
    assert s["total"] >= 2 and s["with_profile"] >= 2, s
    assert db.q1("SELECT hits FROM subscriber WHERE user_id=?", (max_u.id,))["hits"] > 10
    print(f"підписники: {s['total']} у базі, {s['with_profile']} з профілем, "
          f"родин {s['families']}")

    # ── приватність і видалення даних (у кінці: стирає родину) ──
    await press("f:set")
    await press("f:privacy")
    await press("f:wipe")
    assert db.child_of(max_u.id), "підтвердження не мало нічого видаляти"
    await press("f:wipe:yes")
    assert not db.child_of(max_u.id), "дані не видалились"
    assert not db.q1("SELECT 1 FROM meal"), "прийоми їжі лишились після видалення"
    assert not db.q1("SELECT 1 FROM pantry"), "холодильник лишився після видалення"
    assert db.q1("SELECT 1 FROM subscriber WHERE user_id=?", (max_u.id,)), \
        "підписник має лишатись у базі після видалення даних родини"
    await handlers.cmd_start(Update(bot, max_u, text="/start"), ctx)
    print("видалення даних: родина стерта, підписник у базі, бот пропонує почати наново")

    # ── вільний текст ──
    upd = Update(bot, max_u, text="привіт")
    await handlers.on_text(upd, ctx)          # нічого не чекаємо - має пройти далі
    await handlers.on_free_text(upd, ctx)
    print("вільний текст не ламає бота")

    print(f"\nнайбільша клавіатура: {widest[1]} кнопок, {widest[2]} байт - {widest[0]}")
    print(f"екранів перевірено: {screens}, повідомлень надіслано: {len(bot.sent)}")
    if errors:
        print("\nПОМИЛКИ:")
        for e in dict.fromkeys(errors):
            print(" ", e)
        sys.exit(1)
    print("SMOKE OK - помилок немає")


if __name__ == "__main__":
    asyncio.run(main())
