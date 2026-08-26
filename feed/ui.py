"""Екрани прикорму: текст + інлайн-клавіатури. Чистий рендер, без побічних ефектів."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton as B
from telegram import InlineKeyboardMarkup as M

from . import catalog, db, growth, suggest

TZ = ZoneInfo("Europe/Kyiv")



def esc(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def local(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, TZ)


def day_bounds(offset: int = 0) -> tuple[int, int, date]:
    today = datetime.now(TZ).date() + timedelta(days=offset)
    start = datetime.combine(today, datetime.min.time(), TZ)
    return int(start.timestamp()), int((start + timedelta(days=1)).timestamp()), today


def birth_date(child) -> date:
    return date.fromisoformat(child["birth_date"])


def months_of(child) -> int:
    return growth.age_months(birth_date(child))


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

    lines = [
        f"👶 <b>{esc(child['name'])}</b>, {growth.age_text(birth_date(child))}",
        f"Етап: {catalog.band_title(catalog.band_for(months))}",
        "",
        f"Сьогодні: {len(meals)} прийом(и), {len(eaten_today)} продукт(и)",
        f"Введено всього: {len(intro)} з {len(catalog.products())}",
    ]
    if watch:
        lines.append("Під наглядом: " + ", ".join(catalog.label(c) for c in watch[:4]))

    stock = len(db.pantry_codes(child["family_id"]))
    fridge = f" · {stock}" if stock else ""
    kb = M([
        [B("🍽 Записати прийом їжі", callback_data="f:new")],
        [B(f"🧊 Холодильник{fridge}", callback_data="f:fr"), B("🍲 Тарілки", callback_data="f:plates")],
        [B("🥕 Продукти", callback_data="f:lib"), B("👨‍🍳 Рецепти", callback_data="f:rec")],
        [B("📔 Щоденник", callback_data="f:diary"), B("📈 Зростання", callback_data="f:grow")],
        [B("⚙️ Налаштування", callback_data="f:set")],
    ])
    return "\n".join(lines), kb


# ─────────────────────── конструктор тарілки ───────────────────────


def balance_hint(codes: list[str]) -> str:
    if not codes:
        return "Обери, що було в тарілці. Можна один продукт, можна пʼять."
    missing = suggest.missing_slots(codes)
    if not missing:
        return "Тарілка збалансована: є рослинне, вуглевод, білок і жир."
    return "Не вистачає: " + ", ".join(suggest.SLOT_LABEL[s] for s in missing)


def draft(child, codes: list[str], intro: dict, group: str | None = None) -> tuple[str, M]:
    months = months_of(child)
    head = ["🍽 <b>Нова тарілка</b>", ""]
    if codes:
        head.append("У тарілці: " + ", ".join(catalog.label(c) for c in codes))
    head.append(balance_hint(codes))

    rows: list[list[B]] = []
    if group == "fav":
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
        items = sorted(catalog.by_key(group), key=lambda p: (p["from_month"], p["name"]))
        rows += _rows([
            B(("✔️ " if p["code"] in codes else mark(p["code"], intro, months) + " ")
              + catalog.name(p["code"]),
              callback_data=f"f:sel:{p['code']}")
            for p in items
        ])

    nav = [B("⭐ Часте", callback_data="f:grp:fav")]
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


def pick_kind(codes: list[str]) -> tuple[str, M]:
    now_h = datetime.now(TZ).hour
    guess = "breakfast" if now_h < 11 else "lunch" if now_h < 15 else "dinner" if now_h < 21 else "snack"
    text = ("Який це прийом їжі?\n\nУ тарілці: "
            + ", ".join(catalog.label(c) for c in codes))
    rows = _rows([B(("• " if k == guess else "") + name, callback_data=f"f:save:{k}")
                  for k, name in catalog.MEAL_KINDS])
    rows.append([B("‹ Назад", callback_data="f:new_keep")])
    return text, M(rows)


def meal_record(child, codes: list[str], kind: str, ts: int | None = None) -> str:
    """Постійний запис у стрічці чату. Без кнопок - його ніщо не перезапише."""
    ts = ts or db.now()
    day_from, day_to, _ = day_bounds()
    todays = db.meals_between(child["id"], day_from, day_to)
    line = " · ".join(catalog.label(c) for c in codes)
    head = f"✅ <b>{catalog.MEAL_LABEL[kind]}</b> · {local(ts).strftime('%H:%M')}"
    tail = f"\n\n<i>Сьогодні це {len(todays)}-й прийом їжі.</i>" if len(todays) > 1 else ""
    return f"{head}\n{line}{tail}"


def after_save(child, intro: dict, fresh: list[str]) -> tuple[str, M]:
    """Окреме повідомлення після запису: попередження і навігація."""
    if not fresh:
        return home(child, intro)
    lines = ["🆕 <b>Нові продукти</b>: " + ", ".join(catalog.label(c) for c in fresh),
             "",
             "Правило трьох днів: тримаємо новий продукт у меню 2-3 дні поспіль "
             "і не вводимо інший новий, щоб реакцію було видно однозначно.",
             "Нагадаю ввечері спитати, як пройшло."]
    for c in fresh:
        prod = catalog.product(c)
        if prod and prod.get("allergen"):
            lines.append(f"⚠️ {prod['emoji']} {prod['name']} - алерген ({prod['allergen']}). "
                         "Наступні рази давати регулярно, не відкладати.")
    rows = _rows([B(f"⚠️ Реакція: {catalog.label(c)}",
                    callback_data=f"f:react:{c}") for c in fresh], 1)
    rows.append([B("📔 Щоденник", callback_data="f:diary"), B("🏠 Головна", callback_data="f:home")])
    return "\n".join(lines), M(rows)


# ─────────────────────────── бібліотека ───────────────────────────


def library(child, intro: dict) -> tuple[str, M]:
    months = months_of(child)
    lines = ["🥕 <b>Продукти</b>",
             f"Введено {len(intro)} з {len(catalog.products())}.",
             "",
             "✅ введено · 🆕 ще ні · 👀 під наглядом · ⛔ уникаємо · ⏳ зарано за віком",
             f"{catalog.ALLERGEN_MARK} алерген: вводити рано і потім регулярно",
             "",
             "<i>Мʼясо, риба, яйця і бобові лежать на різних полицях, але для балансу "
             "тарілки це один білок.</i>"]
    rows = []
    for key in catalog.nav_keys():
        emoji, title = catalog.key_title(key)
        items = catalog.by_key(key)
        done = sum(1 for p in items if p["code"] in intro)
        rows.append([B(f"{emoji} {title} · {done}/{len(items)}", callback_data=f"f:lg:{key}")])
    rows.append([B("🏠 Головна", callback_data="f:home")])
    return "\n".join(lines), M(rows)


def group_list(child, group: str, intro: dict) -> tuple[str, M]:
    months = months_of(child)
    group = catalog.default_key(group)
    emoji, title = catalog.key_title(group)
    items = sorted(catalog.by_key(group), key=lambda p: (p["from_month"], p["name"]))
    lines = [f"{emoji} <b>{title}</b>",
             f"Вік {child['name']}: {growth.age_text(birth_date(child))} · позицій {len(items)}"]
    rows = sub_tabs(group, "f:lg")
    rows += _rows([B(f"{mark(p['code'], intro, months)} {catalog.name(p['code'])}",
                     callback_data=f"f:p:{p['code']}") for p in items])
    rows.append([B("‹ Продукти", callback_data="f:lib"), B("🏠 Головна", callback_data="f:home")])
    return "\n".join(lines), M(rows)


def card(child, code: str, intro: dict) -> tuple[str, M]:
    p = catalog.product(code)
    months = months_of(child)
    band = catalog.band_for(months)
    row = intro.get(code)

    head = [f"{p['emoji']} <b>{esc(p['name'])}</b>{catalog.mark_allergen(code)}"]
    meta = [f"з {p['from_month']} міс", f"ризик подавитися: {catalog.CHOKING[p['choking']]}"]
    if p.get("allergen"):
        meta.append(f"⚠️ алерген: {p['allergen']}")
    head.append(" · ".join(meta))
    if p["from_month"] > months:
        head.append(f"⏳ Для {esc(child['name'])} ще зарано - рекомендовано з {p['from_month']} місяців.")
    head.append("")

    head.append("<b>Як подавати зараз</b>")
    head.append(esc(p["serve"][band]))
    head.append("")
    for key, lo, hi, title in catalog.AGE_BANDS:
        if key != band:
            head.append(f"<i>{title}:</i> {esc(p['serve'][key])}")
    head.append("")
    if p.get("benefit"):
        head.append(f"<b>Користь.</b> {esc(p['benefit'])}")
    if p.get("caution"):
        head.append(f"<b>⚠️ Обережно.</b> {esc(p['caution'])}")

    if row:
        emoji_s, label_s = catalog.STATUS[row["status"]]
        head.append("")
        head.append(f"{emoji_s} Статус: {label_s} · перший раз "
                    f"{local(row['first_ts']).strftime('%d.%m.%Y')} · разів: {row['times']}")
    rx = [r for r in db.reactions(child["id"], 50) if r["code"] == code]
    if rx:
        head.append("Реакції: " + ", ".join(
            f"{catalog.REACTION_LABEL.get(r['kind'], r['kind'])} ({local(r['ts']).strftime('%d.%m')})"
            for r in rx[:3]))

    rows = [
        [B("🍽 Подали зараз", callback_data=f"f:quick:{code}")],
        [B("⚠️ Реакція", callback_data=f"f:react:{code}")],
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
    return "\n".join(head), M(rows)


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
    rows.append([B("🏠 Головна", callback_data="f:home")])
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
        lines.append(f"   {esc(catalog.serving(code, months))}")
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
        lines.append(f"   {esc(catalog.serving(code, months))}")
    if p.get("note"):
        lines += ["", f"<i>{esc(p['note'])}</i>"]
    rows = [[B("🍽 Подали цю тарілку", callback_data=f"f:plu:{pid}")],
            [B("‹ Тарілки", callback_data=f"f:plates:{p['age']}"), B("🏠 Головна", callback_data="f:home")]]
    return "\n".join(lines), M(rows)


def recipes_screen(child) -> tuple[str, M]:
    months = months_of(child)
    items = catalog.recipes_for(months)
    lines = [f"👨‍🍳 <b>Рецепти</b>", f"Доступно за віком: {len(items)} з {len(catalog.recipes())}", ""]
    rows = [[B(f"{r['emoji']} {r['title']}", callback_data=f"f:r:{r['id']}")] for r in items]
    rows.append([B("🏠 Головна", callback_data="f:home")])
    return "\n".join(lines), M(rows)


def recipe_card(child, rid: str) -> tuple[str, M]:
    r = catalog.recipe(rid)
    lines = [f"{r['emoji']} <b>{esc(r['title'])}</b>",
             f"з {r['age_from']} міс · {esc(r['time'])}", "",
             "<b>Інгредієнти</b>"]
    lines += [f"· {esc(i)}" for i in r["ingredients"]]
    lines += ["", "<b>Як готувати</b>"]
    lines += [f"{n}. {esc(s)}" for n, s in enumerate(r["steps"], 1)]
    lines += ["", f"<b>💡 Порада.</b> {esc(r['tips'])}", f"<b>❄️ Заморозка.</b> {esc(r['freeze'])}"]
    rows = [[B("🍽 Подали цю страву", callback_data=f"f:ru:{rid}")],
            [B("‹ Рецепти", callback_data="f:rec"), B("🏠 Головна", callback_data="f:home")]]
    return "\n".join(lines), M(rows)


# ─────────────────────────── щоденник ───────────────────────────


def diary(child, offset: int = 0) -> tuple[str, M]:
    ts_from, ts_to, day = day_bounds(offset)
    meals = db.meals_between(child["id"], ts_from, ts_to)
    label = "Сьогодні" if offset == 0 else "Вчора" if offset == -1 else day.strftime("%d.%m.%Y")
    lines = [f"📔 <b>Щоденник · {label}</b>", ""]
    if not meals:
        lines.append("Записів немає.")
    rows: list[list[B]] = []
    for m in meals:
        codes = db.meal_codes(m["id"])
        lines.append(f"{local(m['ts']).strftime('%H:%M')} · {catalog.MEAL_LABEL.get(m['kind'], m['kind'])}")
        lines.append("   " + ", ".join(catalog.label(c) for c in codes))
        if m["note"]:
            lines.append(f"   <i>{esc(m['note'])}</i>")
        rows.append([B(f"🗑 {local(m['ts']).strftime('%H:%M')}", callback_data=f"f:mdel:{m['id']}"),
                     B(f"📝 нотатка", callback_data=f"f:mnote:{m['id']}")])
    nav = [B("‹ Раніше", callback_data=f"f:diary:{offset - 1}")]
    if offset < 0:
        nav.append(B("Пізніше ›", callback_data=f"f:diary:{offset + 1}"))
    rows.append(nav)
    rows.append([B("⚠️ Реакції", callback_data="f:rxlog"), B("📊 Зведення", callback_data="f:stats")])
    rows.append([B("🏠 Головна", callback_data="f:home")])
    return "\n".join(lines), M(rows)


def stats(child, intro: dict) -> tuple[str, M]:
    months = months_of(child)
    total = len(catalog.products())
    by_group_done = []
    for g in catalog.GROUP_ORDER:
        items = catalog.by_group(g)
        done = sum(1 for p in items if p["code"] in intro)
        bar = "▰" * round(8 * done / len(items)) + "▱" * (8 - round(8 * done / len(items)))
        by_group_done.append(f"{catalog.GROUPS[g][0]} {bar} {done}/{len(items)} {catalog.GROUPS[g][1]}")

    ts_from, _, _ = day_bounds(-6)
    _, ts_to, _ = day_bounds()
    week = db.meals_between(child["id"], ts_from, ts_to)
    week_codes = {c for m in week for c in db.meal_codes(m["id"])}
    allerg_intro = [c for c in intro if catalog.product(c) and catalog.product(c).get("allergen")]
    allerg_all = [c for c, p in catalog.products().items() if p.get("allergen")]

    lines = [f"📊 <b>Зведення</b>", "",
             f"Введено {len(intro)} з {total} продуктів", ""]
    lines += by_group_done
    lines += ["", f"За 7 днів: {len(week)} прийом(и), {len(week_codes)} різних продуктів",
              f"Алергени введено: {len(allerg_intro)} з {len(allerg_all)}"]
    missing = [c for c in allerg_all if c not in intro
               and catalog.product(c)["from_month"] <= months]
    if missing:
        lines.append("Ще не пробували: " + ", ".join(catalog.label(c) for c in missing))
        lines.append("<i>Алергени вводять рано і потім регулярно - відкладання підвищує ризик алергії.</i>")
    return "\n".join(lines), M([[B("‹ Щоденник", callback_data="f:diary"),
                                 B("🏠 Головна", callback_data="f:home")]])


# ─────────────────────────── зростання ───────────────────────────


def growth_screen(child) -> tuple[str, M]:
    days = growth.age_days(birth_date(child))
    sex = child["sex"]
    lines = [f"📈 <b>Зростання · {esc(child['name'])}</b>",
             f"{growth.age_text(birth_date(child))}", ""]
    w = db.last_measure(child["id"], "weight_kg")
    h = db.last_measure(child["id"], "height_cm")
    if w:
        d = growth.age_days(birth_date(child), local(w["ts"]).date())
        lines += [f"<b>Вага</b> · вимір {local(w['ts']).strftime('%d.%m.%Y')}",
                  growth.summary(sex, "wfa", d, w["weight_kg"], "кг"), ""]
    if h:
        d = growth.age_days(birth_date(child), local(h["ts"]).date())
        lines += [f"<b>Зріст</b> · вимір {local(h['ts']).strftime('%d.%m.%Y')}",
                  growth.summary(sex, "lhfa", d, h["height_cm"], "см"), ""]
    if not w and not h:
        lines.append("Ще немає вимірів. Додайте вагу і зріст - покажу перцентиль за ВООЗ.")

    hist = [m for m in db.measures(child["id"], 10)]
    if len(hist) > 1:
        lines.append("<b>Історія</b>")
        for m in hist:
            parts = []
            if m["weight_kg"]:
                parts.append(f"{m['weight_kg']:.2f} кг")
            if m["height_cm"]:
                parts.append(f"{m['height_cm']:.1f} см")
            lines.append(f"{local(m['ts']).strftime('%d.%m.%Y')} · " + ", ".join(parts))
    lines += ["", "<i>Перцентиль - це не оцінка. Стабільний коридор важливіший за саме число.</i>"]

    rows = [[B("⚖️ Додати вагу", callback_data="f:gw"), B("📏 Додати зріст", callback_data="f:gh")],
            [B("🏠 Головна", callback_data="f:home")]]
    return "\n".join(lines), M(rows)


# ─────────────────────────── налаштування ───────────────────────────


def settings(child, family_id: int, remind: bool) -> tuple[str, M]:
    members = db.family_members(family_id)
    lines = [f"⚙️ <b>Налаштування</b>", "",
             f"Дитина: <b>{esc(child['name'])}</b>, {esc(child['birth_date'])}, "
             f"{'дівчинка' if child['sex'] == 'girls' else 'хлопчик'}",
             f"Доступ мають: " + ", ".join(esc(m["name"]) for m in members),
             f"Нагадування: {'увімкнені' if remind else 'вимкнені'}",
             "",
             "<i>Бібліотека продуктів - довідка на основі рекомендацій ВООЗ, AAP і EFSA. "
             "Вона не замінює педіатра.</i>"]
    rows = [[B("➕ Додати дорослого", callback_data="f:inv")],
            [B(("🔕 Вимкнути" if remind else "🔔 Увімкнути") + " нагадування", callback_data="f:rem")],
            [B("✏️ Змінити дату народження", callback_data="f:editbirth")],
            [B("🏠 Головна", callback_data="f:home")]]
    return "\n".join(lines), M(rows)


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
        lines.append(f"Удома {len(stock)} продукт(и):")
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
    rows.append([B("➕ Додати", callback_data="f:fradd"), B("✍️ Списком", callback_data="f:frtext")])
    if stock:
        rows.append([B("➖ Прибрати", callback_data="f:frdel"),
                     B("🧹 Спорожнити", callback_data="f:frclear")])
    rows.append([B("🏠 Головна", callback_data="f:home")])
    return "\n".join(lines), M(rows)


def fridge_pick(child, key: str, stock: list[str], intro: dict) -> tuple[str, M]:
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
    items = sorted(catalog.by_key(key), key=lambda p: (p["from_month"], p["name"]))
    rows += _rows([B(("✔️ " if p["code"] in stock else mark(p["code"], intro, months) + " ")
                     + catalog.name(p["code"]),
                     callback_data=f"f:frt:{p['code']}") for p in items])
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
            lines += ["", f"<i>Нового в списку {len(fresh)} позицій - це багато для одного "
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
