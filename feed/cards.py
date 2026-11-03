"""Картки-картинки 1080×1350 на великі досягнення і підсумок місяця.

Малює Chromium (Playwright) з HTML, як зразки 21.10. Великих віх у дитини лічені
одиниці, тому браузер запускається на кожну картку і одразу закривається.
Внизу кожної картки назва бота: ділитися ніхто не просить, але як поділяться -
видно, звідки.
"""
from __future__ import annotations

import html
import logging

from . import achievements, catalog, db, growth, suggest, ui

log = logging.getLogger("feed.cards")

BIG = {("taste", 25), ("taste", 50), ("taste", 100), ("brave", 1), ("brave", 3),
       ("brave", 6), ("brave", 9), ("streak", 30), ("diary", 100)}

CSS = """
:root{--cream:#FFF7EC;--ink:#1F2A24;--muted:#5E6A62;--green:#2E8B4E;--coral:#F2704E;--sun:#F6B93B;--line:#EADFCF}
body{margin:0;font-family:Inter,"Noto Color Emoji",sans-serif;color:var(--ink)}
.card{width:1080px;height:1350px;box-sizing:border-box;padding:76px 84px 64px;display:flex;flex-direction:column;overflow:hidden}
.c1{background:var(--cream)}.c2{background:#FFF1E6}.c3{background:#EEF6EC}.c4{background:#1F2A24;color:#FFF7EC}
.kick{font:600 30px Inter;letter-spacing:.06em;text-transform:uppercase;color:var(--coral)}
.c3 .kick{color:var(--green)}.c4 .kick{color:var(--sun)}
h1{font:800 100px/1.03 Inter;margin:22px 0 0;letter-spacing:-.02em}
.sub{font:500 40px/1.35 Inter;color:var(--muted);margin-top:20px}.c4 .sub{color:#C9D3CC}
.grid{display:grid;grid-template-columns:repeat(5,118px);justify-content:space-between;row-gap:14px;margin-top:40px}
.st{width:118px;height:118px;border-radius:50%;background:#fff;display:flex;align-items:center;justify-content:center;font-size:58px;box-shadow:0 5px 0 var(--line)}
.foot{margin-top:auto;padding-top:28px;font:600 30px Inter;color:var(--muted)}.c4 .foot{color:#9FB0A5}
.bar{height:26px;border-radius:13px;background:#EADFCF;overflow:hidden;margin-top:18px}
.bar i{display:block;height:100%;background:var(--green);border-radius:13px}
.rank{display:flex;justify-content:space-between;font:600 32px Inter;margin-top:44px}
.alg{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin-top:48px}
.a{border-radius:24px;padding:22px 24px;font:600 32px Inter;background:#fff;border:3px solid var(--line)}
.a.on{border-color:var(--green);background:#E7F4EA}.a.now{border-color:var(--coral);background:#FFE3D8}.a.off{color:#9A9488}
.huge{font:800 380px/1 Inter;letter-spacing:-.04em;margin-top:60px;color:var(--green)}
.kp{display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-top:56px}
.k{background:#2B3A31;border-radius:32px;padding:34px 36px}
.k b{display:block;font:800 92px/1 Inter;color:#FFF7EC}.k span{display:block;font:500 30px Inter;color:#C9D3CC;margin-top:12px}
.row{font-size:64px;letter-spacing:10px;margin-top:44px}
"""
FOOT = '<div class="foot">Щоденник прикорму · @MalenkyiVsesvitBot</div>'

MONTHS = ("січня", "лютого", "березня", "квітня", "травня", "червня", "липня", "серпня",
          "вересня", "жовтня", "листопада", "грудня")
MONTH_NOM = ("Січень", "Лютий", "Березень", "Квітень", "Травень", "Червень", "Липень",
             "Серпень", "Вересень", "Жовтень", "Листопад", "Грудень")


GROUP_EMOJI = {"Молоко": "🥛", "Яйце": "🥚", "Риба": "🐟", "Глютен": "🌾", "Арахіс": "🥜",
               "Горіхи": "🌰", "Кунжут": "🫘", "Селера": "🥬", "Морепродукти": "🦐"}


def _e(text) -> str:
    return html.escape(str(text))


def _page(cls: str, body: str, foot: str = FOOT) -> str:
    return (f'<!doctype html><html lang="uk"><head><meta charset="utf-8"><style>{CSS}</style>'
            f'</head><body><section class="card {cls}">{body}{foot}</section></body></html>')


def _girl(child) -> bool:
    return child["sex"] == "girls"


def _age(child) -> str:
    return growth.age_text(ui.birth_date(child))


def taste(child, lvl: int) -> str:
    intro = db.intro_map(child["id"])
    codes = sorted((c for c, r in intro.items() if r["status"] != "avoid" and catalog.product(c)),
                   key=lambda c: intro[c]["first_ts"])
    first = min((intro[c]["first_ts"] for c in codes), default=db.now())
    weeks = max((db.now() - first) // (7 * 86400), 1)
    emojis = list(dict.fromkeys(catalog.product(c)["emoji"] for c in reversed(codes)))[:25]
    stickers = "".join(f'<div class="st">{e}</div>' for e in reversed(emojis))
    levels = achievements.BY_KEY["taste"][3]
    nxt = next((x for x in levels if x > lvl), None)
    rank = ""
    if nxt:
        rank = (f'<div class="rank"><span>Далі: {nxt} продуктів</span>'
                f'<span style="color:var(--muted)">ще {nxt - len(codes)}</span></div>'
                f'<div class="bar"><i style="width:{min(100, round(100 * len(codes) / nxt))}%"></i></div>')
    verb = "спробувала" if _girl(child) else "спробував"
    return _page("c1", f'<div class="kick">🏅 Нове досягнення</div>'
                       f'<h1>{_e(child["name"])} {verb} {lvl} продуктів</h1>'
                       f'<div class="sub">{_e(_age(child))} · за {ui.plural(weeks, "тиждень", "тижні", "тижнів")} прикорму</div>'
                       f'<div class="grid">{stickers}</div>{rank}')


def brave(child, lvl: int) -> str:
    intro = db.intro_map(child["id"])
    done = achievements.allergens_done(intro)

    def since(group: str) -> int:
        keys = achievements.ALLERGEN_GROUPS[group]
        return min((r["first_ts"] for c, r in intro.items()
                    if r["status"] == "ok" and keys & suggest.allergen_keys(c)), default=0)

    latest = max(done, key=since) if done else ""
    cells = "".join(
        f'<div class="a {"now" if g == latest else "on" if g in done else "off"}">'
        f'{"✓" if g in done else "🔒"} {_e(g if len(g) < 10 else g[:7] + ".")}</div>'
        for g in achievements.ALLERGEN_GROUPS)
    title = f"Новий алерген: {latest.lower()}" if latest else "Сміливець"
    return _page("c2", f'<div class="kick">❗ Ачівка «Сміливець»</div><h1>{_e(title)}</h1>'
                       f'<div class="sub">Без реакції. {_e(child["name"])} {"молодчинка" if _girl(child) else "молодець"}.</div>'
                       f'<div class="alg">{cells}</div>'
                       f'<div class="rank"><span>Алергени введено</span><span>{len(done)} з 9</span></div>'
                       f'<div class="bar"><i style="width:{round(100 * len(done) / 9)}%;background:var(--coral)"></i></div>'
                       f'<div style="margin-top:auto;text-align:right;font-size:220px;line-height:1">'
                       f'{GROUP_EMOJI.get(latest, "🏅")}</div>')


def number(child, key: str, lvl: int) -> str:
    _, emoji, title, _ = achievements.BY_KEY[key]
    if key == "streak":
        head = f"{lvl} днів поспіль"
        sub = "щодня щось записали в щоденник прикорму"
        dots = "".join('<div style="width:76px;height:76px;border-radius:20px;background:var(--green);'
                       'color:#fff;display:flex;align-items:center;justify-content:center;font:700 34px Inter">'
                       f'{i}</div>' for i in range(1, lvl + 1))
        art = ('<div style="display:grid;grid-template-columns:repeat(10,76px);justify-content:space-between;'
               f'row-gap:18px;margin-top:64px">{dots}</div>'
               '<div style="margin-top:auto;text-align:right;font-size:260px;line-height:1">🔥</div>')
    else:
        head = f"{lvl} прийомів у щоденнику"
        sub = "кожен записаний сніданок, обід і вечеря"
        art = '<div style="margin-top:auto;text-align:right;font-size:300px;line-height:1">🍽</div>'
    return _page("c3", f'<div class="kick">{emoji} Ачівка «{_e(title)}»</div><h1>{_e(head)}</h1>'
                       f'<div class="sub">{_e(child["name"])}, {growth.age_months(ui.birth_date(child))} міс · {sub}</div>'
                       f'{art}')


def month(child, year: int, mon: int, story: bool = False) -> str | None:
    """Підсумок місяця. None - якщо місяць порожній (менше 10 прийомів).
    story=True - 1080×1920 для сторіз (_story)."""
    start = int(ui.datetime(year, mon, 1, tzinfo=ui.TZ).timestamp())
    end = int(ui.datetime(year + mon // 12, mon % 12 + 1, 1, tzinfo=ui.TZ).timestamp())
    meals = db.q("SELECT id FROM meal WHERE child_id=? AND ts>=? AND ts<?", (child["id"], start, end))
    if len(meals) < 10:
        return None
    intro = db.intro_map(child["id"])
    new = [c for c, r in intro.items() if start <= r["first_ts"] < end and catalog.product(c)]
    fav = db.q("SELECT mi.code, COUNT(*) n FROM meal_item mi JOIN meal m ON m.id=mi.meal_id "
                "WHERE m.child_id=? AND m.ts>=? AND m.ts<? GROUP BY mi.code ORDER BY n DESC LIMIT 20",
                (child["id"], start, end))
    # улюблене - їжа, а не олія чи масло, які йдуть у кожну тарілку
    fav = next((r for r in fav if (catalog.product(r["code"]) or {}).get("group") not in ("fat", "other", None)), None)
    allerg = [c for c in new if catalog.product(c).get("allergen") and intro[c]["status"] == "ok"]
    fav_cell = (f'<div class="k"><b>{catalog.product(fav["code"])["emoji"]}</b><span>улюблене: '
                f'{_e(catalog.product(fav["code"])["name"].lower())}, '
                f'{ui.plural(fav["n"], "раз", "рази", "разів")}</span></div>') if fav else ""
    row = "".join(list(dict.fromkeys(catalog.product(c)["emoji"] for c in new))[:8])
    if story:
        return _story(child, year, mon, len(meals), new, allerg, fav, intro)
    return _page("c4", f'<div class="kick">📅 Підсумок місяця</div>'
                       f'<h1>{MONTH_NOM[mon - 1]} {year}</h1>'
                       f'<div class="sub">{_e(child["name"])} · {_e(_age(child))}</div>'
                       f'<div class="kp"><div class="k"><b>+{len(new)}</b><span>'
                       f'{ui.plural(len(new), "новий продукт", "нові продукти", "нових продуктів").split(" ", 1)[1]}</span></div>'
                       f'<div class="k"><b>{len(allerg)}</b><span>'
                       f'{ui.plural(len(allerg), "алерген", "алергени", "алергенів").split(" ", 1)[1]} без реакцій</span></div>'
                       f'<div class="k"><b>{len(meals)}</b><span>'
                       f'{ui.plural(len(meals), "прийом", "прийоми", "прийомів").split(" ", 1)[1]} їжі</span></div>{fav_cell}</div>'
                       + (f'<div class="row">{row}</div><div class="sub" style="font-size:32px;margin-top:18px">'
                          f'нове цього місяця</div>' if row else ""))


STORY_CSS = """
body{margin:0;font-family:Inter,"Noto Color Emoji",sans-serif;color:#2A1B14}
.s{width:1080px;height:1920px;box-sizing:border-box;padding:230px 80px 290px;overflow:hidden;position:relative;
   background:linear-gradient(165deg,#FFE0CC 0%,#FFF3E2 48%,#FFE9A8 100%)}
.in{position:relative;display:flex;flex-direction:column;height:100%}
.blob{position:absolute;border-radius:50%;opacity:.55}
.k{font:700 30px Inter;letter-spacing:.14em;text-transform:uppercase;color:#E2552F}
.who{font:800 76px/1.05 Inter;margin-top:26px;letter-spacing:-.02em}
.big{font:900 330px/.82 Inter;letter-spacing:-.05em;color:#E2552F;margin-top:14px}
.unit{font:800 76px/1.05 Inter;letter-spacing:-.02em;margin-top:-6px}
.wall{display:flex;flex-wrap:wrap;gap:20px 18px;margin:48px 0 44px}
.st{width:140px;height:140px;border-radius:50%;background:#fff;display:flex;align-items:center;justify-content:center;
    font-size:78px;box-shadow:0 8px 0 rgba(226,85,47,.18),0 14px 28px rgba(120,60,20,.12)}
.more{font:800 46px Inter;color:#E2552F}
.chips{display:flex;gap:18px;margin-top:auto}
.c{flex:1;background:rgba(255,255,255,.78);border-radius:34px;padding:28px 26px}
.c b{display:block;font:800 72px/1 Inter}.c span{display:block;font:600 27px/1.25 Inter;color:#7A5A48;margin-top:10px}
.fav{display:flex;align-items:center;gap:28px;background:#2A1B14;color:#FFF3E2;border-radius:40px;padding:30px 36px;margin-top:22px}
.fav i{font-style:normal;font-size:110px;line-height:1}.fav b{display:block;font:800 52px/1.1 Inter}
.fav span{display:block;font:600 28px Inter;color:#E8CDB8;margin-bottom:6px;letter-spacing:.08em;text-transform:uppercase}
.ft{margin-top:30px;font:600 30px Inter;color:#7A5A48;text-align:center}.ft b{color:#2A1B14}
"""
TILT = (-6, 4, -3, 7, -5, 2, 6, -4, 3, -7, 5, -2, 4, -6, 3)


def _word(n: int, one: str, few: str, many: str) -> str:
    return ui.plural(n, one, few, many).split(" ", 1)[1]


def _story(child, year, mon, n_meals, new, allerg, fav, intro) -> str:
    """Підсумок місяця 1080×1920 для сторіз. Герой - дитина і її нові смаки; бот лише підписом
    унизу, бо рекламою не діляться. Поля 230/290 - там кнопки Instagram."""
    girl = _girl(child)
    month_l = MONTH_NOM[mon - 1].lower()
    new = sorted(new, key=lambda c: intro[c]["first_ts"])
    emojis = list(dict.fromkeys(catalog.product(c)["emoji"] for c in new))
    shown, rest = emojis[:15], len(emojis) - 15
    wall = "".join(f'<div class="st" style="transform:rotate({TILT[i]}deg)">{e}</div>' for i, e in enumerate(shown))
    if rest > 0:
        wall += f'<div class="st more">+{rest}</div>'
    if new:
        head = (f'<div class="who">{_e(child["name"])} за {month_l} {"спробувала" if girl else "спробував"}</div>'
                f'<div class="big">{len(new)}</div>'
                f'<div class="unit">{_word(len(new), "новий смак", "нові смаки", "нових смаків")}</div>')
    else:
        head = (f'<div class="who">{_e(child["name"])} за {month_l}</div><div class="big">{n_meals}</div>'
                f'<div class="unit">{_word(n_meals, "прийом", "прийоми", "прийомів")} їжі</div>')
    months = growth.age_months(ui.birth_date(child))
    chips = (f'<div class="c"><b>{n_meals}</b><span>{_word(n_meals, "прийом", "прийоми", "прийомів")} їжі</span></div>'
             f'<div class="c"><b>{len(allerg)}</b><span>{_word(len(allerg), "алерген", "алергени", "алергенів")} '
             f'без реакцій</span></div>'
             f'<div class="c"><b>{months}</b><span>{_word(months, "місяць", "місяці", "місяців")} '
             f'{"їй" if girl else "йому"}</span></div>')
    fav_html = ""
    if fav:
        pr = catalog.product(fav["code"])
        fav_html = (f'<div class="fav"><i>{pr["emoji"]}</i><div><span>Смак місяця</span>'
                    f'<b>{_e(pr["name"])}, {ui.plural(fav["n"], "раз", "рази", "разів")}</b></div></div>')
    blobs = ('<div class="blob" style="width:520px;height:520px;background:#FFB48F;top:-160px;right:-180px"></div>'
             '<div class="blob" style="width:420px;height:420px;background:#FFD66B;bottom:120px;left:-220px"></div>')
    body = (f'{blobs}<div class="in"><div class="k">🥄 Підсумок прикорму · {MONTH_NOM[mon - 1]} {year}</div>'
            f'{head}<div class="wall">{wall}</div><div class="chips">{chips}</div>{fav_html}'
            f'<div class="ft">Щоденник прикорму <b>@MalenkyiVsesvitBot</b></div></div>')
    return (f'<!doctype html><html lang="uk"><head><meta charset="utf-8"><style>{STORY_CSS}</style>'
            f'</head><body><section class="card s">{body}</section></body></html>')


def for_achievement(child, key: str, lvl: int) -> str | None:
    if (key, lvl) not in BIG:
        return None
    if key == "taste":
        return taste(child, lvl)
    if key == "brave":
        return brave(child, lvl)
    return number(child, key, lvl)


STORY_CAPTION = ("📱 <b>Версія для сторіз.</b> Збережіть картинку і додайте в історію. "
                 "Щоб друзі відкрили бот одним дотиком, додайте стікер «Посилання» з цим посиланням:\n")


def story_button(child_id: int, year: int, mon: int):
    return ui.B("📱 Версія для сторіз", callback_data=f"f:mst:{child_id}:{year}{mon:02d}")


async def render(page_html: str, height: int = 1350) -> bytes:
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": 1080, "height": height})
            await page.set_content(page_html, wait_until="load")
            return await page.locator(".card").screenshot(type="png")
        finally:
            await browser.close()


async def monthly(context) -> None:
    """1-го числа: підсумок минулого місяця картинкою. Лише тим, у кого за місяць
    набралось 10+ прийомів, і хто не заблокував бота. Своя родина автора теж отримує."""
    from telegram.error import TelegramError
    today = ui.datetime.now(ui.TZ).date()
    if today.day != 1:
        return
    year, mon = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    sent = 0
    for child in db.q("SELECT * FROM child"):
        page = month(child, year, mon)
        if not page:
            continue
        mark = f"month_card:{year}-{mon:02d}"
        if db.get_kv(child["family_id"], mark):
            continue
        db.set_kv(child["family_id"], mark, str(db.now()))   # мітка по відправці: один раз
        try:
            png = await render(page)
        except Exception:
            log.exception("картка місяця для дитини %s", child["id"])
            continue
        for chat_id in db.family_chats(child["family_id"]):
            if db.q1("SELECT 1 FROM subscriber WHERE user_id=? AND blocked=1", (chat_id,)):
                continue
            try:
                await context.bot.send_photo(chat_id, png, caption=f"📅 Підсумок місяця: "
                                             f"{MONTH_NOM[mon - 1].lower()} {html.escape(child['name'])}",
                                             reply_markup=ui.M([[ui.share_button(chat_id)],
                                                                 [story_button(child["id"], year, mon)]]))
                sent += 1
            except TelegramError as exc:
                log.warning("картка місяця не дійшла %s: %s", chat_id, exc)
    if sent:
        log.info("підсумок місяця: %s карток", sent)


def install(app) -> None:
    if app.job_queue is not None:
        app.job_queue.run_daily(monthly, time=ui.datetime.strptime("11:00", "%H:%M").time().replace(tzinfo=ui.TZ),
                                name="feed_month_card")
