"""Екрани прикорму: текст + інлайн-клавіатури. Чистий рендер, без побічних ефектів."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton as B
from telegram import InlineKeyboardMarkup as M

from . import achievements, catalog, db, growth, suggest
from .rich import Rich

TZ = ZoneInfo("Europe/Kyiv")



def esc(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def plural(n: int, one: str, few: str, many: str) -> str:
    """3 прийоми, 5 прийомів, 21 прийом."""
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} {one}"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} {few}"
    return f"{n} {many}"


def bar(done: int, total: int, width: int = 8) -> str:
    """Смужка прогресу ▰▰▱▱ як у зведенні."""
    fill = round(width * done / total) if total else 0
    return "▰" * fill + "▱" * (width - fill)


def chips(*parts: str) -> str:
    """Рядок-підзаголовок під назвою екрана."""
    return "<p><i>" + " · ".join(x for x in parts if x) + "</i></p>"


def local(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, TZ)


def day_bounds(offset: int = 0) -> tuple[int, int, date]:
    today = datetime.now(TZ).date() + timedelta(days=offset)
    return bounds_of(today)


def bounds_of(day: date) -> tuple[int, int, date]:
    start = datetime.combine(day, datetime.min.time(), TZ)
    return int(start.timestamp()), int((start + timedelta(days=1)).timestamp()), day


def day_label(day: date | None) -> str:
    """Як назвати дату прийому їжі: сьогодні, вчора або число."""
    today = datetime.now(TZ).date()
    if day is None or day == today:
        return "сьогодні"
    if day == today - timedelta(days=1):
        return "вчора"
    return day.strftime("%d.%m.%Y")


def birth_date(child) -> date:
    return date.fromisoformat(child["birth_date"])


def months_of(child) -> int:
    return growth.age_months(birth_date(child))


PREP_UNTIL = 6      # місяців: до цього віку головна показує режим «Готуємось»


def feed_months(child) -> int:
    """Вік для підказок. Хто почав раніше 6 міс (4-5 за порадою лікаря), отримує
    підказки шестимісячного: молодших продуктів у каталозі немає."""
    return max(months_of(child), PREP_UNTIL)


def days_to_start(child, today: date | None = None) -> int:
    """Скільки діб до 6 місяців. 0 і менше - вже виповнилось."""
    return (growth.month_date(birth_date(child), PREP_UNTIL) - (today or datetime.now(TZ).date())).days


def spoon(child) -> bool:
    """Родина годує з ложки (пюре, каші) - тоді подачу показуємо під ложку, не шматочками."""
    return db.get_kv(child["family_id"], "style") == "spoon"


def _rows(buttons: list[B], per_row: int = 2) -> list[list[B]]:
    return [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]


def sub_tabs(key: str, prefix: str) -> list[list[B]]:
    """Ряд вкладок усередині групи. Для білка це мʼясо / риба / яйця / бобові."""
    group, sub = catalog.split_key(key)
    subs = catalog.SUBS_OF.get(group)
    if not subs:
        return []
    return _rows([B(("• " if s == sub else "") + f"{catalog.SUBGROUPS[s][0]} {catalog.SUBGROUPS[s][1].split()[0]}",
                    callback_data=f"{prefix}:{group}.{s}") for s in subs], 4)


def shelf_items(key: str, show_rare: bool) -> list[dict]:
    """Продукти полиці в порядку показу. Екзотика ховається, поки її не попросили."""
    return sorted(catalog.by_key(key, rare=show_rare),
                  key=lambda p: (p["from_month"], p["name"]))


def rare_toggle(key: str, where: str, show_rare: bool) -> list[B]:
    """Кнопка «показати/сховати рідкісні». where - куди повернутись після перемикання."""
    hidden = catalog.rare_count(key)
    if not hidden:
        return []
    if show_rare:
        return [B("🙈 Сховати рідкісні", callback_data=f"f:rare:{where}")]
    return [B(f"🥬 Показати рідкісні · {hidden}", callback_data=f"f:rare:{where}")]


def mark(code: str, intro: dict, months: int) -> str:
    p = catalog.product(code)
    row = intro.get(code)
    if row and row["status"] == "avoid":
        return "⛔"
    if row and row["status"] == "watch":
        return "👀"
    if row:
        return "✅"
    if p and p["from_month"] > months:
        return "⏳"
    return "🆕"


# ─────────────────────────── головна ───────────────────────────


def home(child, intro: dict) -> tuple[str, M]:
    months = months_of(child)
    ts_from, ts_to, _ = day_bounds()
    meals = db.meals_between(child["id"], ts_from, ts_to)
    eaten_today = {c for m in meals for c in db.meal_codes(m["id"])}
    watch = [c for c, r in intro.items() if r["status"] == "watch"]
    allerg_all = [c for c, p in catalog.products().items() if p.get("allergen")]
    allerg_in = sum(1 for c in allerg_all if c in intro)

    # до 6 міс і ще нічого не вводили: своя головна. Хто почав раніше (4-5 за порадою
    # лікаря), отримує звичайну - «готуємось» йому лише заважає
    prep_left = days_to_start(child) if months < PREP_UNTIL and not intro else 0
    if prep_left > 0:
        return _home_prep(child, prep_left)

    today = (f"{plural(len(meals), 'прийом', 'прийоми', 'прийомів')}, "
             f"{plural(len(eaten_today), 'продукт', 'продукти', 'продуктів')}") if meals else "ще нічого"
    parts = [f"<h3>👶 {esc(child['name'])} · {growth.age_text(birth_date(child))}</h3>",
             chips(f"етап {catalog.band_title(catalog.band_for(months))}"),
             "<table>"
             f"<tr><td>Сьогодні</td><td colspan=\"2\">{today}</td></tr>"
             f"<tr><td>Введено</td><td>{bar(len(intro), len(catalog.products()))}</td>"
             f"<td>{len(intro)}/{len(catalog.products())}</td></tr>"
             f"<tr><td>Алергени</td><td>{bar(allerg_in, len(allerg_all))}</td>"
             f"<td>{allerg_in}/{len(allerg_all)}</td></tr>"
             "</table>"]
    got = achievements.top(child["id"])
    if got:
        more = f" +{len(got) - 5}" if len(got) > 5 else ""
        parts.append("<p>🏅 " + "  ".join(achievements.badge(k, lvl) for k, lvl in got[:5]) + more + "</p>")
    if watch:
        parts.append("<blockquote>👀 <b>Під наглядом:</b> "
                     + ", ".join(catalog.label(c) for c in watch[:4]) + "</blockquote>")

    start_now = not intro and months < PREP_UNTIL + 2   # 6 міс є, а щоденник порожній
    if start_now:
        parts.append("<blockquote><b>🌱 Можна починати прикорм</b><br>"
                     "Готовий план першого тижня: що давати, як подати і коли перевіряти "
                     "реакцію.</blockquote>")

    news = news_due(child["family_id"])
    if news:
        parts.append(NEWS_BLOCK)

    stock = len(db.pantry_codes(child["family_id"]))
    fridge = f" · {stock}" if stock else ""
    kb = M(([[B("📅 План першого тижня", callback_data="f:prep:week", style="primary")]] if start_now else [])
           + (NEWS_ROWS if news else []) + [
        [B("🍽 Записати прийом їжі", callback_data="f:new", style="success")],
        [B(f"🧊 Холодильник{fridge}", callback_data="f:fr"),
         B("🎯 Що ввести далі", callback_data="f:next", style="primary")],
        [B("🥕 Продукти", callback_data="f:lib"), B("👨‍🍳 Рецепти", callback_data="f:rec")],
        [B("📔 Щоденник", callback_data="f:diary"), B("📈 Зростання", callback_data="f:grow")],
        [B("🏅 Досягнення", callback_data="f:ach"), B("⚙️ Налаштування", callback_data="f:set")],
    ])
    return Rich("".join(parts)), kb


NEWS_BLOCK = ("<blockquote><b>🆕 Два оновлення</b><br>"
              "🥄 Поради подачі тепер бувають під ложку (пюре, каші) або шматочками "
              "(BLW). Як годуєте ви?<br>"
              "☀️ Нагадування про вітамін D: у вибраний час, щодня або через день."
              "</blockquote>")
NEWS_ROWS = [[B("🥄 З ложки", callback_data="f:news:spoon"),
              B("✋ Шматочками", callback_data="f:news:blw")],
             [B("☀️ Вітамін D", callback_data="f:news:vd"),
              B("Пізніше", callback_data="f:news:later")]]


def _home_prep(child, left: int) -> tuple[str, M]:
    """Головна до старту прикорму: відлік і підготовка замість порожнього щоденника."""
    start = growth.month_date(birth_date(child), PREP_UNTIL)
    parts = [f"<h3>👶 {esc(child['name'])} · {growth.age_text(birth_date(child))}</h3>",
             chips("етап: готуємось до прикорму"),
             "<table>"
             f"<tr><td>До старту</td><td>{bar(PREP_UNTIL * 30 - min(left, PREP_UNTIL * 30), PREP_UNTIL * 30)}</td>"
             f"<td>{plural(left, 'день', 'дні', 'днів')}</td></tr>"
             f"<tr><td>{PREP_UNTIL} місяців</td><td colspan=\"2\">{start.strftime('%d.%m.%Y')}</td></tr>"
             "</table>",
             "<p>Поки основа харчування молоко чи суміш. За цей час можна підготуватись: "
             "ознаки готовності, стільчик і посуд, план першого тижня."
             + (f" У день, коли виповниться {PREP_UNTIL} місяців, я нагадаю."
                if db.get_kv(child["family_id"], "remind", "1") == "1" else "") + "</p>"]
    news = news_due(child["family_id"])
    if news:
        parts.append(NEWS_BLOCK)
    kb = M((NEWS_ROWS if news else []) + [
        [B("🌱 Як підготуватись", callback_data="f:prep", style="primary")],
        [B("📅 План першого тижня", callback_data="f:prep:week")],
        [B("🥕 Продукти", callback_data="f:lib"), B("👨‍🍳 Рецепти", callback_data="f:rec")],
        [B("📈 Зростання", callback_data="f:grow"), B("🍽 Вже почали", callback_data="f:new")],
        [B("🏅 Досягнення", callback_data="f:ach"), B("⚙️ Налаштування", callback_data="f:set")],
    ])
    return Rich("".join(parts)), kb


def _days_left(n: int) -> str:
    if n == 1:
        return "лишився 1 день, починаємо завтра"
    word = "лишився" if n % 10 == 1 and n % 100 != 11 else "лишилось"
    return f"{word} {plural(n, 'день', 'дні', 'днів')}"


# ─────────────────────── режим «Готуємось» ───────────────────────

PREP_SOURCE = "<p><i>Довідка на основі рекомендацій ВООЗ, AAP, EFSA і Solid Starts.</i></p>"
PREP_FIRST_DAYS = 3   # правило трьох днів: один новий продукт тримаємо 2-3 дні


def prep(child) -> tuple[str, M]:
    """Розділ «Готуємось»: ознаки готовності і що підготувати. Доступний у будь-якому віці."""
    left = days_to_start(child)
    when = f"до {PREP_UNTIL} місяців {_days_left(left)}" if left > 0 else f"{PREP_UNTIL} місяців уже є"
    parts = ["<h3>🌱 Готуємось до прикорму</h3>",
             chips(f"{esc(child['name'])}, {growth.age_text(birth_date(child))}", when),
             "<p>Прикорм починають близько 6 місяців, не раніше 4. Дивимось на вік "
             "і на ці ознаки разом.</p>",
             "<h4>✅ Ознаки готовності</h4><ul>"
             "<li>сидить з підтримкою і тримає голову</li>"
             "<li>зник рефлекс виштовхування язиком: ложка в роті вже не виштовхується сама</li>"
             "<li>цікавиться їжею: тягнеться до тарілки, відкриває рот на ложку</li></ul>",
             "<h4>🪑 Що підготувати</h4><ul>"
             "<li>стільчик для годування зі стійкою опорою для спини і ніг</li>"
             "<li>м'яка ложка з вузьким краєм</li>"
             "<li>миска на присосці і слинявчик</li>"
             "<li>чашка або поїльник для знайомства з водою</li></ul>",
             "<p>Грудне молоко чи суміш лишається основою харчування, прикорм додається поступово.</p>",
             PREP_SOURCE]
    rows = [[B("📅 План першого тижня", callback_data="f:prep:week", style="primary")],
            [B("🥕 Продукти", callback_data="f:lib"), B("👨‍🍳 Рецепти", callback_data="f:rec")],
            [B("🏠 Головна", callback_data="f:home")]]
    return Rich("".join(parts)), M(rows)


def prep_week(child, intro: dict, stock: list[str] | None = None) -> tuple[str, M]:
    """План першого тижня з тих самих правил, що й «Що ввести далі» (suggest.next_products)."""
    months = max(months_of(child), PREP_UNTIL)
    items = suggest.next_products(months, intro, stock, limit=2)
    by_spoon = spoon(child)
    parts = ["<h3>📅 План першого тижня</h3>",
             chips(f"{esc(child['name'])}, {growth.age_text(birth_date(child))}",
                   "з ложки" if by_spoon else "шматочками"),
             "<p>Один новий продукт за раз, 2-3 дні поспіль. Новий даємо вранці, щоб до вечора "
             "бачити реакцію. Порція мала, кілька ложечок, потім звичайне молоко чи суміш.</p>"]
    day = 1
    for code, _ in items:
        prod = catalog.product(code)
        last = day + PREP_FIRST_DAYS - 1
        parts.append(f"<h4>Дні {day}-{last}: {prod['emoji']} {esc(prod['name'])}{catalog.mark_allergen(code)}</h4>"
                     f"<p>{esc(catalog.serving(code, months, by_spoon))}</p>")
        day = last + 1
    if items:
        parts.append(f"<h4>День {day}</h4><p>Без нового: даємо знайоме і дивимось, як дитина почувається. "
                     "Далі продовжуємо за підказками «Що ввести далі».</p>")
    else:
        parts.append("<p>За віком усе, що бот знає, уже введено. Тримаємо різноманіття.</p>")
    parts.append(PREP_SOURCE)
    # до 6 міс план на майбутнє: «Подали» перевело б у звичайний режим раніше часу
    early = months_of(child) < PREP_UNTIL
    rows = [([] if early else [B(f"🍽 Подали: {catalog.name(c)}", callback_data=f"f:quick:{c}", style="success")])
            + [B(f"🥕 {catalog.name(c)}" if early else "🥕 Картка", callback_data=f"f:p:{c}")]
            for c, _ in items]
    rows.append([B("🌱 Готуємось", callback_data="f:prep"), B("🏠 Головна", callback_data="f:home")])
    return Rich("".join(parts)), M(rows)


# ─────────────────────── конструктор тарілки ───────────────────────


NEWS_KEY = "news_spoon_vitd"   # картка «два оновлення», 21.10
NEWS_MAX_DAYS = 3


def news_due(family_id: int) -> bool:
    """Картка новинок на головній: поки не натиснули жодної кнопки, але не довше
    трьох днів, коли людина заходила. Рахуємо дні, не показ: головна малюється часто."""
    state = db.get_kv(family_id, NEWS_KEY)
    if state == "done" or db.get_kv(family_id, "style"):
        return False
    today = datetime.now(TZ).date().isoformat()
    days = state.split(",") if state else []
    if today not in days:
        if len(days) >= NEWS_MAX_DAYS:
            return False
        db.set_kv(family_id, NEWS_KEY, ",".join(days + [today]))
    return True


def style_ask(child) -> tuple[str, M]:
    return (f"✅ Профіль готовий: {esc(child['name'])}, {growth.age_text(birth_date(child))}.\n\n"
            "Як годуєте? Від цього залежить, як бот підказує подачу.",
            M([[B("🥄 З ложки: пюре, каші", callback_data="f:ostyle:spoon")],
               [B("✋ Шматочками (BLW)", callback_data="f:ostyle:blw")],
               [B("🤷 Ще не знаю", callback_data="f:ostyle:blw")]]))


def balance_hint(codes: list[str]) -> str:
    if not codes:
        return "Обери, що було в тарілці. Можна один продукт, можна пʼять."
    missing = suggest.missing_slots(codes)
    if not missing:
        return "Тарілка збалансована: є рослинне, вуглевод, білок і жир."
    return "Не вистачає: " + ", ".join(suggest.SLOT_LABEL[s] for s in missing)


def draft(child, codes: list[str], intro: dict, group: str | None = None,
          show_rare: bool = False) -> tuple[str, M]:
    months = months_of(child)
    head = ["🍽 <b>Нова тарілка</b>", ""]
    if codes:
        head.append("У тарілці: " + ", ".join(catalog.label(c) for c in codes))
    head.append(balance_hint(codes))

    rows: list[list[B]] = []
    if group == "fridge":
        stock = db.pantry_codes(child["family_id"])
        head.append("")
        if stock:
            head.append("🧊 <b>З холодильника</b>")
            ordered = [c for key in catalog.nav_keys() for c in stock
                       if catalog.key_of(c) == key]
            rows += _rows([B(("✔️ " if c in codes else "") + catalog.label(c),
                             callback_data=f"f:sel:{c}") for c in ordered])
        else:
            head.append("🧊 Холодильник порожній. Склади туди те, що є вдома - "
                        "і цей екран показуватиме тільки його.")
            rows.append([B("🧊 Наповнити холодильник", callback_data="f:fr")])
    elif group == "fav":
        fav = db.recent_codes(child["id"], 8)
        head.append("")
        head.append("⭐ <b>Часте</b>" if fav else "⭐ Часте зʼявиться після перших записів.")
        rows += _rows([B(("✔️ " if c in codes else "") + catalog.label(c),
                         callback_data=f"f:sel:{c}") for c in fav])
    elif group:
        group = catalog.default_key(group)
        emoji, title = catalog.key_title(group)
        head.append("")
        head.append(f"{emoji} <b>{title}</b>")
        rows += sub_tabs(group, "f:grp")
        items = shelf_items(group, show_rare)
        rows += _rows([
            B(("✔️ " if p["code"] in codes else mark(p["code"], intro, months) + " ")
              + catalog.name(p["code"]),
              callback_data=f"f:sel:{p['code']}")
            for p in items
        ])
        toggle = rare_toggle(group, f"grp:{group}", show_rare)
        if toggle:
            rows.append(toggle)

    nav = [B("🧊 З холодильника", callback_data="f:grp:fridge"),
           B("⭐ Часте", callback_data="f:grp:fav")]
    nav += [B(f"{catalog.GROUPS[g][0]} {catalog.GROUPS[g][1]}",
              callback_data=f"f:grp:{g}.{catalog.SUBS_OF[g][0]}" if g in catalog.SUBS_OF
              else f"f:grp:{g}")
            for g in catalog.GROUP_ORDER]
    rows += _rows(nav)

    tail = []
    if codes:
        tail.append(B("✅ Подали", callback_data="f:save"))
    tail.append(B("✖️ Скасувати", callback_data="f:home"))
    rows.append(tail)
    return "\n".join(head), M(rows)


def pick_kind(codes: list[str], day: date | None = None) -> tuple[str, M]:
    now_h = datetime.now(TZ).hour
    guess = "breakfast" if now_h < 11 else "lunch" if now_h < 15 else "dinner" if now_h < 21 else "snack"
    today = datetime.now(TZ).date()
    text = ("Який це прийом їжі?\n\nУ тарілці: "
            + ", ".join(catalog.label(c) for c in codes)
            + f"\nКоли: <b>{day_label(day)}</b>")
    rows = _rows([B(("• " if k == guess else "") + name, callback_data=f"f:save:{k}")
                  for k, name in catalog.MEAL_KINDS])
    picked = day or today
    rows.append([B(("• " if picked == today else "") + "Сьогодні", callback_data="f:day:0"),
                 B(("• " if picked == today - timedelta(days=1) else "") + "Вчора",
                   callback_data="f:day:1"),
                 B("📅 Інша дата", callback_data="f:day:ask")])
    rows.append([B("‹ Назад", callback_data="f:new_keep")])
    return text, M(rows)


MEAL_DATE_PROMPT = ("📅 Якого числа це було? Формат: <code>03.09.2026</code>\n\n"
                    "Можна записати заднім числом за будь-який день цього року.")


def meal_record(child, codes: list[str], kind: str, ts: int | None = None) -> str:
    """Постійний запис у стрічці чату. Без кнопок - його ніщо не перезапише."""
    ts = ts or db.now()
    day = local(ts).date()
    day_from, day_to, _ = bounds_of(day)
    same_day = db.meals_between(child["id"], day_from, day_to)
    line = " · ".join(catalog.label(c) for c in codes)
    when = local(ts).strftime("%H:%M")
    if day != datetime.now(TZ).date():
        when = local(ts).strftime("%d.%m, %H:%M")
    head = f"✅ <b>{catalog.MEAL_LABEL[kind]}</b> · {when}"
    word = "Сьогодні" if day == datetime.now(TZ).date() else "Того дня"
    tail = f"\n\n<i>{word} це {len(same_day)}-й прийом їжі.</i>" if len(same_day) > 1 else ""
    return f"{head}\n{line}{tail}"


def after_save(child, intro: dict, fresh: list[str]) -> tuple[str, M]:
    """Окреме повідомлення після запису: попередження і навігація."""
    if not fresh:
        return home(child, intro)
    parts = ["<h4>🆕 Нові продукти: " + ", ".join(catalog.label(c) for c in fresh) + "</h4>",
             "<p>Правило трьох днів: тримаємо новий продукт у меню 2-3 дні поспіль "
             "і не вводимо інший новий, щоб реакцію було видно однозначно.</p>",
             "<p>Нагадаю ввечері спитати, як пройшло.</p>"]
    for c in fresh:
        prod = catalog.product(c)
        if prod and prod.get("allergen"):
            parts.append(f"<blockquote>⚠️ {prod['emoji']} <b>{esc(prod['name'])}</b> - алерген "
                         f"({esc(prod['allergen'])}). Наступні рази давати регулярно, не відкладати.</blockquote>")
    rows = _rows([B(f"⚠️ Реакція: {catalog.label(c)}", callback_data=f"f:react:{c}", style="danger")
                  for c in fresh], 1)
    rows.append([B("📔 Щоденник", callback_data="f:diary"), B("🏠 Головна", callback_data="f:home")])
    return Rich("".join(parts)), M(rows)


# ─────────────────────────── бібліотека ───────────────────────────


def library(child, intro: dict, show_rare: bool = False) -> tuple[str, M]:
    months = months_of(child)
    shown = sum(len(catalog.by_key(k, rare=show_rare)) for k in catalog.nav_keys())
    lines = ["🥕 <b>Продукти</b>",
             f"Введено {len(intro)} з {shown}.",
             "",
             "✅ введено · 🆕 ще ні · 👀 під наглядом · ⛔ уникаємо · ⏳ зарано за віком",
             f"{catalog.ALLERGEN_MARK} алерген: вводити рано і потім регулярно",
             "",
             "<i>Мʼясо, риба, яйця і бобові лежать на різних полицях, але для балансу "
             "тарілки це один білок.</i>"]
    rows = []
    hidden = 0
    for key in catalog.nav_keys():
        emoji, title = catalog.key_title(key)
        items = catalog.by_key(key, rare=show_rare)
        hidden += catalog.rare_count(key)
        done = sum(1 for p in items if p["code"] in intro)
        rows.append([B(f"{emoji} {title} · {done}/{len(items)}", callback_data=f"f:lg:{key}")])
    if hidden:
        rows.append([B("🙈 Сховати рідкісні" if show_rare else f"🥬 Показати рідкісні · {hidden}",
                       callback_data="f:rare:lib")])
    rows.append([B("🏠 Головна", callback_data="f:home")])
    return "\n".join(lines), M(rows)


def group_list(child, group: str, intro: dict, show_rare: bool = False) -> tuple[str, M]:
    months = months_of(child)
    group = catalog.default_key(group)
    emoji, title = catalog.key_title(group)
    items = shelf_items(group, show_rare)
    lines = [f"{emoji} <b>{title}</b>",
             f"Вік {child['name']}: {growth.age_text(birth_date(child))} · позицій {len(items)}"]
    rows = sub_tabs(group, "f:lg")
    rows += _rows([B(f"{mark(p['code'], intro, months)} {catalog.name(p['code'])}",
                     callback_data=f"f:p:{p['code']}") for p in items])
    toggle = rare_toggle(group, f"lg:{group}", show_rare)
    if toggle:
        rows.append(toggle)
    rows.append([B("‹ Продукти", callback_data="f:lib"), B("🏠 Головна", callback_data="f:home")])
    return "\n".join(lines), M(rows)


def card(child, code: str, intro: dict) -> tuple[str, M]:
    p = catalog.product(code)
    months = months_of(child)
    band = catalog.band_for(months)
    row = intro.get(code)

    risk = {"low": "🟢", "med": "🟡", "high": "🔴"}[p["choking"]]
    meta = [catalog.age_label(p["from_month"]), f"{risk} ризик подавитися: {catalog.CHOKING[p['choking']]}"]
    if p.get("allergen"):
        meta.append(f"<mark>⚠️ алерген: {esc(p['allergen'])}</mark>")
    parts = [f"<h3>{p['emoji']} {esc(p['name'])}{catalog.mark_allergen(code)}</h3>", chips(*meta)]
    if p["from_month"] > months:
        parts.append(f"<blockquote>⏳ Для {esc(child['name'])} ще зарано - рекомендовано "
                     f"{catalog.age_label(p['from_month'])}.</blockquote>")

    band_title = next(t for k, lo, hi, t in catalog.AGE_BANDS if k == band)
    by_spoon = spoon(child) and band in catalog.SPOON_BANDS and catalog.spoon_texts().get(code)
    how = "з ложки" if by_spoon else "зараз"
    parts.append(f"<h4>Як подавати {how} · {esc(band_title)}</h4>"
                 f"<p>{esc(catalog.serving(code, months, spoon(child)))}</p>")
    if p.get("cook"):
        steps = [x.strip() for x in re.split(r"(?<=\.)\s+(?=[А-ЯІЇЄҐ⚠])", p["cook"]) if x.strip()]
        parts.append("<h4>⏱ Готування</h4>" + (
            "<ul>" + "".join(f"<li>{esc(x)}</li>" for x in steps) + "</ul>" if len(steps) > 1
            else f"<p>{esc(p['cook'])}</p>"))
    if p.get("benefit"):
        parts.append(f"<h4>Користь</h4><p>{esc(p['benefit'])}</p>")
    if p.get("caution"):
        parts.append(f"<blockquote><b>⚠️ Обережно.</b> {esc(p['caution'])}</blockquote>")
    others = [(t, p["serve"][k]) for k, lo, hi, t in catalog.AGE_BANDS if k != band]
    if by_spoon:
        others.insert(0, (f"Шматочками, {band_title}", p["serve"][band]))
    if others:
        parts.append("<h4>Інший вік</h4><blockquote expandable>"
                     + "<br>".join(f"<b>{esc(t)}:</b> {esc(v)}" for t, v in others)
                     + "</blockquote>")

    foot = []
    if row:
        emoji_s, label_s = catalog.STATUS[row["status"]]
        foot.append(f"{emoji_s} {label_s} · перший раз {local(row['first_ts']).strftime('%d.%m.%Y')} · "
                    + plural(row["times"], "раз", "рази", "разів"))
    rx = [r for r in db.reactions(child["id"], 50) if r["code"] == code]
    if rx:
        foot.append("Реакції: " + ", ".join(
            f"{catalog.REACTION_LABEL.get(r['kind'], r['kind'])} ({local(r['ts']).strftime('%d.%m')})"
            for r in rx[:3]))
    if foot:
        parts.append("<p><i>" + "<br>".join(foot) + "</i></p>")

    rows = [
        [B("🍽 Подали зараз", callback_data=f"f:quick:{code}", style="success")],
        [B("⚠️ Реакція", callback_data=f"f:react:{code}", style="danger")],
    ]
    st_row = []
    if not row or row["status"] != "ok":
        st_row.append(B("✅ Освоєно", callback_data=f"f:st:{code}:ok"))
    if not row or row["status"] != "avoid":
        st_row.append(B("⛔ Уникаємо", callback_data=f"f:st:{code}:avoid"))
    if row and row["status"] in ("avoid", "watch"):
        st_row.append(B("↩️ Зняти позначку", callback_data=f"f:st:{code}:ok"))
    if st_row:
        rows.append(st_row)
    back_key = catalog.key_of(code)
    rows.append([B(f"‹ {catalog.key_title(back_key)[1]}", callback_data=f"f:lg:{back_key}"),
                 B("🏠 Головна", callback_data="f:home")])
    return Rich("".join(parts)), M(rows)


# ─────────────────────────── реакції ───────────────────────────


def react_menu(code: str) -> tuple[str, M]:
    p = catalog.product(code)
    text = (f"⚠️ Реакція на {p['emoji']} <b>{esc(p['name'])}</b>\n\n"
            "Що помітили? Продукт автоматично піде в статус «під наглядом».")
    rows = _rows([B(name, callback_data=f"f:rx:{code}:{kind}") for kind, name in catalog.REACTIONS], 1)
    rows.append([B("Все добре, реакції не було", callback_data=f"f:st:{code}:ok")])
    rows.append([B("‹ Назад", callback_data=f"f:p:{code}")])
    return text, M(rows)


def react_saved(code: str, kind: str) -> tuple[str, M]:
    p = catalog.product(code)
    lines = [f"Записано: {catalog.REACTION_LABEL[kind]} на {p['emoji']} {esc(p['name'])}.",
             f"👀 {esc(p['name'])} тепер під наглядом."]
    if kind == "breath":
        lines = ["🚨 <b>Набряк губ, язика чи утруднене дихання - це невідкладний стан.</b>",
                 "Викликайте швидку (103). Не чекайте, поки минеться.",
                 "", f"Запис збережено: {p['emoji']} {esc(p['name'])}."]
    elif kind in ("rash", "vomit"):
        lines.append("Продукт наступні дні не давайте. Якщо реакція повториться - до педіатра "
                     "чи алерголога зі списком з щоденника.")
    else:
        lines.append("Разова реакція часто буває на нову текстуру, а не на алерген. "
                     "Спробуйте ще раз за кілька днів меншою порцією.")
    rows = [[B("📝 Додати нотатку", callback_data=f"f:rxnote:{code}")],
            [B("‹ До продукту", callback_data=f"f:p:{code}"), B("🏠 Головна", callback_data="f:home")]]
    return "\n".join(lines), M(rows)


def reactions_log(child) -> tuple[str, M]:
    rows_db = db.reactions(child["id"], 20)
    lines = ["⚠️ <b>Журнал реакцій</b>", ""]
    if not rows_db:
        lines.append("Поки що порожньо. І це добре.")
    for r in rows_db:
        note = f" - {esc(r['note'])}" if r["note"] else ""
        lines.append(f"{local(r['ts']).strftime('%d.%m %H:%M')} · {catalog.label(r['code'])} · "
                     f"{catalog.REACTION_LABEL.get(r['kind'], r['kind'])}{note}")
    return "\n".join(lines), M([[B("‹ Щоденник", callback_data="f:diary"),
                                 B("🏠 Головна", callback_data="f:home")]])


# ─────────────────────────── тарілки і рецепти ───────────────────────────


def plates_screen(child, band: str | None = None) -> tuple[str, M]:
    months = months_of(child)
    band = band or catalog.band_for(months)
    items = catalog.plates_for(band)
    lines = [f"🍲 <b>Ідеї тарілок · {catalog.band_title(band)}</b>", "",
             "Принцип: рослинне + вуглевод + білок + жир. Жир додаємо завжди - без нього "
             "не засвояться вітаміни A, D, E, K."]
    lines += ["", f"Готових ідей: {len(items)}. Якщо мало - «Ще варіанти» збирає нові "
              "з продуктів, дозволених за віком."]
    rows = [[B(p["title"], callback_data=f"f:pl:{p['id']}")] for p in items]
    rows.append([B("🎲 Ще варіанти", callback_data="f:gen"),
                 B("✍️ У мене вже є…", callback_data="f:have")])
    rows.append([B(("• " if b == band else "") + t, callback_data=f"f:plates:{b}")
                 for b, _, _, t in catalog.AGE_BANDS])
    rows.append([B("🧊 Холодильник", callback_data="f:fr"), B("🏠 Головна", callback_data="f:home")])
    return "\n".join(lines), M(rows)


def _plate_button(codes: list[str], prefix: str) -> B:
    return B(" · ".join(catalog.name(c) for c in codes),
             callback_data=f"{prefix}:{suggest.encode(codes)}")


def generated(child, plates: list[list[str]], back: str = "f:gen") -> tuple[str, M]:
    lines = ["🎲 <b>Свіжі варіанти</b>",
             f"{catalog.band_title(catalog.band_for(months_of(child)))} · зібрано за віком, "
             "переважно з уже введеного, плюс потроху нового.", ""]
    if not plates:
        lines.append("Не вдалось зібрати тарілку. Схоже, зарано за віком або надто багато "
                     "продуктів у статусі «уникаємо».")
    else:
        lines.append("Тисни варіант, щоб побачити, як подавати кожен продукт.")
    rows = [[_plate_button(pl, "f:gp")] for pl in plates]
    rows.append([B("🎲 Ще", callback_data=back), B("✍️ У мене вже є…", callback_data="f:have")])
    rows.append([B("‹ Тарілки", callback_data="f:plates"), B("🏠 Головна", callback_data="f:home")])
    return "\n".join(lines), M(rows)


def gen_plate_card(child, codes: list[str], intro: dict, back: str = "f:gen") -> tuple[str, M]:
    months = months_of(child)
    lines = ["🍲 <b>" + esc(" · ".join(catalog.name(c) for c in codes)) + "</b>", ""]
    for code in codes:
        prod = catalog.product(code)
        lines.append(f"{mark(code, intro, months)} {prod['emoji']} <b>{esc(prod['name'])}</b>")
        lines.append(f"   {esc(catalog.serving(code, months, spoon(child)))}")
    fresh = [c for c in codes if c not in intro]
    lines += ["", f"<i>{esc(balance_hint(codes))}</i>"]
    if len(fresh) == 1:
        lines.append(f"<i>Нове тут одне: {catalog.label(fresh[0])}. Так і треба - "
                     "один новий продукт за раз.</i>")
    elif len(fresh) > 1:
        lines.append("<i>⚠️ Тут більше одного нового продукту. Краще ввести їх різними днями, "
                     "інакше не зрозуміло, на що була реакція.</i>")
    rows = [[B("🍽 Подали цю тарілку", callback_data=f"f:gu:{suggest.encode(codes)}")],
            [B("🎲 Інші варіанти", callback_data=back),
             B("‹ Тарілки", callback_data="f:plates")],
            [B("🏠 Головна", callback_data="f:home")]]
    return "\n".join(lines), M(rows)


HAVE_PROMPT = ("✍️ Напиши, що вже є під рукою - одним повідомленням, через кому.\n\n"
               "Наприклад: <code>морква і курка</code> або <code>яблуко, йогурт</code>.\n"
               "Доберу те, чого не вистачає до збалансованої тарілки.")


def have(child, found: list[str], plates: list[list[str]], intro: dict) -> tuple[str, M]:
    lines = ["✍️ <b>У тебе вже є</b>", " · ".join(catalog.label(c) for c in found)]
    need = suggest.missing_slots(found)
    if need:
        lines += ["", "Не вистачає: " + ", ".join(suggest.SLOT_LABEL[s] for s in need),
                  "", "Готові варіанти:"]
    else:
        lines += ["", "Тарілка вже збалансована: є рослинне, вуглевод, білок і жир."]
    rows = [[_plate_button(pl, "f:gp")] for pl in plates]
    if not need:
        rows = [[B("🍽 Подали цю тарілку", callback_data=f"f:gu:{suggest.encode(found)}")]]
    rows.append([B("🧊 Скласти в холодильник", callback_data="f:frput")])
    rows.append([B("✍️ Інший набір", callback_data="f:have"),
                 B("🎲 Ще варіанти", callback_data="f:gen")])
    rows.append([B("‹ Тарілки", callback_data="f:plates"), B("🏠 Головна", callback_data="f:home")])
    return "\n".join(lines), M(rows)


def plate_card(child, pid: str, intro: dict) -> tuple[str, M]:
    p = catalog.plate(pid)
    months = months_of(child)
    lines = [f"🍲 <b>{esc(p['title'])}</b>", f"{catalog.band_title(p['age'])}", ""]
    for code in p["items"]:
        prod = catalog.product(code)
        lines.append(f"{mark(code, intro, months)} {prod['emoji']} <b>{esc(prod['name'])}</b>")
        lines.append(f"   {esc(catalog.serving(code, months, spoon(child)))}")
    if p.get("note"):
        lines += ["", f"<i>{esc(p['note'])}</i>"]
    rows = [[B("🍽 Подали цю тарілку", callback_data=f"f:plu:{pid}")],
            [B("‹ Тарілки", callback_data=f"f:plates:{p['age']}"), B("🏠 Головна", callback_data="f:home")]]
    return "\n".join(lines), M(rows)


def _recipe_button(r: dict, months: int) -> B:
    late = "⏳ " if r["age_from"] > months else ""
    return B(f"{late}{r['emoji']} {r['title']} · {r['time']}", callback_data=f"f:r:{r['id']}")


def recipes_screen(child, show_all: bool = False, stock: list[str] | None = None) -> tuple[str, M]:
    months = months_of(child)
    every = catalog.recipes()
    fit = catalog.recipes_for(months)
    later = [r for r in every if r["age_from"] > months]
    items = every if show_all else fit
    stock = stock or []

    lines = [f"👨‍🍳 <b>Рецепти</b>", "",
             f"{esc(child['name'])}, {growth.age_text(birth_date(child))}",
             f"Підходять зараз: <b>{len(fit)}</b> з {len(every)}."]
    if later:
        by_age = {}
        for r in later:
            by_age[r["age_from"]] = by_age.get(r["age_from"], 0) + 1
        parts = [f"{n} з {age} міс" for age, n in sorted(by_age.items())]
        lines.append("Решта відкриється з віком: " + ", ".join(parts) + ".")
    lines += ["",
              "У кожному рецепті: інгредієнти з вагою, кроки, порада і як заморожувати. "
              "Без солі і цукру до року."]

    ready = [r for r in fit if not catalog.recipe_missing(r["id"], stock)[1]]
    if stock:
        lines.append("")
        if ready:
            lines.append(f"🧊 З холодильника можна приготувати прямо зараз: <b>{len(ready)}</b>.")
        else:
            lines.append("🧊 Повного набору на жоден рецепт удома поки немає.")

    if show_all:
        lines.append("")
        lines.append("<i>Показані всі, ⏳ означає зарано за віком.</i>")

    rows = [[B("🔍 Знайти за продуктом", callback_data="f:rfind")]]
    if stock:
        rows.append([B(f"🧊 Що зварити з наявного{f' · {len(ready)}' if ready else ''}",
                       callback_data="f:rfridge")])
    rows += [[_recipe_button(r, months)] for r in items]
    if later:
        rows.append([B("• За віком" if not show_all else "За віком", callback_data="f:rec"),
                     B("Усі рецепти" if not show_all else "• Усі рецепти",
                       callback_data="f:rec:all")])
    rows.append([B("🏠 Головна", callback_data="f:home")])
    return "\n".join(lines), M(rows)


RECIPE_FIND_PROMPT = ("🔍 Напиши продукт або назву страви - покажу все, що знайду.\n\n"
                      "Наприклад: <code>курка</code>, <code>гарбузовий суп</code>, "
                      "<code>сирники</code> або кілька продуктів через кому.\n\n"
                      "<i>Так само працює будь-яке повідомлення в чаті: кнопку "
                      "натискати не обовʼязково.</i>")


def search_result(child, query: str, codes: list[str], recipes: list[dict],
                  intro: dict) -> tuple[str, M]:
    """Відповідь на довільний текст: рецепти за назвою, продукти, добір тарілки."""
    months = months_of(child)
    lines = [f"🔍 <b>{esc(query.strip()[:80])}</b>", ""]
    rows: list[list[B]] = []

    if recipes:
        fit = [r for r in recipes if r["age_from"] <= months]
        tail = "" if len(fit) == len(recipes) else f", підходять за віком {len(fit)}"
        lines.append(f"👨‍🍳 Рецептів: <b>{len(recipes)}</b>{tail}")
        rows += [[_recipe_button(r, months)] for r in recipes]

    if codes:
        lines += ["", "🥕 Продукти в запиті: " + ", ".join(catalog.label(c) for c in codes)]
        need = suggest.missing_slots(codes)
        if need:
            lines.append("До повної тарілки бракує: "
                         + ", ".join(suggest.SLOT_LABEL[s] for s in need)
                         + ". Доберу, якщо треба.")
        else:
            lines.append("Це вже збалансована тарілка: є рослинне, вуглевод, білок і жир.")
        rows += _rows([B(f"{catalog.label(c)}", callback_data=f"f:p:{c}") for c in codes])
        rows.append([B("🍲 Зібрати тарілку", callback_data="f:hv"),
                     B("🧊 В холодильник", callback_data="f:frput")])

    if not recipes and not codes:
        lines.append("Нічого не знайшов. Спробуй простіше: <code>курка</code>, "
                     "<code>гарбуз</code>, <code>сирники</code>.")

    rows.append([B("👨‍🍳 Всі рецепти", callback_data="f:rec"),
                 B("🏠 Головна", callback_data="f:home")])
    return "\n".join(lines), M(rows)


def recipes_found(child, codes: list[str], found: list[dict],
                  near: list[dict] | None = None) -> tuple[str, M]:
    months = months_of(child)
    lines = ["🔍 <b>Рецепти з: </b>" + ", ".join(catalog.label(c) for c in codes), ""]
    rows: list[list[B]] = []
    if found:
        fit = [r for r in found if r["age_from"] <= months]
        lines.append(f"Знайшов {len(found)}, підходять за віком {len(fit)}.")
        rows += [[_recipe_button(r, months)] for r in found]
    else:
        lines.append("Рецептів саме з цим поки немає.")
        if near:
            lines += ["", "Найближче з тієї ж полиці:"]
            rows += [[_recipe_button(r, months)] for r in near]
        else:
            lines += ["", "Спробуй інший продукт або подивись «🎲 Ще варіанти» "
                      "в розділі тарілок: там страва збирається без рецепта."]
    rows.append([B("🔍 Інший продукт", callback_data="f:rfind"),
                 B("‹ Рецепти", callback_data="f:rec")])
    rows.append([B("🏠 Головна", callback_data="f:home")])
    return "\n".join(lines), M(rows)


def recipes_from_fridge(child, stock: list[str]) -> tuple[str, M]:
    months = months_of(child)
    fit = catalog.recipes_for(months)
    ready, almost = [], []
    for r in fit:
        _, missing = catalog.recipe_missing(r["id"], stock)
        if not missing:
            ready.append(r)
        elif len(missing) == 1:
            almost.append((r, missing[0]))

    lines = ["🧊 <b>Що зварити з наявного</b>", ""]
    rows: list[list[B]] = []
    if ready:
        lines.append(f"<b>Усе є ({len(ready)}):</b>")
        lines += [f"· {r['emoji']} {esc(r['title'])} · {esc(r['time'])}" for r in ready]
        rows += [[_recipe_button(r, months)] for r in ready]
    else:
        lines.append("Повного набору на жоден рецепт удома немає. "
                     "Ось що майже готове:")
    if almost:
        lines += ["", f"<b>Бракує однієї позиції ({len(almost)}):</b>"]
        for r, miss in almost[:8]:
            lines.append(f"· {r['emoji']} {esc(r['title'])} - купити {catalog.name(miss)}")
        rows += [[_recipe_button(r, months)] for r, _ in almost[:8]]
    rows.append([B("🛒 Список покупок", callback_data="f:frbuy"),
                 B("‹ Рецепти", callback_data="f:rec")])
    rows.append([B("🏠 Головна", callback_data="f:home")])
    return "\n".join(lines), M(rows)


def recipe_card(child, rid: str, stock: list[str] | None = None) -> tuple[str, M]:
    r = catalog.recipe(rid)
    months = months_of(child)
    parts = [f"<h3>{r['emoji']} {esc(r['title'])}</h3>", chips(f"з {r['age_from']} міс", f"⏱ {esc(r['time'])}")]
    if r["age_from"] > months:
        parts.append(f"<blockquote>⏳ Для {esc(child['name'])} ще зарано: страва розрахована на "
                     f"{r['age_from']} місяців.</blockquote>")
    if stock:
        have, missing = catalog.recipe_missing(rid, stock)
        if have and not missing:
            parts.append("<p>🧊 Усе є вдома.</p>")
        elif have:
            parts.append("<p>🧊 Є вдома: " + ", ".join(catalog.name(c) for c in have)
                         + ". Бракує: " + ", ".join(catalog.name(c) for c in missing) + ".</p>")
    parts.append("<h4>Інгредієнти</h4><ul>" + "".join(f"<li>{esc(i)}</li>" for i in r["ingredients"]) + "</ul>")
    parts.append("<h4>Як готувати</h4><ol>" + "".join(f"<li>{esc(x)}</li>" for x in r["steps"]) + "</ol>")
    parts.append(f"<blockquote><b>💡 Порада.</b> {esc(r['tips'])}</blockquote>")
    parts.append(f"<h4>❄️ Заморозка</h4><blockquote expandable>{esc(r['freeze'])}</blockquote>")
    rows = [[B("🍽 Подали цю страву", callback_data=f"f:ru:{rid}", style="success")],
            [B("‹ Рецепти", callback_data="f:rec"), B("🏠 Головна", callback_data="f:home")]]
    return Rich("".join(parts)), M(rows)


# ─────────────────────────── щоденник ───────────────────────────


def diary(child, offset: int = 0) -> tuple[str, M]:
    ts_from, ts_to, day = day_bounds(offset)
    meals = db.meals_between(child["id"], ts_from, ts_to)
    label = "сьогодні" if offset == 0 else "вчора" if offset == -1 else day.strftime("%d.%m.%Y")
    parts = [f"<h3>📔 Щоденник · {label}</h3>"]
    rows: list[list[B]] = []
    if not meals:
        parts.append("<p>Записів немає.</p>")
    else:
        body = ""
        codes_all: set[str] = set()
        for m in meals:
            codes = db.meal_codes(m["id"])
            codes_all.update(codes)
            what = ", ".join(catalog.label(c) for c in codes)
            if m["note"]:
                what += f"<br><i>{esc(m['note'])}</i>"
            body += (f"<tr><td>{local(m['ts']).strftime('%H:%M')}</td>"
                     f"<td>{catalog.MEAL_LABEL.get(m['kind'], m['kind'])}</td><td>{what}</td></tr>")
            rows.append([B(f"🗑 {local(m['ts']).strftime('%H:%M')}", callback_data=f"f:mdel:{m['id']}"),
                         B("📝 нотатка", callback_data=f"f:mnote:{m['id']}")])
        parts.append("<table><tr><th>Час</th><th>Прийом</th><th>Що їли</th></tr>" + body + "</table>")
        parts.append("<p><i>" + plural(len(meals), "прийом", "прийоми", "прийомів") + " · "
                     + plural(len(codes_all), "продукт", "продукти", "продуктів") + "</i></p>")
    nav = [B("‹ Раніше", callback_data=f"f:diary:{offset - 1}")]
    if offset < 0:
        nav.append(B("Пізніше ›", callback_data=f"f:diary:{offset + 1}"))
    rows.append(nav)
    rows.append([B("⚠️ Реакції", callback_data="f:rxlog"), B("📊 Зведення", callback_data="f:stats")])
    rows.append([B("🏆 Моя активність", callback_data="f:act")])
    rows.append([B("🏠 Головна", callback_data="f:home")])
    return Rich("".join(parts)), M(rows)


def stats(child, intro: dict) -> tuple[str, M]:
    months = months_of(child)
    total = len(catalog.products())
    body = ""
    for g in catalog.GROUP_ORDER:
        items = catalog.by_group(g)
        done = sum(1 for p in items if p["code"] in intro)
        body += (f"<tr><td>{catalog.GROUPS[g][0]} {catalog.GROUPS[g][1]}</td>"
                 f"<td>{bar(done, len(items))}</td><td>{done}/{len(items)}</td></tr>")

    ts_from, _, _ = day_bounds(-6)
    _, ts_to, _ = day_bounds()
    week = db.meals_between(child["id"], ts_from, ts_to)
    week_codes = {c for m in week for c in db.meal_codes(m["id"])}
    allerg_all = [c for c, p in catalog.products().items() if p.get("allergen")]
    allerg_intro = [c for c in allerg_all if c in intro]

    parts = ["<h3>📊 Зведення</h3>",
             chips(f"введено {len(intro)} з {total}",
                   "за 7 днів " + plural(len(week), "прийом", "прийоми", "прийомів") + ", "
                   + plural(len(week_codes), "продукт", "продукти", "продуктів")),
             "<table>" + body + "</table>",
             f"<h4>❗ Алергени · {len(allerg_intro)} з {len(allerg_all)}</h4>"]
    missing = [c for c in allerg_all if c not in intro and catalog.product(c)["from_month"] <= months]
    if missing:
        by_key: dict[str, list[str]] = {}
        for c in missing:
            by_key.setdefault(catalog.key_title(catalog.key_of(c))[1], []).append(catalog.label(c))
        parts.append(f"<p>Ще не пробували · {len(missing)}</p><blockquote expandable>"
                     + "<br>".join(f"<b>{esc(k)}:</b> {', '.join(v)}" for k, v in by_key.items())
                     + "</blockquote>")
        parts.append("<p><i>Алергени вводять рано і потім регулярно - відкладання підвищує ризик алергії.</i></p>")
    rows = []
    if missing:
        rows.append([B("🎯 Що ввести далі", callback_data="f:next", style="primary")])
    rows.append([B("‹ Щоденник", callback_data="f:diary"), B("🏠 Головна", callback_data="f:home")])
    return Rich("".join(parts)), M(rows)


def album(child) -> tuple[str, M]:
    """Альбом досягнень: зароблене з датою і що далі з прогресом."""
    have = achievements.earned(child["id"])
    now = achievements.metrics(child)
    done, todo = [], []
    for key, emoji, title, levels in achievements.ACH:
        got = have.get(key, [])
        nxt = next((lvl for lvl in levels if lvl not in {g for g, _ in got}), None)
        if got:
            lvl, ts = got[-1]
            stars = " " + "★" * len(got) + "☆" * (len(levels) - len(got)) if len(levels) > 1 else ""
            done.append(f"<li>{emoji} <b>{esc(title)}</b>{stars}<br>"
                        f"{esc(achievements.level_text(key, lvl))} · {local(ts).strftime('%d.%m')}</li>")
        if nxt is not None:
            left = max(nxt - now[key], 0)
            todo.append(f"<li>🔒 {emoji} {esc(title)}: {esc(achievements.level_text(key, nxt))}"
                        + (f" · {bar(now[key], nxt)} ще {left}" if nxt > 1 and key != "team" else "")
                        + "</li>")
    parts = [f"<h3>🏅 Досягнення {esc(child['name'])}</h3>",
             chips(f"зароблено {sum(len(v) for v in have.values())} з "
                   f"{sum(len(a[3]) for a in achievements.ACH)}")]
    if done:
        parts.append("<ul>" + "".join(done) + "</ul>")
    else:
        parts.append("<p>Поки порожньо. Перше досягнення - за перший записаний прийом їжі.</p>")
    if todo:
        parts.append("<h4>Далі</h4><ul>" + "".join(todo) + "</ul>")
    rows = [[B("🍽 Записати прийом їжі", callback_data="f:new", style="success")],
            [B("🏠 Головна", callback_data="f:home")]]
    return Rich("".join(parts)), M(rows)


def new_achievement(child, got: list[tuple[str, int]]) -> tuple[str, M]:
    """Повідомлення про нове. Кілька за раз - одним повідомленням."""
    lines = []
    for key, lvl in got:
        _, emoji, title, _ = achievements.BY_KEY[key]
        lines.append(f"{emoji} <b>{esc(title)}</b>: {esc(achievements.level_text(key, lvl))}")
    head = "🏅 <b>Нове досягнення!</b>" if len(got) == 1 else "🏅 <b>Нові досягнення!</b>"
    return (f"{head}\n\n" + "\n".join(lines) + f"\n\n{esc(child['name'])} молодець.",
            M([[B("🏅 Усі досягнення", callback_data="f:ach")]]))


def plan_tomorrow(child, plan: dict) -> tuple[str, M]:
    """План на завтра: один новий продукт і решта дня зі знайомого."""
    months = months_of(child)
    focus = plan["hold"] or plan["new"]
    names = lambda codes: " + ".join(catalog.name(c).lower() for c in codes)
    parts = ["<h3>📅 План на завтра</h3>",
             chips(f"{esc(child['name'])}, {growth.age_text(birth_date(child))}",
                   "з ложки" if spoon(child) else "шматочками")]
    if plan["hold"]:
        parts.append(f"<h4>⏳ Продовжуємо: {catalog.label(focus)}</h4>"
                     "<p>Правило трьох днів ще триває, тож нового завтра не вводимо. "
                     "Даємо той самий продукт зранку і дивимось реакцію.</p>")
    elif plan["new"]:
        prod = catalog.product(focus)
        parts.append(f"<h4>🆕 Новий продукт: {prod['emoji']} {esc(prod['name'])}{catalog.mark_allergen(focus)}</h4>")
    if focus:
        alone = len(plan["morning"]) == 1
        parts.append(f"<p><b>Сніданок{', окремо' if alone else ''}:</b> {esc(names(plan['morning']))}</p>"
                     f"<p>{esc(catalog.serving(focus, months, spoon(child)))}</p>"
                     "<p><i>Новий продукт - у першій половині дня, щоб до вечора побачити реакцію.</i></p>")
    else:
        parts.append("<p>За віком усе, що бот знає, уже введено. Тримаємо різноманіття.</p>")
    if plan["meals"]:
        parts.append("<h4>🍽 Решта дня - тільки знайоме</h4><ul>"
                     + "".join(f"<li><b>{t}:</b> {esc(names(p))}</li>" for t, p in plan["meals"])
                     + "</ul>")
    elif focus:
        parts.append("<p><i>Знайомих продуктів поки замало для окремих страв: решту дня - "
                     "грудне молоко чи суміш, як зазвичай.</i></p>")
    if plan["recipe"]:
        r = plan["recipe"]
        parts.append(f"<p>📖 Підійде рецепт: <b>{esc(r['title'])}</b> - усе, крім нового, вже введено.</p>")

    rows = []
    if focus:
        rows.append([B(f"🍽 Подали: {catalog.name(focus)}", callback_data=f"f:quick:{focus}", style="success")])
    nav = []
    if plan["new"]:
        nav.append(B("🔄 Інший новий", callback_data="f:plan:o"))
    if plan["meals"]:
        nav.append(B("🎲 Інше меню", callback_data="f:plan:m"))
    if nav:
        rows.append(nav)
    if plan["recipe"]:
        rows.append([B("📖 Рецепт", callback_data=f"f:r:{plan['recipe']['id']}")])
    rows.append([B("‹ Що ввести далі", callback_data="f:next"), B("🏠 Головна", callback_data="f:home")])
    return Rich("".join(parts)), M(rows)


ACT_WINDOWS = (7, 30)


def activity(child, user_id: int, days: int, others: dict[int, int]) -> tuple[str, M]:
    """Моя активність: скільки записала саме ця людина і де вона серед інших батьків.
    others - прийоми за той самий період у інших людей (своя родина автора не входить)."""
    _, ts_to, today = day_bounds()
    since, _, _ = day_bounds(-(days - 1))
    mine = db.meal_times(child["id"], since, user_id)
    family = db.meal_times(child["id"], since)
    my_days = {local(t).date() for t in mine}
    fam_days = {local(t).date() for t in db.meal_times(child["id"], since - 400 * 86400)}
    streak, d = 0, today if today in fam_days else today - timedelta(days=1)
    while d in fam_days:
        streak, d = streak + 1, d - timedelta(days=1)
    new = sum(1 for r in db.intro_map(child["id"]).values() if r["first_ts"] >= since)

    period = "7 днів" if days == 7 else "30 днів"
    parts = [f"<h3>🏆 Моя активність</h3>", chips(f"за {period}")]
    parts.append("<table>"
                 f"<tr><td>Записали ви</td><td>{plural(len(mine), 'прийом', 'прийоми', 'прийомів')}</td></tr>"
                 f"<tr><td>Днів із записами</td><td>{len(my_days)} з {days}</td></tr>"
                 f"<tr><td>Уся родина</td><td>{plural(len(family), 'прийом', 'прийоми', 'прийомів')}</td></tr>"
                 f"<tr><td>Нових продуктів</td><td>{new}</td></tr>"
                 f"<tr><td>Серія</td><td>{plural(streak, 'день', 'дні', 'днів')} поспіль</td></tr>"
                 "</table>")

    counts = sorted(others.values())
    if len(counts) >= 10 and mine:
        below = sum(1 for n in counts if n < len(mine))
        pct = round(100 * below / len(counts))
        median = counts[len(counts) // 2]
        top10 = counts[int(len(counts) * 0.9)]
        parts.append(f"<h4>Серед інших батьків</h4>")
        place = 1 + sum(1 for n in counts if n > len(mine))
        if pct >= 50:
            better = f"частіше, ніж <b>{min(pct, 99)}%</b>" if place > 1 else "<b>найчастіше</b> з усіх"
            parts.append(f"<p>Ви записуєте {better} батьків, які вели щоденник за цей час. "
                         f"Місце: <b>{place}</b> з {len(counts) + 1}.</p>")
        else:
            parts.append(f"<p>Найближча ціль: {plural(median, 'прийом', 'прийоми', 'прийомів')} за "
                         f"{period}, типовий рівень тих, хто веде щоденник. Чим більше записів, "
                         f"тим точніші поради.</p>")
        parts.append(f"<p>{bar(min(len(mine), top10), top10, 10)} ви: {len(mine)}</p>"
                     f"<p><i>Типово {plural(median, 'прийом', 'прийоми', 'прийомів')} за {period}, "
                     f"у найактивніших 10% - від {top10}. Порівнюємо з {len(counts)} "
                     f"{'людиною' if len(counts) % 10 == 1 and len(counts) % 100 != 11 else 'людьми'}.</i></p>")
    elif not mine:
        parts.append("<p>За цей час ви ще нічого не записали. Перший запис - кнопка "
                     "«🍽 Записати прийом їжі» на головній.</p>")

    other = 30 if days == 7 else 7
    rows = [[B(f"За {other} днів", callback_data=f"f:act:{other}")],
            [B("‹ Щоденник", callback_data="f:diary"), B("🏠 Головна", callback_data="f:home")]]
    return Rich("".join(parts)), M(rows)


# ─────────────────────────── зростання ───────────────────────────


def growth_screen(child) -> tuple[str, M]:
    sex = child["sex"]
    parts = [f"<h3>📈 Зростання · {esc(child['name'])}</h3>", chips(growth.age_text(birth_date(child)))]
    w = db.last_measure(child["id"], "weight_kg")
    h = db.last_measure(child["id"], "height_cm")
    if w:
        d = growth.age_days(birth_date(child), local(w["ts"]).date())
        parts.append(f"<h4>Вага · {local(w['ts']).strftime('%d.%m.%Y')}</h4>"
                     f"<p>{growth.summary(sex, 'wfa', d, w['weight_kg'], 'кг')}</p>")
    if h:
        d = growth.age_days(birth_date(child), local(h["ts"]).date())
        parts.append(f"<h4>Зріст · {local(h['ts']).strftime('%d.%m.%Y')}</h4>"
                     f"<p>{growth.summary(sex, 'lhfa', d, h['height_cm'], 'см')}</p>")
    if not w and not h:
        parts.append("<p>Ще немає вимірів. Додайте вагу і зріст - покажу перцентиль за ВООЗ.</p>")

    hist = db.measures(child["id"], 10)
    if len(hist) > 1:
        def cells(m) -> str:
            kg = f"{m['weight_kg']:.2f} кг" if m["weight_kg"] else ""
            cm = f"{m['height_cm']:.1f} см" if m["height_cm"] else ""
            return f"<tr><td>{local(m['ts']).strftime('%d.%m.%Y')}</td><td>{kg}</td><td>{cm}</td></tr>"
        body = "".join(cells(m) for m in hist)
        parts.append("<details><summary>Історія вимірів</summary><table>"
                     "<tr><th>Дата</th><th>Вага</th><th>Зріст</th></tr>" + body + "</table></details>")
    parts.append("<p><i>Перцентиль - це не оцінка. Стабільний коридор важливіший за саме число.</i></p>")

    rows = [[B("⚖️ Додати вагу", callback_data="f:gw", style="primary"),
             B("📏 Додати зріст", callback_data="f:gh", style="primary")],
            [B("🏠 Головна", callback_data="f:home")]]
    return Rich("".join(parts)), M(rows)


# ─────────────────────────── налаштування ───────────────────────────


def settings(child, family_id: int, remind: bool, show_rare: bool = False) -> tuple[str, M]:
    members = db.family_members(family_id)
    lines = [f"⚙️ <b>Налаштування</b>", "",
             f"Дитина: <b>{esc(child['name'])}</b>, {esc(child['birth_date'])}, "
             f"{'дівчинка' if child['sex'] == 'girls' else 'хлопчик'}",
             f"Доступ мають: " + ", ".join(esc(m["name"]) for m in members),
             f"Нагадування: {'увімкнені' if remind else 'вимкнені'}",
             f"Вітамін D: {vitd_summary(db.vitd_conf(family_id))}",
             f"Подача: {'з ложки (пюре, каші)' if spoon(child) else 'шматочками (BLW)'}",
             f"Рідкісні продукти: {'показані' if show_rare else 'сховані'}",
             "",
             "<i>Бібліотека продуктів - довідка на основі рекомендацій ВООЗ, AAP і EFSA. "
             "Вона не замінює педіатра.</i>"]
    rows = [[B("➕ Додати дорослого", callback_data="f:inv")],
            [B(("🔕 Вимкнути" if remind else "🔔 Увімкнути") + " нагадування", callback_data="f:rem")],
            [B("☀️ Вітамін D", callback_data="f:vd"),
             B("🥄 Подача: " + ("шматочками" if spoon(child) else "з ложки"), callback_data="f:style")],
            [B(("🙈 Сховати" if show_rare else "🥬 Показати") + " рідкісні продукти",
               callback_data="f:rare:set")],
            [B("✏️ Змінити дату народження", callback_data="f:editbirth")],
            [B("💬 Зворотний звʼязок", callback_data="f:fb")],
            [B("ℹ️ Інструкція", callback_data="f:guide"),
             B("🔒 Дані і приватність", callback_data="f:privacy")],
            [B("🏠 Головна", callback_data="f:home")]]
    return "\n".join(lines), M(rows)


VITD_TIMES = ("07:00", "08:00", "09:00", "10:00", "12:00", "18:00", "20:00", "21:00")


def vitd_summary(conf: dict) -> str:
    if not conf["on"]:
        return "вимкнено"
    return f"{'щодня' if conf['every'] == 1 else 'через день'} о {conf['at']}"


def vitd(child, family_id: int) -> tuple[str, M]:
    conf = db.vitd_conf(family_id)
    members = db.family_members(family_id)
    lines = ["☀️ <b>Вітамін D</b>", "",
             "Одне повідомлення у вибраний час. Без повторів: якщо пропустили, "
             "бот не нагадує ще раз.", ""]
    if conf["on"]:
        start = date.fromisoformat(conf["from"]).strftime("%d.%m.%Y")
        who = next((m["name"] for m in members if m["user_id"] == conf["chat"]), None)
        lines += [f"Зараз: <b>{vitd_summary(conf)}</b>",
                  f"Відлік від: {start}",
                  f"Кому: {esc(who) if who else 'всім дорослим'}"]
    else:
        lines.append("Зараз: <b>вимкнено</b>")

    def mark(on: bool, text: str) -> str:
        return ("✓ " if on else "") + text

    rows = [[B("🔕 Вимкнути" if conf["on"] else "🔔 Увімкнути", callback_data="f:vd:on",
               style=None if conf["on"] else "success")]]
    if conf["on"]:
        rows.append([B(mark(conf["every"] == 1, "Щодня"), callback_data="f:vd:ev:1"),
                     B(mark(conf["every"] == 2, "Через день"), callback_data="f:vd:ev:2")])
        if conf["every"] == 2:
            today = datetime.now(TZ).date()
            rows.append([B(mark(conf["from"] == today.isoformat(), "Сьогодні день D"),
                           callback_data="f:vd:from:0"),
                         B(mark(conf["from"] == (today + timedelta(days=1)).isoformat(),
                                "Завтра день D"), callback_data="f:vd:from:1")])
        rows += _rows([B(mark(conf["at"] == t, t), callback_data=f"f:vd:at:{t.replace(':', '')}")
                       for t in VITD_TIMES], 4)
        rows.append([B("✏️ Свій час", callback_data="f:vd:attext")])
        if len(members) > 1:
            rows.append([B(mark(conf["chat"] is None, "Всім дорослим"), callback_data="f:vd:to:all"),
                         B(mark(conf["chat"] is not None, "Тільки мені"), callback_data="f:vd:to:me")])
    rows.append([B("‹ Налаштування", callback_data="f:set"), B("🏠 Головна", callback_data="f:home")])
    return "\n".join(lines), M(rows)


def privacy(child) -> tuple[str, M]:
    lines = ["🔒 <b>Дані і приватність</b>", "",
             "<b>Що зберігається.</b> Імʼя дитини, дата народження, стать, ваші записи "
             "про їжу, реакції, виміри ваги і зросту, вміст холодильника. "
             "Ще - які кнопки ви натискаєте і що шукаєте текстом, щоб ми бачили, "
             "чого боту бракує. Цей журнал живе пів року.",
             "",
             "<b>Де.</b> На приватному сервері, у власній базі, окремо для кожної родини. "
             "Чужа родина ваших записів не бачить.",
             "",
             "<b>Що НЕ робиться.</b> Дані не передаються третім особам, не продаються і "
             "не використовуються для реклами.",
             "",
             "<b>Видалення.</b> Кнопка нижче стирає профіль дитини, всі записи родини "
             "і журнал дій назавжди, без можливості відновити.",
             "",
             "<i>Бот це довідка на основі рекомендацій ВООЗ, AAP і EFSA. "
             "Він не замінює педіатра.</i>"]
    rows = [[B("🗑 Видалити всі дані родини", callback_data="f:wipe")],
            [B("‹ Налаштування", callback_data="f:set"), B("🏠 Головна", callback_data="f:home")]]
    return "\n".join(lines), M(rows)


def wipe_confirm(child) -> tuple[str, M]:
    text = ("🗑 <b>Видалити всі дані?</b>\n\n"
            f"Зникне профіль <b>{esc(child['name'])}</b>, увесь щоденник, журнал реакцій, "
            "виміри зростання і холодильник. Це стосується обох дорослих у родині.\n\n"
            "Відновити не вийде: резервних копій ваших записів немає.")
    rows = [[B("Так, видалити назавжди", callback_data="f:wipe:yes")],
            [B("‹ Ні, скасувати", callback_data="f:set")]]
    return text, M(rows)


# ─────────────────────────── холодильник ───────────────────────────


STALE_DAYS = 7


def _stock_lines(stock: list[str], since: dict[str, int] | None = None) -> list[str]:
    """Вміст холодильника по полицях, у тому ж порядку, що й бібліотека."""
    lines: list[str] = []
    now_ts = db.now()
    for key in catalog.nav_keys():
        emoji, title = catalog.key_title(key)
        codes = [c for c in stock if catalog.key_of(c) == key]
        if not codes:
            continue
        parts = []
        for c in codes:
            days = (now_ts - since.get(c, now_ts)) // 86400 if since else 0
            old = f" <i>({days} дн.)</i>" if days >= STALE_DAYS else ""
            parts.append(f"{catalog.name(c)}{old}")
        lines.append(f"{emoji} <b>{title}</b>: " + ", ".join(parts))
    return lines


def fridge(child, stock: list[str], since: dict[str, int], intro: dict) -> tuple[str, M]:
    lines = ["🧊 <b>Холодильник</b>", ""]
    if not stock:
        lines += ["Поки порожній.",
                  "",
                  "Склади сюди те, що є вдома - і я збиратиму тарілки тільки з цього "
                  "та підкажу, що докупити на тиждень.",
                  "",
                  "Найшвидше через «✍️ Списком»: напиши <code>морква, курка, йогурт</code>."]
    else:
        lines.append(f"Удома {plural(len(stock), 'продукт', 'продукти', 'продуктів')}:")
        lines += _stock_lines(stock, since)
        need = suggest.missing_slots(stock)
        lines.append("")
        if need:
            lines.append("Для повної тарілки бракує: "
                         + ", ".join(suggest.SLOT_LABEL[s] for s in need))
        else:
            lines.append("Тарілку можна скласти хоч зараз: є рослинне, вуглевод, білок і жир.")
        stale = [c for c in stock if (db.now() - since.get(c, db.now())) // 86400 >= STALE_DAYS]
        if stale:
            lines.append(f"Лежить понад тиждень: " + ", ".join(catalog.name(c) for c in stale)
                         + ". Перевір, чи не зіпсувалось.")

    rows: list[list[B]] = []
    if stock:
        rows.append([B("🍲 Скласти тарілку з цього", callback_data="f:frgen")])
    rows.append([B("🛒 Список покупок на тиждень", callback_data="f:frbuy")])
    rows.append([B("🍲 Ідеї тарілок за віком", callback_data="f:plates")])
    rows.append([B("➕ Додати", callback_data="f:fradd"), B("✍️ Списком", callback_data="f:frtext")])
    if stock:
        rows.append([B("➖ Прибрати", callback_data="f:frdel"),
                     B("🧹 Спорожнити", callback_data="f:frclear")])
    rows.append([B("🏠 Головна", callback_data="f:home")])
    return "\n".join(lines), M(rows)


def fridge_pick(child, key: str, stock: list[str], intro: dict,
                show_rare: bool = False) -> tuple[str, M]:
    months = months_of(child)
    key = catalog.default_key(key)
    emoji, title = catalog.key_title(key)
    lines = ["➕ <b>Що поклали в холодильник</b>",
             f"{emoji} {title}",
             "",
             "Тисни продукт, щоб покласти або забрати. ✔️ значить, що він уже вдома."]
    if stock:
        lines += ["", "Зараз усередині: " + ", ".join(catalog.name(c) for c in stock)]
    rows = sub_tabs(key, "f:fradd")
    items = shelf_items(key, show_rare)
    rows += _rows([B(("✔️ " if p["code"] in stock else mark(p["code"], intro, months) + " ")
                     + catalog.name(p["code"]),
                     callback_data=f"f:frt:{p['code']}") for p in items])
    toggle = rare_toggle(key, f"fradd:{key}", show_rare)
    if toggle:
        rows.append(toggle)
    nav = [B(f"{catalog.GROUPS[g][0]} {catalog.GROUPS[g][1]}",
             callback_data=f"f:fradd:{g}.{catalog.SUBS_OF[g][0]}" if g in catalog.SUBS_OF
             else f"f:fradd:{g}")
           for g in catalog.GROUP_ORDER]
    rows += _rows(nav)
    rows.append([B("🧊 Готово", callback_data="f:fr")])
    return "\n".join(lines), M(rows)


def fridge_remove(child, stock: list[str]) -> tuple[str, M]:
    lines = ["➖ <b>Що закінчилось</b>", "",
             "Тисни продукт, щоб прибрати його з холодильника."]
    rows = _rows([B(catalog.label(c), callback_data=f"f:frx:{c}") for c in stock])
    rows.append([B("🧊 Готово", callback_data="f:fr")])
    return "\n".join(lines), M(rows)


FRIDGE_TEXT_PROMPT = (
    "✍️ Напиши одним повідомленням, що є вдома - через кому.\n\n"
    "Наприклад: <code>морква, гречка, індичка, йогурт, олія</code>\n\n"
    "Розпізнаю до 20 продуктів за раз і складу їх у холодильник."
)


def fridge_added(added: list[str], known: list[str]) -> tuple[str, M]:
    lines = []
    if added:
        lines.append("🧊 Поклав у холодильник: " + ", ".join(catalog.label(c) for c in added))
    if known:
        lines.append("Уже було: " + ", ".join(catalog.name(c) for c in known))
    if not lines:
        lines.append("Не впізнав жодного продукту. Напиши простіше, наприклад: морква, гречка.")
    rows = [[B("🧊 Холодильник", callback_data="f:fr")],
            [B("✍️ Ще списком", callback_data="f:frtext"), B("🏠 Головна", callback_data="f:home")]]
    return "\n".join(lines), M(rows)


def fridge_plates(child, plates: list[list[str]], stock: list[str]) -> tuple[str, M]:
    lines = ["🍲 <b>Тарілки з холодильника</b>",
             f"{catalog.band_title(catalog.band_for(months_of(child)))} · зібрано тільки з того, "
             "що є вдома.", ""]
    if not plates:
        lines.append("З наявного тарілку зібрати не вдалось. Схоже, продукти ще зарано за віком "
                     "або їх надто мало. Подивись список покупок.")
    else:
        need = suggest.missing_slots(stock)
        if need:
            lines.append("У холодильнику немає нічого під: "
                         + ", ".join(suggest.SLOT_LABEL[s] for s in need)
                         + ". Тому частина тарілок неповна.")
        lines.append("Тисни варіант, щоб побачити, як подавати кожен продукт.")
    rows = [[_plate_button(pl, "f:gp")] for pl in plates]
    rows.append([B("🎲 Ще", callback_data="f:frgen"),
                 B("🛒 Що докупити", callback_data="f:frbuy")])
    rows.append([B("🧊 Холодильник", callback_data="f:fr"), B("🏠 Головна", callback_data="f:home")])
    return "\n".join(lines), M(rows)


def fridge_shopping(child, items: list[tuple[str, str]], stock: list[str],
                    intro: dict) -> tuple[str, M]:
    months = months_of(child)
    lines = ["🛒 <b>Покупки на тиждень</b>",
             f"{esc(child['name'])}, {growth.age_text(birth_date(child))} "
             f"Рахую на 7 днів уперед і віднімаю те, що вже в холодильнику.", ""]
    if not items:
        lines.append("Докуповувати нічого: на тиждень удома вистачає всього.")
    else:
        for code, why in items:
            prod = catalog.product(code)
            state = "уже їсть" if intro.get(code) and intro[code]["status"] == "ok" else (
                "нове" if code not in intro else "знайоме")
            lines.append(f"{mark(code, intro, months)} {prod['emoji']} <b>{esc(prod['name'])}</b>"
                         f"{catalog.mark_allergen(code)} · {state}")
            lines.append(f"   <i>{esc(why)}</i>")
        fresh = [c for c, _ in items if c not in intro]
        if len(fresh) > 2:
            lines += ["", f"<i>Нового в списку {plural(len(fresh), 'позиція', 'позиції', 'позицій')} - це багато для одного "
                      "тижня. Візьми 2-3 і вводь по одному, тримаючи кожне 2-3 дні поспіль. "
                      "Решта нікуди не дінеться.</i>"]
        elif fresh:
            lines += ["", "<i>Нове тут: " + ", ".join(catalog.name(c) for c in fresh)
                      + ". Вводь по одному і тримай 2-3 дні поспіль, "
                      "щоб реакцію було видно однозначно.</i>"]
    rows = []
    if items:
        rows.append([B("✅ Купив, усе в холодильник", callback_data="f:frbuyall")])
    rows.append([B("🧊 Холодильник", callback_data="f:fr"), B("🏠 Головна", callback_data="f:home")])
    return "\n".join(lines), M(rows)


# ─────────────────────── що ввести далі ───────────────────────


# ─────────────────────── онбординг: дата і перший запис ───────────────────────

MONTHS_SHORT = ("січ", "лют", "бер", "квіт", "трав", "черв",
                "лип", "серп", "вер", "жовт", "лист", "груд")
# день усередині тижня: точність до тижня порадам вистачає, а друкувати дату не треба
DAY_BUCKETS = (("1-7", 4), ("8-14", 11), ("15-21", 18), ("22-31", 25))


def birth_year(name: str) -> tuple[str, M]:
    this = datetime.now(TZ).year
    rows = [[B(str(y), callback_data=f"f:by:{y}") for y in range(this - 2, this + 1)],
            [B("✏️ Ввести дату текстом", callback_data="f:btext")]]
    return f"Дата народження: {esc(name)}. Оберіть рік:", M(rows)


def birth_month(name: str, year: int) -> tuple[str, M]:
    today = datetime.now(TZ).date()
    last = today.month if year == today.year else 12
    btns = [B(MONTHS_SHORT[m - 1], callback_data=f"f:bm:{year}:{m}") for m in range(1, last + 1)]
    rows = _rows(btns, 4) + [[B("‹ Рік", callback_data="f:bback")]]
    return f"{esc(name)}, {year}. Місяць:", M(rows)


def birth_day(name: str, year: int, month: int) -> tuple[str, M]:
    today = datetime.now(TZ).date()
    btns = [B(title, callback_data=f"f:bd:{year}:{month}:{day}") for title, day in DAY_BUCKETS
            if date(year, month, int(title.split("-")[0])) <= today]
    rows = [btns, [B("‹ Місяць", callback_data=f"f:by:{year}"),
                   B("✏️ Точна дата", callback_data="f:btext")]]
    return f"{esc(name)}, {MONTHS_SHORT[month - 1]} {year}. Яке число?", M(rows)


# стартові продукти: те, з чого зазвичай починають, щоб відмітити вже спробуване одним тапом
FIRST_TRIED = ("kabachok", "brokoli", "tsvitna", "garbuz", "morkva", "yabluko", "grusha",
               "banan", "avokado", "vivsianka", "grechka", "yaitse", "kurka", "indychka", "yogurt")
SHORT_NAMES = {"tsvitna": "Цвітна", "yogurt": "Йогурт"}


def first_tried(child, picked: list[str]) -> tuple[str, M]:
    """Перший екран після профілю: одне питання замість панелі з дев'яти кнопок."""
    ate = "їла" if child["sex"] == "girls" else "їв"
    parts = [f"<h3>Що {esc(child['name'])} вже {ate}?</h3>",
             "<p>Тапніть усе, що вже пробували, навіть один раз. З цього почнеться щоденник, "
             "і я підкажу, що вводити далі.</p>"]
    btns = []
    for code in FIRST_TRIED:
        p = catalog.product(code)
        if not p:
            continue
        mark = "✔️ " if code in picked else ""
        btns.append(B(f"{mark}{p['emoji']} {SHORT_NAMES.get(code, p['name'])}",
                      callback_data=f"f:ft:{code}"))
    btns.append(B("🔎 Інше", callback_data="f:fother"))
    rows = _rows(btns, 3)
    if picked:
        rows.append([B(f"Зберегти ({len(picked)})", callback_data="f:fsave", style="success")])
    none = [B("🍼 Ще не почали прикорм", callback_data="f:fnone",
              **({"style": "primary"} if months_of(child) < PREP_UNTIL and not picked else {}))]
    if months_of(child) < PREP_UNTIL and not picked:
        rows.insert(0, none)
    else:
        rows.append(none)
    rows.append([B("Пропустити", callback_data="f:home")])
    return Rich("".join(parts)), M(rows)


def first_saved(child, codes: list[str]) -> str:
    return (f"📔 <b>Записано у щоденник: {esc(child['name'])}</b>\n"
            + " · ".join(catalog.label(c) for c in codes))


def next_up(child, intro: dict, items: list[tuple[str, str]], hold: list[str],
            stock: list[str]) -> tuple[str, M]:
    """Головна підказка бота: один наступний продукт, і чому саме він."""
    months = months_of(child)
    total = len(catalog.products())
    parts = ["<h3>🎯 Що ввести далі</h3>",
             chips(f"{esc(child['name'])}, {growth.age_text(birth_date(child))}", f"введено {len(intro)} з {total}")]
    rows: list[list[B]] = []

    if hold:
        now_ts = db.now()
        lis = ""
        for code in hold[:3]:
            row = intro[code]
            days = max((now_ts - row["first_ts"]) // 86400 + 1, 1)
            state = "під наглядом після реакції" if row["status"] == "watch" else f"день {days} з 3"
            lis += f"<li>{catalog.label(code)} - {state}</li>"
        parts += ["<h4>⏳ Зараз новий продукт вводити рано</h4>", f"<ul>{lis}</ul>",
                  "<p>Правило трьох днів: новий продукт тримаємо в меню 2-3 дні поспіль і "
                  "не додаємо поруч інший новий, інакше не зрозуміло, на що була реакція.</p>",
                  "<p><i>Коли переносить добре - познач «освоєно», і я одразу підкажу наступний.</i></p>"]
        rows += [[B(f"✅ Освоєно: {catalog.name(c)}", callback_data=f"f:st:{c}:ok", style="success"),
                  B("⚠️ Реакція", callback_data=f"f:react:{c}", style="danger")] for c in hold[:3]]
        rows.append([B("📅 План на завтра", callback_data="f:plan", style="primary")])
        rows.append([B("🍽 Записати прийом їжі", callback_data="f:new"),
                     B("🏠 Головна", callback_data="f:home")])
        return Rich("".join(parts)), M(rows)

    if not items:
        parts.append("<p>За віком уже введено все, що бот знає. Далі просто тримаємо "
                     "різноманіття: щодня рослинне, вуглевод, білок і жир.</p>")
        rows.append([B("🍲 Ідеї тарілок", callback_data="f:plates"),
                     B("🏠 Головна", callback_data="f:home")])
        return Rich("".join(parts)), M(rows)

    parts.append("<p>Порядок такий: спершу алергени, далі те, чого в раціоні найменше.</p>")
    for code, why in items:
        prod = catalog.product(code)
        parts.append(f"<h4>{prod['emoji']} {esc(prod['name'])}{catalog.mark_allergen(code)} · {catalog.age_label(prod['from_month'])}</h4>"
                     f"<p><i>{esc(why)}</i></p><p>{esc(catalog.serving(code, months, spoon(child)))}</p>")
    parts.append("<p><i>Вводимо по одному і тримаємо 2-3 дні поспіль. Це підказка, не "
                 "черга: можна дати будь-який інший продукт і позначити його в «Продуктах».</i></p>")

    for code, _ in items:
        rows.append([B(f"🍽 Подали: {catalog.name(code)}", callback_data=f"f:quick:{code}", style="success"),
                     B("🥕 Картка", callback_data=f"f:p:{code}")])
    fresh = [c for c, _ in items if c not in stock]
    rows.append([B("🔄 Інші варіанти", callback_data="f:nxo")]
                + ([B("🧊 У холодильник", callback_data="f:nxfr")] if fresh else []))
    rows.append([B("📅 План на завтра", callback_data="f:plan", style="primary")])
    rows.append([B("🛒 Покупки на тиждень", callback_data="f:frbuy"),
                 B("🏠 Головна", callback_data="f:home")])
    return Rich("".join(parts)), M(rows)


# ─────────────────────────── інструкція ───────────────────────────


GUIDE_ORDER = ["start", "prep", "meal", "fridge", "lib", "allergy", "growth", "ach", "family"]

GUIDE_TITLES = {
    "start": "🚀 З чого почати",
    "prep": "🌱 До 6 місяців",
    "meal": "🍽 Записи і тарілки",
    "fridge": "🧊 Холодильник",
    "lib": "🥕 Продукти і подача",
    "allergy": "⚠️ Алергени і реакції",
    "growth": "📈 Зростання",
    "ach": "🏅 Досягнення",
    "family": "👨‍👩‍👧 Родина і дані",
}


def _guide_text(key: str) -> str:
    total = len(catalog.products())
    plates_n = len(catalog.plates())
    recipes_n = len(catalog.recipes())
    allerg_n = len(catalog.allergen_codes())

    if key == "start":
        return (
            "🚀 <b>З чого почати</b>\n\n"
            "<b>1. Профіль дитини.</b> Імʼя, дата народження, стать. Стать потрібна "
            "тільки для таблиць зростання ВООЗ: вони різні для дівчаток і хлопчиків.\n\n"
            "<b>2. Перший запис.</b> «🍽 Записати прийом їжі» → обери продукти "
            "(до 6 за раз) → «✅ Подали» → вкажи, який це прийом їжі. Два-три тапи.\n\n"
            "<b>3. Далі буде швидше.</b> Запис відкривається на вкладці «🧊 З холодильника», "
            "поруч «⭐ Часте» - те, що ви даєте найчастіше. Щоденний запис коштує пару дотиків.\n\n"
            "<b>4. Забули записати?</b> На екрані вибору прийому їжі є «Сьогодні / Вчора / "
            "📅 Інша дата» - запис ляже в потрібний день.\n\n"
            "<b>Що лишається в чаті.</b> Кожен запис приходить окремим повідомленням "
            "без кнопок. Його вже ніщо не перезапише: гортаєш чат і бачиш, що дитина "
            "їла сьогодні, вчора і тиждень тому.\n\n"
            "<b>Прикорм починають з 6 місяців</b> і не раніше 4. До того грудне молоко "
            "або суміш закривають усе. Бот показує вік дитини і підбирає подачу під нього. "
            "Ще не почали? Наступна сторінка, «🌱 До 6 місяців»."
        )

    if key == "prep":
        return (
            "🌱 <b>До 6 місяців</b>\n\n"
            "Поки дитина молодша за 6 місяців і нічого не вводили, головна показує відлік "
            "до старту прикорму замість порожнього щоденника.\n\n"
            "<b>🌱 Як підготуватись.</b> Ознаки готовності, що купити заздалегідь.\n\n"
            "<b>📅 План першого тижня.</b> Перші продукти по одному, по 2-3 дні кожен, "
            "з подачею під вік. До 6 місяців план можна переглянути наперед, з 6 місяців "
            "біля кожного продукту є «🍽 Подали».\n\n"
            "<b>Нагадування.</b> У день, коли виповниться 6 місяців, о 11:00 прийде одне "
            "повідомлення з планом. Далі головна стає звичайним щоденником, а поки в ньому "
            "порожньо, зверху висить «🌱 Можна починати» з тим самим планом.\n\n"
            "<b>Почали раніше</b> за порадою лікаря? «🍽 Вже почали» - і бот перейде у "
            "звичайний режим з першого запису."
        )

    if key == "meal":
        return (
            "🍽 <b>Записи і тарілки</b>\n\n"
            "<b>Правило чотирьох слотів.</b> Збалансована тарілка це рослинне + "
            "вуглевод + білок + жир. Бот показує, чого бракує, просто під час набору.\n\n"
            "Жир додаємо завжди: без нього не засвояться вітаміни A, D, E і K.\n\n"
            f"<b>🍲 Ідеї тарілок.</b> {plates_n} готових наборів за віком (6-8, 9-11, 12+ міс) "
            "лежать у Холодильнику. Кнопка «Подали цю тарілку» кидає весь склад у щоденник "
            "одним дотиком.\n\n"
            "<b>🎲 Ще варіанти.</b> Збирає нові тарілки з продуктів, дозволених за віком. "
            "Знайоме бере частіше за нове, і в одній тарілці максимум один новий продукт.\n\n"
            "<b>🔍 Пошук просто в чаті.</b> Напиши «гарбузовий суп» - покажу рецепт, "
            "напиши «морква і курка» - доберу решту тарілки. Кнопку тиснути не треба.\n\n"
            "<b>🎯 Що ввести далі.</b> Кнопка на головній: наступні продукти і чому саме "
            "вони, «🔄 Інші варіанти» показує ще. Поки триває правило трьох днів, вона про це "
            "і скаже.\n\n"
            "<b>📅 План на завтра.</b> Кнопка на екрані «Що ввести далі»: один новий продукт "
            "на сніданок, решта дня зі знайомого. «🔄 Інший новий» міняє новий продукт, "
            "«🎲 Інше меню» - решту дня.\n\n"
            f"<b>👨‍🍳 Рецепти.</b> {plural(recipes_n, 'рецепт', 'рецепти', 'рецептів')} з кроками, "
            "порадою і як заморожувати. "
            "Фільтруються за віком дитини.\n\n"
            "<b>📔 Щоденник.</b> День за днем, нотатка до прийому, видалення помилкового "
            "запису. «📊 Зведення» показує прогрес по групах і скільки алергенів уже введено."
        )

    if key == "fridge":
        return (
            "🧊 <b>Холодильник</b>\n\n"
            "Це список того, що зараз є вдома. Він спільний для обох дорослих у родині.\n\n"
            "<b>Як наповнити.</b> Два способи:\n"
            "· «➕ Додати» - тапати продукти по полицях, ✔️ значить що вже вдома;\n"
            "· «✍️ Списком» - написати одним повідомленням: "
            "<code>морква, гречка, індичка, олія, йогурт</code>. До 20 позицій за раз.\n\n"
            "<b>🍲 Скласти тарілку з цього.</b> Той самий генератор, але обмежений тим, "
            "що є вдома. Якщо під якийсь слот нічого немає, бот прямо скаже, чого бракує.\n\n"
            "Холодильник ще й перша вкладка запису їжі: «🍽 Записати прийом їжі» одразу "
            "показує те, що вдома, а вся бібліотека лишається поруч.\n\n"
            "<b>🛒 Список покупок на тиждень.</b> Рахує норму на 7 днів: 6 різних "
            "рослинних, 3 вуглеводи, 4 білки, 2 жири. Усередині білка стежить за "
            "різноманіттям: мʼясо двічі, риба, яйця і бобові по разу.\n\n"
            "Далі віднімає те, що вже вдома, спершу пропонує те, що дитина вже їсть, "
            "і додає рівно один новий продукт та один ще не введений алерген. "
            "Кнопка «✅ Купив» перекладає весь список у холодильник.\n\n"
            "<b>Що закінчилось</b> прибирається вручну через «➖ Прибрати». Автоматично "
            "продукти не списуються: морква від однієї порції не зникає."
        )

    if key == "lib":
        return (
            "🥕 <b>Продукти і подача</b>\n\n"
            f"{plural(total, 'картка', 'картки', 'карток')} на десяти полицях. У кожній головне не «можна чи ні», а "
            "<b>як подавати саме зараз</b>, за віком твоєї дитини: розмір шматка, форма, "
            "ступінь розварювання.\n\n"
            "<b>🥄 З ложки чи шматочками.</b> Подача буває двох видів: пюре і каші з ложки "
            "або шматочки, які дитина бере сама (BLW). Обирається при старті, змінюється в "
            "Налаштуваннях → «🥄 Подача».\n\n"
            "<b>Полиці білка.</b> Мʼясо, риба, яйця і бобові лежать окремо, щоб не шукати "
            "індичку серед сорока позицій. Для балансу тарілки це однаково один білок.\n\n"
            "<b>Позначки у списках:</b>\n"
            "✅ введено · 🆕 ще не пробували · 👀 під наглядом після реакції · "
            "⛔ уникаємо · ⏳ зарано за віком\n"
            f"❗ алерген ({plural(allerg_n, 'позиція', 'позиції', 'позицій')})\n\n"
            "<b>Ризик подавитися</b> вказаний у кожній картці. Це не заборона, а "
            "інструкція з нарізки: виноград і черрі різати вздовж на чотири, "
            "сосиску ніколи кружальцями, цілі горіхи до 4-5 років тільки меленими.\n\n"
            "<b>🥬 Рідкісні продукти.</b> Кейл, мангольд, топінамбур і решта екзотики "
            "за замовчуванням сховані: у списках вони тільки заважають. Кнопка «Показати "
            "рідкісні» внизу полиці вмикає їх назавжди, у налаштуваннях є той самий перемикач. "
            "Бот їх не пропонує сам, але якщо такий продукт є вдома - працює з ним як зі звичайним.\n\n"
            "<b>Кнопки в картці.</b> «🍽 Подали зараз» записує продукт одним дотиком. "
            "«✅ Освоєно» і «⛔ Уникаємо» міняють статус. Продукти зі статусом «уникаємо» "
            "бот більше не пропонує в тарілках."
        )

    if key == "allergy":
        return (
            "⚠️ <b>Алергени і реакції</b>\n\n"
            "<b>Правило трьох днів.</b> Новий продукт тримають у меню 2-3 дні поспіль і "
            "не вводять поруч інший новий. Тоді зрозуміло, на що саме була реакція. "
            "Бот нагадає ввечері і через три дні сам.\n\n"
            "<b>Алергени вводять рано і регулярно.</b> Це головна зміна в рекомендаціях "
            "за останні роки: відкладати яйце, рибу, арахіс чи молочне на потім не "
            "захищає від алергії, а підвищує її ризик. Введений алерген треба давати "
            "регулярно, інакше переносимість втрачається.\n\n"
            "У «📊 Зведенні» видно, скільки алергенів уже введено і які ще не пробували.\n\n"
            "<b>Якщо щось пішло не так.</b> Кнопка «⚠️ Реакція» у картці продукту або "
            "одразу після запису. Продукт автоматично йде в статус «👀 під наглядом», "
            "а запис лягає в журнал реакцій з датою. Цей журнал і є те, що варто "
            "показати педіатру чи алергологу.\n\n"
            "🚨 <b>Набряк губ чи язика, утруднене дихання це невідкладний стан.</b> "
            "Викликайте швидку (103) одразу, не чекайте, поки минеться.\n\n"
            "Разова реакція часто буває на нову текстуру. Висип чи блювання, що "
            "повторились, це вже привід до лікаря."
        )

    if key == "growth":
        return (
            "📈 <b>Зростання</b>\n\n"
            "Додаєш вагу і зріст, бот рахує <b>перцентиль і z-score за офіційними "
            "таблицями ВООЗ</b> для віку і статі дитини, з точністю до дня.\n\n"
            "Таблиці взяті з оригінальних файлів ВООЗ, не з інтернету: вага до віку і "
            "зріст до віку, 0-5 років, обидві статі.\n\n"
            "<b>Як читати.</b> Перцентиль 25 означає, що з ста дітей того ж віку "
            "двадцять пʼять важать менше. Це не оцінка і не діагноз.\n\n"
            "⭐ <b>Важливий не сам перцентиль, а коридор.</b> Дитина, яка стабільно "
            "тримається на 20-му перцентилі, розвивається нормально. Насторожує різкий "
            "перехід між коридорами, і це вже питання до педіатра.\n\n"
            "Історія вимірів зберігається, тому коридор видно."
        )

    if key == "ach":
        return (
            "🏅 <b>Досягнення</b>\n\n"
            "Значки за те, що ви вже зробили: перший запис, 5-100 продуктів, введені "
            "групи алергенів, серія днів поспіль, збалансована тарілка, щоденник на двох "
            "дорослих. Найсвіжіші видно на головній, усі - у «🏅 Досягнення».\n\n"
            "<b>Великі віхи</b> (25, 50, 100 продуктів, нова група алергенів, 30 днів поспіль) "
            "приходять картинкою, яку зручно зберегти на памʼять.\n\n"
            "<b>📅 Підсумок місяця.</b> 1-го числа о 11:00 картинка з підсумком минулого "
            "місяця, якщо за нього було 10+ прийомів їжі.\n\n"
            "<b>🏆 Моя активність</b> (у Щоденнику): скільки записали саме ви, серія днів і "
            "місце серед інших батьків за 7 або 30 днів. Імен інших там немає."
        )

    if key == "family":
        return (
            "👨‍👩‍👧 <b>Родина і дані</b>\n\n"
            "<b>Один профіль дитини на кількох дорослих.</b> Мама і тато пишуть в один "
            "щоденник і бачать однакову картину. Налаштування → «➕ Додати дорослого» "
            "видає одноразовий код, він діє добу. Другий дорослий просто надсилає цей код "
            "боту в чат, навіть якщо вже встиг завести свій профіль, і тисне «✅ Приєднатися».\n\n"
            "<b>🔔 Нагадування.</b> Ввечері о 20:30 один підсумок дня: як пройшов новий "
            "продукт, що минуло три дні, і нагадування записати, якщо за день порожньо, а ви "
            "вели щоденник останні дні. Вимикаються однією кнопкою в налаштуваннях.\n\n"
            "<b>☀️ Вітамін D.</b> Налаштування → «☀️ Вітамін D»: щодня або через день, у "
            "вибраний час, усім дорослим чи лише собі. Кнопка «✅ Дали» під нагадуванням.\n\n"
            "<b>Дані.</b> Щоденник живе на приватному сервері, окремо для кожної родини. "
            "Чужа родина не бачить вашу. Нічого не передається третім особам і не "
            "використовується для реклами. У налаштуваннях є кнопка, яка видаляє "
            "профіль дитини і всі записи назавжди.\n\n"
            "<b>⚠️ Головне.</b> Бот це довідка на основі рекомендацій ВООЗ, AAP і EFSA. "
            "Він не ставить діагнозів і не замінює педіатра. Якщо в дитини алергія, "
            "особливості розвитку чи хронічна хвороба, меню узгоджують з лікарем."
        )
    return ""


def guide(key: str = "start") -> tuple[str, M]:
    key = key if key in GUIDE_TITLES else "start"
    idx = GUIDE_ORDER.index(key)
    text = _guide_text(key)
    text += f"\n\n<i>Сторінка {idx + 1} з {len(GUIDE_ORDER)}</i>"

    nav = []
    if idx > 0:
        nav.append(B("‹ Назад", callback_data=f"f:guide:{GUIDE_ORDER[idx - 1]}"))
    if idx < len(GUIDE_ORDER) - 1:
        nav.append(B("Далі ›", callback_data=f"f:guide:{GUIDE_ORDER[idx + 1]}"))
    rows = [nav] if nav else []
    rows += _rows([B(("• " if k == key else "") + GUIDE_TITLES[k], callback_data=f"f:guide:{k}")
                   for k in GUIDE_ORDER if k != key], 2)
    rows.append([B("💬 Написати автору", callback_data="f:fb")])
    rows.append([B("🏠 Головна", callback_data="f:home")])
    return text, M(rows)


# ─────────────────────── вечірній підсумок ───────────────────────


def evening_digest(child, ask: list[tuple[int, str]], mastered: list[str],
                   show_record: bool, can_next: bool) -> tuple[str, M]:
    """Одна картка на родину за добу: перша подача, правило трьох днів, тихий день.

    Свідомо коротка: її читають увечері з телефона, і вона має закриватися
    одним тапом або взагалі не вимагати дії.
    """
    parts = [f"<h3>🌙 {esc(child['name'])}: підсумок дня</h3>"]
    if ask:
        names = ", ".join(catalog.label(c) for _, c in ask)
        parts += [f"<h4>🆕 Уперше сьогодні: {names}</h4>",
                  "<p>Як пройшло? Висип, червоні щоки, живіт, стілець - якщо щось "
                  "впало в око, познач.</p>"]
    if mastered:
        names = ", ".join(catalog.label(c) for c in mastered)
        parts.append(f"<p>✅ Три дні без реакцій, рахую освоєними: {names}.</p>")
        if can_next:
            parts.append("<p>Карантин знято - можна вводити новий продукт.</p>")
    if show_record:
        parts.append("<p>📭 За сьогодні записів немає. Якщо їли - додай, поки памʼятаєш. "
                     "Якщо був день на молоці, просто пропусти.</p>")

    rows: list[list[B]] = []
    for nid, code in ask:
        p = catalog.product(code)
        name = p["name"] if p else code
        rows.append([B(f"✅ {name} - добре", callback_data=f"f:nudge:{nid}:ok", style="success"),
                     B("⚠️ Була реакція", callback_data=f"f:nudge:{nid}:react", style="danger")])
    for code in mastered:
        p = catalog.product(code)
        name = p["name"] if p else code
        rows.append([B(f"⚠️ {name} - все ж була реакція", callback_data=f"f:react:{code}")])
    if can_next and not ask:
        rows.append([B("➕ Що вводити далі", callback_data="f:next", style="primary")])
    if show_record:
        rows.append([B("🍽 Записати", callback_data="f:new", style="success")])
    rows.append([B("🔕 Не нагадувати", callback_data="f:rem")])
    return Rich("".join(parts)), M(rows)
