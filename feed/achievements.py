"""Досягнення дитини: значки з того, що родина вже записує в щоденник.

Рахуються після кожного натискання (sync), нове одразу приходить тому, хто натиснув.
Старим родинам усе зароблене до запуску нараховано мовчки (backfill), інакше 21.10
кожен отримав би пачку повідомлень за раз. Репостів не просимо: на картці є назва бота.
"""
from __future__ import annotations

from datetime import timedelta

from . import catalog, db, suggest, ui

# (ключ, емодзі, назва, пороги рівнів). Один поріг = разове досягнення.
ACH = (
    ("start", "🍽", "Перший запис", (1,)),
    ("taste", "🥕", "Дослідник смаку", (5, 10, 25, 50, 100)),
    ("brave", "❗", "Сміливець", (1, 3, 6, 9)),
    ("streak", "🔥", "Серія", (3, 7, 14, 30)),
    ("diary", "📔", "Літописець", (10, 50, 100, 300)),
    ("plate", "⚖️", "Збалансована тарілка", (1,)),
    ("variety", "🌈", "Різноманіття", (15,)),
    ("team", "🤝", "Командна гра", (2,)),
)
BY_KEY = {a[0]: a for a in ACH}

# Основні алергени, як на картці «Сміливець»: група -> ключі з поля allergen
ALLERGEN_GROUPS = {
    "Молоко": {"молоко"}, "Яйце": {"яйце"}, "Риба": {"риба"},
    "Глютен": {"пшениця", "глютен", "жито", "ячмінь"}, "Арахіс": {"арахіс"},
    "Горіхи": {"горіхи деревні"}, "Кунжут": {"кунжут"}, "Селера": {"селера"},
    "Морепродукти": {"ракоподібні", "молюски"},
}


def level_text(key: str, n: int) -> str:
    return {
        "start": "перший прийом їжі в щоденнику",
        "taste": ui.plural(n, "продукт", "продукти", "продуктів"),
        "brave": "перший алерген без реакції" if n == 1 else f"{n} з 9 алергенів без реакції",
        "streak": ui.plural(n, "день", "дні", "днів") + " поспіль із записами",
        "diary": ui.plural(n, "прийом", "прийоми", "прийомів") + " у щоденнику",
        "plate": "овочі, вуглевод, білок і жир в одній тарілці",
        "variety": f"{n} різних продуктів за тиждень",
        "team": "обидва дорослі ведуть щоденник",
    }[key]


def allergens_done(intro: dict) -> list[str]:
    """Групи алергенів, введені без реакції. «Сліди» (масло, гхі) не рахуються: це не
    знайомство з білком молока. Будь-що з групи «не підходить» - групу не зараховуємо."""
    ok, bad = set(), set()
    for code, row in intro.items():
        raw = (catalog.product(code) or {}).get("allergen") or ""
        keys = {p.split("(")[0].strip() for p in raw.split(",") if "сліди" not in p}
        if row["status"] == "ok":
            ok |= keys
        elif row["status"] in ("avoid", "watch"):
            bad |= suggest.allergen_keys(code)
    return [g for g, keys in ALLERGEN_GROUPS.items() if keys & ok and not keys & bad]


def metrics(child) -> dict[str, int]:
    """Поточні значення для всіх досягнень."""
    cid = child["id"]
    intro = db.intro_map(cid)
    meals = db.q("SELECT id, ts, by_user FROM meal WHERE child_id=? ORDER BY ts", (cid,))
    items: dict[int, list[str]] = {}
    for r in db.q("SELECT mi.meal_id, mi.code FROM meal_item mi JOIN meal m ON m.id=mi.meal_id "
                  "WHERE m.child_id=?", (cid,)):
        items.setdefault(r["meal_id"], []).append(r["code"])

    days = {ui.local(m["ts"]).date() for m in meals}
    today = ui.day_bounds()[2]
    streak, d = 0, today if today in days else today - timedelta(days=1)
    while d in days:
        streak, d = streak + 1, d - timedelta(days=1)

    week_from = ui.day_bounds(-6)[0]
    week = {c for m in meals if m["ts"] >= week_from for c in items.get(m["id"], [])}
    return {
        "start": min(len(meals), 1),
        "taste": sum(1 for r in intro.values() if r["status"] != "avoid"),
        "brave": len(allergens_done(intro)),
        "streak": streak,
        "diary": len(meals),
        "plate": int(any(not suggest.missing_slots(c) for c in items.values())),
        "variety": len(week),
        "team": len({m["by_user"] for m in meals}),
    }


def earned(child_id: int) -> dict[str, list[tuple[int, int]]]:
    """key -> [(рівень, ts)] за зростанням рівня."""
    out: dict[str, list[tuple[int, int]]] = {}
    for r in db.q("SELECT key, level, ts FROM achievement WHERE child_id=? ORDER BY level",
                  (child_id,)):
        out.setdefault(r["key"], []).append((r["level"], r["ts"]))
    return out


def sync(child) -> list[tuple[str, int]]:
    """Записує нові рівні і повертає їх [(key, level)]. Рівень не забирається, навіть
    якщо метрика впала (серія перервалась, запис видалили): зароблене лишається."""
    have = earned(child["id"])
    now = db.now()
    new = []
    for key, value in metrics(child).items():
        got = {lvl for lvl, _ in have.get(key, [])}
        for lvl in BY_KEY[key][3]:
            if value >= lvl and lvl not in got:
                db.run("INSERT OR IGNORE INTO achievement(child_id, key, level, ts) VALUES(?,?,?,?)",
                       (child["id"], key, lvl, now))
                new.append((key, lvl))
    return new


def top(child_id: int) -> list[tuple[str, int]]:
    """Найвищий рівень кожного досягнення, свіжі першими: для рядка значків на головній."""
    rows = [(key, lv[-1][0], lv[-1][1]) for key, lv in earned(child_id).items()]
    return [(k, lvl) for k, lvl, _ in sorted(rows, key=lambda r: -r[2])]


def badge(key: str, lvl: int) -> str:
    emoji = BY_KEY[key][1]
    return f"{emoji}{lvl}" if len(BY_KEY[key][3]) > 1 else emoji


def backfill() -> int:
    """Разово перед запуском: зароблене до сьогодні - мовчки, без повідомлень."""
    n = 0
    for child in db.q("SELECT * FROM child"):
        n += len(sync(child))
    return n
