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
import os
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from feed import db  # noqa: E402

db.DB_PATH = Path(tempfile.mkdtemp()) / "smoke.sqlite3"

from feed import catalog, feedback, growth, handlers, reminders, rich, suggest, ui  # noqa: E402
from telegram import InlineKeyboardMarkup as M  # noqa: E402
from telegram.ext import ApplicationHandlerStop  # noqa: E402

ALLOWED_TAGS = {"b", "i", "u", "s", "code", "pre", "a", "tg-spoiler", "br", "blockquote"}
RICH_TAGS = ALLOWED_TAGS | {"strong", "em", "h1", "h2", "h3", "h4", "h5", "h6", "p", "ul", "ol", "li",
                            "table", "tr", "td", "th", "details", "summary", "footer", "hr", "mark"}
VOID_TAGS = {"br", "hr"}
errors: list[str] = []
screens = 0


class Checker(html.parser.HTMLParser):
    def __init__(self, where: str, allowed: set[str] = ALLOWED_TAGS):
        super().__init__()
        self.where = where
        self.allowed = allowed
        self.stack: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.allowed:
            errors.append(f"{self.where}: тег <{tag}> Telegram не приймає")
        elif tag not in VOID_TAGS:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in VOID_TAGS:
            return
        if not self.stack or self.stack.pop() != tag:
            errors.append(f"{self.where}: незакритий/зайвий </{tag}>")


def check_text(where: str, text: str, allowed: set[str] = ALLOWED_TAGS, limit: int = 4096) -> None:
    global screens
    screens += 1
    if len(text) > limit:
        errors.append(f"{where}: {len(text)} символів, ліміт Telegram {limit}")
    p = Checker(where, allowed)
    p.feed(text)
    if p.stack:
        errors.append(f"{where}: не закрито {p.stack}")


def check_rich(where: str, html_: str) -> str:
    """Rich-екран і його запасний звичайний варіант. Повертає запасний текст для асертів."""
    check_text(f"{where}:rich", html_, RICH_TAGS, rich.RICH_LIMIT)
    screens_before = screens
    plain = rich.to_tg(html_)
    check_text(f"{where}:plain", plain)
    globals()["screens"] = screens_before
    return plain


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


class Sent(list):
    """Екрани по черзі. Повідомлення про досягнення йдуть окремо (ach), бо приходять
    після екрана дії, і тоді sent[-1] перестав би бути цим екраном."""
    def __init__(self):
        super().__init__()
        self.ach: list[str] = []

    def append(self, text):
        (self.ach if "досягнення!</b>" in str(text) else super()).append(text)


class Bot:
    def __init__(self):
        self.sent: list[str] = Sent()
        self.photos: list[tuple[int, str]] = []
        self.to: list[tuple[int, str]] = []   # кому пішло через send_message
        self.next_id = 1

    async def do_api_request(self, endpoint, api_kwargs=None, **kw):
        a = api_kwargs or {}
        where = f"{endpoint}"
        text = check_rich(where, a["rich_message"]["html"])
        check_kb(where, M.de_json(a["reply_markup"], None) if a.get("reply_markup") else None)
        self.sent.append(text)
        self.next_id += 1
        return {"message_id": self.next_id}

    async def edit_message_text(self, text, **kw):
        check_text("bot.edit", text)
        check_kb("bot.edit", kw.get("reply_markup"))
        self.sent.append(text)

    async def send_photo(self, chat_id, photo, caption="", **kw):
        check_text("bot.photo", caption)
        check_kb("bot.photo", kw.get("reply_markup"))
        self.photos.append((chat_id, caption))
        self.sent.append(caption)

    async def send_message(self, chat_id, text, **kw):
        check_text("bot.send", text)
        check_kb("bot.send", kw.get("reply_markup"))
        self.sent.append(text)
        self.to.append((chat_id, text))
        self.next_id += 1
        return _Msg(self.next_id)


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


class _Msg:
    def __init__(self, message_id=1):
        self.message_id = message_id


class Query:
    def __init__(self, bot, data):
        self.bot, self.data = bot, data
        self.message = _Msg()
        self.last = ""
        self.answered = False

    async def answer(self, text=None, show_alert=False):
        # Telegram приймає одну відповідь на натискання; другу (з алертом) губить мовчки
        assert not self.answered, f"подвійний answer() на {self.data}"
        self.answered = True

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
        self._bot = bot

    def get_bot(self):
        return self._bot


class _Job:
    def __init__(self, jq, name, at, data):
        self.jq, self.name, self.at, self.data = jq, name, at, data

    def schedule_removal(self):
        self.jq.jobs.remove(self)


class JobQueue:
    """Лише облік: які щоденні задачі стоять і на котру."""
    def __init__(self):
        self.jobs: list[_Job] = []

    def run_daily(self, callback, time, name=None, data=None):
        self.jobs.append(_Job(self, name, time, data))

    def get_jobs_by_name(self, name):
        return [j for j in self.jobs if j.name == name]


class Context:
    jq = JobQueue()   # одна черга на всіх, як у справжньому застосунку

    def __init__(self, bot, args=None):
        self.bot = bot
        self.args = args or []
        self.user_data: dict = {}
        self.job_queue = Context.jq


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
    # старі родини стилю не мають: картка новинок на головній, поки не натиснуть
    await press("f:home")
    assert "Два оновлення" in bot.sent[-1], "картки новинок немає"
    await press("f:news:later")
    assert "Два оновлення" not in bot.sent[-1] and db.get_kv(child["family_id"], "style") == ""
    db.run("DELETE FROM kv WHERE key=?", (ui.NEWS_KEY,))
    await press("f:home")
    await press("f:news:blw")
    assert db.get_kv(child["family_id"], "style") == "blw", "вибір з картки не збережено"
    await press("f:home")
    assert "Два оновлення" not in bot.sent[-1]
    print("картка новинок: показ, «Пізніше», вибір подачі")
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

    # ── ліміт тарілки: зайвий продукт не додається, а алерт іде єдиною відповіддю ──
    from feed.handlers import MAX_PLATE
    await press("f:new")
    for code in list(catalog.products())[:MAX_PLATE + 2]:
        await press(f"f:sel:{code}")
    assert len(ctx.user_data["draft"]) == MAX_PLATE, ctx.user_data["draft"]
    await press("f:home")
    assert ctx.user_data["draft"] == []
    print(f"ліміт тарілки {MAX_PLATE} продуктів працює")

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
    await press("f:rec:all")
    for r in catalog.recipes():
        await press(f"f:r:{r['id']}")
    await press("f:rfridge")
    await press("f:rfind")
    await say("курка")
    assert "🔍" in bot.sent[-1] and "Рецептів" in bot.sent[-1], bot.sent[-1]
    await press("f:rfind")
    await say("гарбузовий суп")                  # пошук за НАЗВОЮ страви
    assert "Гарбуз" in bot.sent[-1], bot.sent[-1]
    await press("f:rfind")
    await say("хтозна що")                       # має попросити ще раз
    ctx.user_data.pop("await", None)
    from feed import search
    named = search.recipes_by_name("сирники")
    assert named and "ирник" in named[0]["title"], named
    assert not search.find("хтозна що")["recipes"]
    print(f"пошук за назвою страви: «сирники» -> {named[0]['title']}, "
          f"«гарбузовий суп» -> {search.find('гарбузовий суп')['recipes'][0]['title']}")
    found = catalog.recipes_with(["kurka"])
    assert found, "пошук рецептів за продуктом нічого не знайшов"
    covered = {c for r in catalog.recipes() for c in r["products"]}
    print(f"рецепти: {len(catalog.recipes())} штук, покрито {len(covered)} продуктів, "
          f"пошук «курка» -> {len(found)}")
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
    # вільний текст веде в пошук, а не на головну
    upd = Update(bot, max_u, text="у нас є гречка та індичка")
    await handlers.on_free_text(upd, ctx)
    assert "🔍" in bot.sent[-1] and "Продукти в запиті" in bot.sent[-1], bot.sent[-1]
    await press("f:hv")
    assert "У тебе вже є" in bot.sent[-1], bot.sent[-1]
    upd = Update(bot, max_u, text="сирники")
    await handlers.on_free_text(upd, ctx)
    assert "ирник" in bot.sent[-1], bot.sent[-1]
    print("вільний текст: «гречка та індичка» -> продукти + тарілка, «сирники» -> рецепт")

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

    # ── рідкісні продукти ──
    rare_codes = [c for c in catalog.products() if catalog.is_rare(c)]
    assert rare_codes, "жоден продукт не позначений як рідкісний"
    assert all(not catalog.is_rare(p["code"]) for p in catalog.by_key("veg", rare=False))
    gen_rare = suggest.generate(24, {}, 40)
    assert not any(catalog.is_rare(c) for pl in gen_rare for c in pl), \
        "генератор пропонує екзотику"
    buy = suggest.shopping(24, {}, [])
    assert not any(catalog.is_rare(c) for c, _ in buy), "список покупок пропонує екзотику"
    await press("f:lib")
    await press("f:lg:veg")
    await press("f:rare:lg:veg")                 # показати
    assert db.get_kv(fid, "rare") == "1"
    await press("f:rare:lg:veg")                 # сховати
    assert db.get_kv(fid, "rare") == "0"
    await press("f:rare:lib")
    await press("f:rare:set")
    await press("f:new")
    await press("f:rare:grp:veg")
    await press("f:fradd:veg")
    await press("f:rare:fradd:veg")
    await press("f:set")
    print(f"рідкісні: {len(rare_codes)} позицій сховано, генератор і покупки їх не беруть")

    # ── тарілка з холодильника: перша вкладка запису ──
    await press("f:frtext")
    await say("морква, гречка, індичка, олія")
    await press("f:new")
    assert ctx.user_data["grp"] == "fridge", ctx.user_data.get("grp")
    assert "З холодильника" in bot.sent[-1], bot.sent[-1]
    await press("f:sel:morkva")
    assert ctx.user_data["grp"] == "fridge", "вибір продукту зіскочив з вкладки"
    assert "З холодильника" in bot.sent[-1], bot.sent[-1]
    await press("f:grp:fav")
    await press("f:sel:grechka")
    assert ctx.user_data["grp"] == "fav", "вибір продукту зіскочив з «Часте»"
    print("запис їжі: перша вкладка це холодильник, вибір не стрибає між полицями")

    # ── запис заднім числом ──
    await press("f:new")
    await press("f:sel:morkva")
    await press("f:save")
    await press("f:day:1")                       # вчора
    await press("f:save:lunch")
    last = db.q1("SELECT ts FROM meal ORDER BY id DESC LIMIT 1")["ts"]
    y_from, y_to, _ = ui.day_bounds(-1)
    assert y_from <= last < y_to, "запис не потрапив у вчорашній день"
    assert "day" not in ctx.user_data, "обраний день не скинувся після запису"
    record = bot.sent[-2] if bot.sent[-2].startswith("✅") else bot.sent[-1]
    assert "," in record.split("·")[1], f"у записі за вчора немає дати: {record}"
    await press("f:new")
    await press("f:sel:banan")
    await press("f:save")
    await press("f:day:ask")
    from datetime import date as _date, timedelta as _td
    old = _date.today() - _td(days=5)
    await say(old.strftime("%d.%m.%Y"))
    await press("f:save:dinner")
    last = db.q1("SELECT ts FROM meal ORDER BY id DESC LIMIT 1")["ts"]
    o_from, o_to, _ = ui.bounds_of(old)
    assert o_from <= last < o_to, "запис не потрапив у вказану дату"
    await press("f:day:0")
    print(f"запис заднім числом: вчора і {old.strftime('%d.%m')} лягли у свої дні")

    # ── що ввести далі ──
    await press("f:next")
    hold = suggest.holding(db.intro_map(child["id"]), db.now())
    if hold:
        assert "вводити рано" in bot.sent[-1], bot.sent[-1]
        for code in hold:
            db.set_status(child["id"], code, "ok")
        await press("f:next")
    items = suggest.next_products(ui.months_of(child), db.intro_map(child["id"]),
                                  db.pantry_codes(fid))
    assert items, "не запропоновано жодного наступного продукту"
    assert all(c not in db.intro_map(child["id"]) for c, _ in items), "пропонує вже введене"
    assert not any(catalog.is_rare(c) for c, _ in items), "пропонує екзотику"
    await press("f:nxfr")
    assert set(ctx.user_data["next"]) <= set(db.pantry_codes(fid)), \
        "«додати в холодильник» не поклало продукти"
    db.set_status(child["id"], items[0][0], "watch")
    db.touch_intro(child["id"], items[0][0], db.now())
    await press("f:next")
    assert "вводити рано" in bot.sent[-1], "правило трьох днів не спрацювало"
    print(f"що ввести далі: {len(items)} кандидати, правило трьох днів тримає паузу")

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

    # код просто в чат від того, хто вже завів свій профіль (21.10 пошук ковтнув код)
    nana, ctx_nana = User(303, "Nana"), Context(bot)
    db.add_child(db.create_family(nana.id, "Nana"), "Дубль", child["birth_date"], "girls")
    await press("f:inv")
    code = db.q1("SELECT code FROM invite WHERE used_by IS NULL")["code"]
    await handlers.on_free_text(Update(bot, nana, text="код 000AAA0"), ctx_nana)
    await handlers.on_free_text(Update(bot, nana, text="У мене є код запрошення 0AB12C"), ctx_nana)
    assert db.child_of(nana.id)["name"] == "Дубль", "невалідний код щось змінив"
    await handlers.on_free_text(Update(bot, nana, text=f"У мене є код запрошення {code.lower()}"), ctx_nana)
    assert db.child_of(nana.id)["name"] == "Дубль", "вступ без підтвердження"
    await press(f"f:joincode:{code}", nana, ctx_nana)
    assert db.child_of(nana.id)["id"] == child["id"], "код з тексту не приєднав"
    await handlers.on_free_text(Update(bot, nana, text=code), ctx_nana)
    db.run("DELETE FROM member WHERE user_id=?", (nana.id,))  # далі тести рахують двох дорослих
    print("код запрошення текстом: питає, вступає лише після кнопки")

    # ── вітамін D: одне нагадування у вибраний час, своя задача на родину ──
    fam = db.member(max_u.id)["family_id"]
    jobs = lambda: Context.jq.get_jobs_by_name(f"feed_vitd:{fam}")
    await press("f:set")
    await press("f:vd")
    assert not db.vitd_conf(fam)["on"] and not jobs()
    await press("f:vd:on")
    conf = db.vitd_conf(fam)
    assert conf["on"] and conf["at"] == db.VITD_DEFAULT_AT and conf["every"] == 2, conf
    assert len(jobs()) == 1 and jobs()[0].at.hour == 9
    await press("f:vd:ev:1")
    await press("f:vd:ev:2")
    await press("f:vd:from:1")
    await press("f:vd:at:0730")
    assert len(jobs()) == 1 and (jobs()[0].at.hour, jobs()[0].at.minute) == (7, 30), "задача не переставилась"
    await press("f:vd:attext")
    await say("25:00")
    await say("20.15")
    assert db.vitd_conf(fam)["at"] == "20:15" and jobs()[0].at.hour == 20
    await press("f:vd:to:me")
    assert db.vitd_conf(fam)["chat"] == max_u.id
    await press("f:vd:to:all")
    assert db.vitd_conf(fam)["chat"] is None
    tick = Context(bot)
    tick.job = _Job(Context.jq, "t", None, fam)
    await press("f:vd:from:0")
    n = len(bot.sent)
    await reminders.vitamin_d(tick)
    got = [t for t in bot.sent[n:] if "вітамін D" in t]
    assert len(got) == 2, f"день D: чекали обом дорослим, прийшло {len(got)}"
    await press("f:vd:from:1")
    n = len(bot.sent)
    await reminders.vitamin_d(tick)
    assert not any("вітамін D" in t for t in bot.sent[n:]), "через день: прийшло не в свій день"
    await press("f:vdok")
    await press("f:vd:on")
    assert not db.vitd_conf(fam)["on"] and not jobs(), "вимкнене лишило задачу"
    await press("f:vd:on")
    assert db.vitd_conf(fam)["at"] == "20:15", "повторне увімкнення скинуло час"
    print("вітамін D: вкл/викл, частота, день відліку, час кнопкою і текстом, кому; задача одна")

    # ── подача з ложки ──
    await press("f:style")
    assert db.get_kv(fam, "style") == "spoon"
    await press("f:p:makarony")
    assert "з ложки" in bot.sent[-1] and "Шматочками" in bot.sent[-1], bot.sent[-1][:300]
    await press("f:next")
    await press("f:nxo")
    await press("f:nxo")
    await press("f:plan")
    assert "План на завтра" in bot.sent[-1], bot.sent[-1][:300]
    first_new = ctx.user_data.get("plan_new")
    await press("f:plan:m")
    assert ctx.user_data.get("plan_new") == first_new, "«інше меню» змінило новий продукт"
    await press("f:plan:o")
    assert ctx.user_data.get("plan_new") != first_new or first_new is None, "«інший новий» не змінив"
    for _ in range(4):
        await press("f:plan:o")
    await press("f:style")
    assert db.get_kv(fam, "style") == "blw"
    print("подача: перемикач ложка/шматочки, картка продукту, інші варіанти")

    # ── моя активність ──
    await press("f:diary")
    await press("f:act")
    assert "Моя активність" in bot.sent[-1] and "Записали ви" in bot.sent[-1]
    await press("f:act:30")
    await press("f:act", wife, ctx_wife)
    others = {i: i % 9 for i in range(1, 40)}
    t, _ = ui.activity(db.child_of(max_u.id), max_u.id, 7, others)
    assert "Місце" in str(t) or "Найближча ціль" in str(t), str(t)[:400]
    print("моя активність: 7/30 днів, порівняння з іншими")

    # ── досягнення ──
    from feed import achievements
    alisa = db.child_of(max_u.id)
    assert any("Перший запис" in t for t in bot.sent.ach), "за перший запис досягнення не прийшло"
    n_ach = len(bot.sent.ach)
    assert achievements.sync(alisa) == [], "досягнення видалось вдруге"
    await press("f:home")
    assert len(bot.sent.ach) == n_ach, "повтор досягнень на звичайній дії"
    assert "🏅" in bot.sent[-1], "значків на головній немає"
    await press("f:ach")
    assert "Досягнення" in bot.sent[-1] and "Далі" in bot.sent[-1], bot.sent[-1][:300]
    t, _ = ui.new_achievement(alisa, [("taste", 25), ("brave", 1)])
    assert "Нові досягнення" in t
    print(f"досягнення: {len(bot.sent.ach)} повідомлень, без повторів, альбом і значки на головній")

    # картки: HTML кожного шаблону збирається; браузер у смоуці не запускаємо
    from feed import cards
    for key, lvl in sorted(cards.BIG):
        assert "MalenkyiVsesvitBot" in cards.for_achievement(alisa, key, lvl)
    assert cards.for_achievement(alisa, "taste", 5) is None
    real_render = cards.render

    async def fake_render(page):
        return b"png"
    cards.render = fake_render
    db.mark_tried(alisa["id"], [c for c in catalog.products() if not catalog.product(c).get("allergen")][:30])
    await press("f:home")
    assert bot.photos and "Дослідник смаку" in bot.photos[-1][1], "велика віха без картки"
    cards.render = real_render
    from datetime import datetime as _dt
    now = _dt.now(ui.TZ)
    assert cards.month(alisa, now.year, now.month) is None or "Підсумок" in cards.month(alisa, now.year, now.month)
    print(f"картки: {len(cards.BIG)} шаблонів віх, фото з підписом, підсумок місяця")
    await press("f:new", wife, ctx_wife)
    await press("f:sel:banan", wife, ctx_wife)
    await press("f:save:breakfast", wife, ctx_wife)
    assert db.q1("SELECT by_user FROM meal ORDER BY id DESC LIMIT 1")["by_user"] == wife.id
    assert sorted(db.family_chats(db.member(max_u.id)["family_id"])) == [101, 202]
    print("спільний профіль: обидва дорослі пишуть в один щоденник")

    # ── нагадування ──
    db.run("UPDATE nudge SET due_ts=? WHERE done=0", (db.now() - 10,))
    before = len(bot.sent)
    await reminders.evening_digest(Context(bot))
    digests = [s for s in bot.sent[before:] if s.startswith("<b>🌙")]
    adults = len(db.family_chats(db.member(max_u.id)["family_id"]))
    assert len(digests) == adults, f"підсумків {len(digests)}, очікував по одному на дорослого"
    assert len(set(digests)) == 1, "дорослі отримали різні підсумки"

    # головне: повтор не має надсилати те саме ще раз (саме це і був спам огірками)
    before = len(bot.sent)
    await reminders.evening_digest(Context(bot))
    await reminders.evening_digest(Context(bot))
    again = [s for s in bot.sent[before:] if s.startswith("<b>🌙")]
    assert not again, f"підсумок повторився {len(again)} раз(и) без нових подій"

    # правило трьох днів закривається саме, без питання до батьків
    assert not db.q("SELECT 1 FROM nudge WHERE done=0 AND kind='three_day' AND due_ts<=?",
                    (db.now(),)), "three_day лишився відкритим"
    nid = db.q1("SELECT id FROM nudge WHERE kind='reaction_check' ORDER BY id DESC LIMIT 1")
    if nid:
        await press(f"f:nudge:{nid['id']}:ok")
        assert db.q1("SELECT done FROM nudge WHERE id=?", (nid["id"],))["done"] == 1
    print(f"нагадування: один підсумок на добу, повтору немає, кнопка закриває")

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

    # ── зворотний звʼязок ──
    os.environ["FEEDBACK_TO"] = "909"
    owner = User(909, "Author")
    mom = User(303, "Mom")
    ctx_mom = Context(bot)
    await press("f:guide", mom, ctx_mom)
    assert "f:fb" in json.dumps(ui.guide("start")[1].to_dict()), "в інструкції нема «Написати автору»"
    await press("f:fb", mom, ctx_mom)
    await say("Не можу змінити дату народження", mom, ctx_mom)
    fb = db.q1("SELECT * FROM feedback WHERE user_id=? AND kind='msg'", (mom.id,))
    assert fb and "дату" in fb["text"], "відгук не збережено"
    assert any(c == 909 and "Відгук #" in t for c, t in bot.to), "відгук не дійшов автору"
    link = db.q1("SELECT * FROM feedback_msg WHERE feedback_id=?", (fb["id"],))
    reply = Message(bot, "Налаштування → Змінити дату народження")
    reply.chat_id, reply.reply_to_message = 909, _Msg(link["message_id"])
    upd = Update(bot, owner, text=reply.text)
    upd.message = reply
    try:
        await feedback.on_owner_reply(upd, Context(bot))
        raise AssertionError("reply автора не зупинив обробку")
    except ApplicationHandlerStop:
        pass
    assert any(c == mom.id and "Відповідь автора" in t for c, t in bot.to), "відповідь не дійшла людині"
    assert db.q1("SELECT answered_ts FROM feedback WHERE id=?", (fb["id"],))["answered_ts"]
    # чужий reply (не автор) і reply не на відгук - пропускаються далі
    upd.effective_user = mom
    await feedback.on_owner_reply(upd, Context(bot))
    # пошук без результатів, схожий на питання
    await handlers.cmd_start(Update(bot, mom, text="/start"), ctx_mom)
    await press("f:setup", mom, ctx_mom)
    await say("Соня", mom, ctx_mom)
    # дата кнопками: рік → місяць → тиждень, з поверненнями назад
    await press("f:by:2026", mom, ctx_mom)
    await press("f:bback", mom, ctx_mom)
    await press("f:by:2026", mom, ctx_mom)
    await press("f:bm:2026:4", mom, ctx_mom)
    await press("f:bd:2026:4:4", mom, ctx_mom)
    await press("f:sex:girls", mom, ctx_mom)
    await press("f:ostyle:spoon", mom, ctx_mom)
    sonia = db.child_of(mom.id)
    assert db.get_kv(sonia["family_id"], "style") == "spoon", "подача з онбордингу не лягла"
    assert sonia and sonia["birth_date"] == "2026-04-04", "дата кнопками не лягла"
    # перший запис одразу після профілю: спробуване = освоєне, без нагадувань про реакцію
    for code in ["kabachok", "garbuz", "garbuz", "garbuz"]:
        await press(f"f:ft:{code}", mom, ctx_mom)
    await press("f:fsave", mom, ctx_mom)
    tried = db.intro_map(sonia["id"])
    assert set(tried) == {"kabachok", "garbuz"} and all(r["status"] == "ok" for r in tried.values()), \
        f"перший запис не збережено: {dict(tried)}"
    assert not db.q1("SELECT 1 FROM nudge WHERE child_id=?", (sonia["id"],)), "спробуване дало нагадування"
    await press("f:ft:banan", mom, ctx_mom)
    await press("f:fother", mom, ctx_mom)
    assert ctx_mom.user_data.get("draft") == ["banan"], "відмічене не перейшло в тарілку"
    await press("f:fnone", mom, ctx_mom)
    await press("f:first", mom, ctx_mom)   # вхід з розсилки для старих профілів
    print("онбординг v2: дата кнопками, перший запис 2 продукти, «Інше» і «Ще не почали»")
    await handlers.on_free_text(Update(bot, mom, text="А якщо вона їсть все?"), ctx_mom)
    assert ctx_mom.user_data.get("fb_draft"), "питання без результатів не запропоновано автору"
    await press("f:fbsend", mom, ctx_mom)
    assert db.q1("SELECT 1 FROM feedback WHERE user_id=? AND kind='search'", (mom.id,))
    # питання на 3-й день: один раз, 👎 веде в текст
    db.run("UPDATE family SET created_at=created_at-4*86400")
    await feedback.ask_once(Context(bot))
    await feedback.ask_once(Context(bot))
    asks = db.q1("SELECT COUNT(*) n FROM feedback WHERE user_id=? AND kind='ask'", (mom.id,))["n"]
    assert asks == 1, f"питання поставлено {asks} разів"
    await press("f:rate:down", mom, ctx_mom)
    await say("Мало рецептів з рибою", mom, ctx_mom)
    assert db.q1("SELECT 1 FROM feedback WHERE user_id=? AND kind='rate' AND rating='down' "
                 "AND text LIKE 'Мало%'", (mom.id,)), "👎 з текстом не збережено"
    await press("f:rate:up", mom, ctx_mom)
    print("зворотний звʼязок: кнопка → автор → reply → людина; пошук-питання; 3-й день один раз")

    # ── режим «Готуємось»: діти до 6 міс ──
    from datetime import datetime, timedelta
    today = datetime.now(ui.TZ).date()
    kids = {}
    for uid, nm, age_d in ((401, "Тримісячна", 91), (402, "Пʼятимісячна", 152),
                           (403, "Завтрашня", 182), (404, "Шестимісячна", 183),
                           (405, "Старша", 300)):
        usr = User(uid, nm)
        fid = db.create_family(uid, nm)
        db.add_child(fid, nm, (today - timedelta(days=age_d)).isoformat(), "girls")
        kids[nm] = (usr, Context(bot), db.child_of(uid))
    assert growth.age_months(today - timedelta(days=183)) == 6 and \
        growth.age_months(today - timedelta(days=182)) == 5, "month_date розходиться з age_months"
    for nm, expect in (("Тримісячна", True), ("Пʼятимісячна", True), ("Завтрашня", True),
                       ("Шестимісячна", False), ("Старша", False)):
        usr, cx, ch = kids[nm]
        home = ui.home(ch, db.intro_map(ch["id"]))
        plain = rich.to_tg(str(home[0]))
        assert ("Готуємось до прикорму" in plain) == expect, f"{nm}: блок «Готуємось» на головній: {not expect}"
        assert any(b.callback_data == "f:prep" for r in home[1].inline_keyboard for b in r) == expect, nm
        await press("f:home", usr, cx)
        await press("f:prep", usr, cx)
        await press("f:prep:week", usr, cx)
        for scr in (ui.prep(ch)[0], ui.prep_week(ch, db.intro_map(ch["id"]))[0]):
            assert "—" not in str(scr), f"{nm}: довге тире в тексті"
    assert "Завтрашня" and "лишився 1 день" in rich.to_tg(str(ui.home(kids["Завтрашня"][2], {})[0]))
    print("це головна 5-міс:", rich.to_tg(str(ui.home(kids["Пʼятимісячна"][2], {})[0]))[:400].replace("\n", " | "))
    before = len(bot.sent)
    await reminders.six_months(Context(bot))
    got = [t for t in bot.sent[before:] if "сьогодні 6 місяців" in t]
    assert len(got) == 1 and "Шестимісячна" in got[0], f"нагадування про 6 міс: {len(got)}"
    await reminders.six_months(Context(bot))
    await reminders.six_months(Context(bot))
    assert len([t for t in bot.sent[before:] if "сьогодні 6 місяців" in t]) == 1, "нагадування повторилось"
    print("«Готуємось»: головна 3/5/6 міс, розділ, план тижня; нагадування о 11:00 рівно одне")

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
