"""«Нам N місяців»: картка в день, коли дитині виповнюється черговий місяць.

День рахуємо тією самою міркою, що й вік на екранах (growth.month_date, 30.4375 дн.),
інакше картка «9 місяців» стояла б поруч з «8 міс 30 дн.». Від календарного числа
розходиться на ±1 день - це прийнятно.

Картка - нагорода, тому не залежить від того, чи веде родина щоденник: є прийоми -
показуємо нові смаки місяця, немає - вік і скільки до прикорму. Під карткою єдине
місце, де бот просить оновити вагу і зріст: у перший рік педіатр і так зважує щомісяця.
Більше ніде про заміри не нагадуємо.

Мітка bday:<child>:<N> у kv ставиться ДО відправки (урок 19.09): один місяць - одна картка.
У день 6 місяців картка забирає собі кнопку «План першого тижня» і ставить мітку
six_sent, щоб reminders.six_months того ж дня не писав удруге.
"""
from __future__ import annotations

import html
import logging
from datetime import date, datetime, time, timedelta

from . import achievements, catalog, db, growth, ui

log = logging.getLogger("feed.birthday")

AT = time(10, 0, tzinfo=ui.TZ)   # раніше за 11:00 (six_months, підсумок місяця)
CATCHUP = 1                       # діб: догнати, якщо бот лежав о 10:00
MAX_MONTHS = 24
FRESH_DAYS = 7                    # замір свіжіший за тиждень - не просимо оновити
KEY = "bday"


def years_text(n: int) -> str:
    """12 → «1 рік», 18 → «1 рік 6 місяців», 9 → «9 місяців»."""
    y, m = divmod(n, 12)
    parts = []
    if y:
        parts.append(ui.plural(y, "рік", "роки", "років"))
    if m:
        parts.append(ui.plural(m, "місяць", "місяці", "місяців"))
    return " ".join(parts)


def due(child, today: date) -> tuple[int, bool] | None:
    """(N, запізнення?) якщо сьогодні або вчора дитині виповнилось N місяців."""
    birth = ui.birth_date(child)
    n = growth.age_months(birth, today)
    if not 1 <= n <= MAX_MONTHS:
        return None
    late = (today - growth.month_date(birth, n)).days
    return (n, late > 0) if 0 <= late <= CATCHUP else None


def _span(child, n: int) -> tuple[int, int]:
    birth = ui.birth_date(child)
    start = datetime.combine(growth.month_date(birth, n - 1), time(0), ui.TZ)
    end = datetime.combine(growth.month_date(birth, n), time(0), ui.TZ)
    return int(start.timestamp()), int(end.timestamp())


def month_facts(child, n: int) -> dict:
    """Що сталося за останній місяць життя (від N-1 до N місяців)."""
    ts_from, ts_to = _span(child, n)
    intro = db.intro_map(child["id"])
    new = sorted((c for c, r in intro.items() if ts_from <= r["first_ts"] < ts_to and catalog.product(c)
                  and r["status"] != "avoid"), key=lambda c: intro[c]["first_ts"])
    meals = db.q1("SELECT COUNT(*) c FROM meal WHERE child_id=? AND ts>=? AND ts<?",
                  (child["id"], ts_from, ts_to))["c"]
    allerg = [c for c in new if catalog.product(c).get("allergen") and intro[c]["status"] == "ok"]
    return {"new": new, "meals": meals, "allerg": allerg, "total": len(intro),
            "algs_total": len(achievements.allergens_done(intro))}


def _w(n: int, one: str, few: str, many: str) -> str:
    return ui.plural(n, one, few, many).split(" ", 1)[1]


def _measure_line(child) -> str:
    w = db.last_measure(child["id"], "weight_kg")
    h = db.last_measure(child["id"], "height_cm")
    bits = []
    if w:
        bits.append(f"{w['weight_kg']:.2f}".rstrip("0").rstrip(".") + " кг")
    if h:
        bits.append(f"{h['height_cm']:.1f}".rstrip("0").rstrip(".") + " см")
    last = max((r["ts"] for r in (w, h) if r), default=0)
    return " · ".join(bits) + (f", замір {ui.local(last).strftime('%d.%m')}" if bits else "")


CSS = """
.bd{background:linear-gradient(170deg,#FFE3D1 0%,#FFF4E4 55%,#FFE7A6 100%);position:relative}
.bd .kick{color:#E2552F}
.bd h1{font-size:96px}
.n{display:flex;align-items:baseline;gap:26px;margin-top:6px;color:#E2552F}
.n b{font:900 270px/.95 Inter;letter-spacing:-.05em}.n span{font:800 96px Inter;letter-spacing:-.02em}
.bd .sub{color:#7A5A48;margin-top:10px}
.bd .grid{margin-top:30px}
.bd .st{box-shadow:0 6px 0 rgba(226,85,47,.18)}
.three{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;margin-top:40px}
.three div{background:rgba(255,255,255,.8);border-radius:30px;padding:26px 26px 24px}
.three b{display:block;font:800 68px/1 Inter;color:#2A1B14}
.three span{display:block;font:600 27px/1.25 Inter;color:#7A5A48;margin-top:10px}
.gr{margin-top:28px;font:600 34px Inter;color:#2A1B14}
.art{margin-top:auto;text-align:right;font-size:230px;line-height:1}
.bd .foot{margin-top:24px;color:#7A5A48}
"""


def card(child, n: int) -> str:
    from . import cards   # cards тягне playwright лише в render; тут шаблон і футер
    girl = child["sex"] == "girls"
    f = month_facts(child, n)
    if n % 12 == 0:
        big, unit, kick = str(n // 12), _w(n // 12, "рік", "роки", "роки"), "🎂 З днем народження!"
    else:
        big, unit, kick = str(n), _w(n, "місяць", "місяці", "місяців"), "🎉 Вітаємо з місяцем!"
    sub, row, stats = "", "", []
    if f["new"]:
        verb = "спробувала" if girl else "спробував"
        sub = f'За цей місяць {verb} {ui.plural(len(f["new"]), "новий смак", "нові смаки", "нових смаків")}'
        emojis = list(dict.fromkeys(catalog.product(c)["emoji"] for c in f["new"]))
        row = "".join(f'<div class="st">{e}</div>' for e in emojis[:5])
        stats = [(f'+{len(f["new"])}', _w(len(f["new"]), "новий продукт", "нові продукти", "нових продуктів"))]
    elif f["meals"]:
        sub = f'За цей місяць {ui.plural(f["meals"], "прийом", "прийоми", "прийомів")} їжі в щоденнику'
    elif n == ui.PREP_UNTIL:
        sub = "Час першої ложки 🥄"
    elif n < ui.PREP_UNTIL:
        left = (growth.month_date(ui.birth_date(child), ui.PREP_UNTIL) - growth.month_date(ui.birth_date(child), n)).days
        sub = f'До прикорму {ui.plural(left, "день", "дні", "днів")}'
    if f["total"]:
        stats.append((str(f["total"]), _w(f["total"], "продукт", "продукти", "продуктів") + " уже знайомі"))
    if f["algs_total"]:
        stats.append((f'{f["algs_total"]}/9', "алергенів без реакцій"))
    m = _measure_line(child)
    three = "".join(f'<div><b>{html.escape(v)}</b><span>{html.escape(s)}</span></div>' for v, s in stats[:3])
    size = 150 if row and m else 230   # кулька завжди: тримає підпис бота внизу картки
    art = f'<div class="art" style="font-size:{size}px">{"🎂" if n % 12 == 0 else "🎈"}</div>'
    body = (f'<div class="kick">{kick}</div><h1>{html.escape(child["name"])}</h1>'
            f'<div class="n"><b>{big}</b><span>{unit}</span></div>'
            + (f'<div class="sub">{sub}</div>' if sub else "")
            + (f'<div class="grid">{row}</div>' if row else "")
            + (f'<div class="three">{three}</div>' if three else "")
            + (f'<div class="gr">📏 {html.escape(m)}</div>' if m else "")
            + art)
    page = cards._page("bd", body)
    return page.replace("</style>", CSS + "</style>", 1)


def caption(child, n: int, late: bool) -> str:
    girl = child["sex"] == "girls"
    when = "вчора виповнилось" if late else "сьогодні"
    head = f"🎂 <b>{html.escape(child['name'], quote=False)} {when} {years_text(n)}!</b>"
    w = db.last_measure(child["id"], "weight_kg")
    if w and (db.now() - w["ts"]) < FRESH_DAYS * 86400:
        return head
    ask = ("Якщо вже були на огляді у педіатра, внесіть вагу і зріст - покажу, як "
           f"{'вона' if girl else 'він'} росте за таблицями ВООЗ і скільки додано за місяць.")
    if not w:
        ask = ("Внесіть вагу і зріст після огляду - бот покаже перцентиль ВООЗ, а з кожним "
               "місяцем буде видно криву росту.")
    return f"{head}\n\n{ask}"


def keyboard(child, n: int, chat_id: int, six: bool) -> ui.M:
    rows = [[ui.B("📏 Внести вагу і зріст", callback_data="f:bdm", style="primary")]]
    if six:
        rows.append([ui.B("📅 План першого тижня", callback_data="f:prep:week")])
    rows.append([ui.share_button(chat_id)])
    return ui.M(rows)


async def run(context) -> None:
    from telegram.error import TelegramError
    from telegram.constants import ParseMode
    from . import cards, reminders
    today = datetime.now(ui.TZ).date()
    sent = 0
    for child in db.q("SELECT * FROM child"):
        try:
            hit = due(child, today)
            if not hit:
                continue
            n, late = hit
            fid = child["family_id"]
            mark = f"{KEY}:{child['id']}:{n}"
            if db.get_kv(fid, mark) or db.get_kv(fid, "remind", "1") != "1":
                continue
            chats = [c for c in db.family_chats(fid)
                     if not db.q1("SELECT 1 FROM subscriber WHERE user_id=? AND blocked=1", (c,))]
            if not chats:
                continue
            db.set_kv(fid, mark, str(db.now()))           # мітка по відправці: один раз
            six = n == ui.PREP_UNTIL and not db.intro_map(child["id"])
            if six:
                db.set_kv(fid, f"{reminders.SIX_KEY}:{child['id']}", today.isoformat())
            png = await cards.render(card(child, n))
            for chat_id in chats:
                try:
                    await context.bot.send_photo(chat_id, png, caption=caption(child, n, late),
                                                 parse_mode=ParseMode.HTML,
                                                 reply_markup=keyboard(child, n, chat_id, six))
                    sent += 1
                except TelegramError as exc:
                    log.warning("картка місяця життя не дійшла %s: %s", chat_id, exc)
        except Exception:   # одна крива родина не має гасити решту
            log.exception("картка місяця життя для дитини %s", child["id"])
    if sent:
        log.info("вітання з місяцем: %s карток", sent)


ENABLED = False   # 08.11: чекає погодження зразків Max; True + рестарт = запуск


def install(app) -> None:
    if ENABLED and app.job_queue is not None:
        app.job_queue.run_daily(run, time=AT, name="feed_birthday")
