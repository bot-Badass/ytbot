"""Контент прикорму: продукти, тарілки, рецепти. Читається з data/*.yaml один раз."""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

GROUPS: dict[str, tuple[str, str]] = {
    "veg": ("🥦", "Овочі"),
    "fruit": ("🍎", "Фрукти та ягоди"),
    "grain": ("🥣", "Крупи та злаки"),
    "protein": ("🍖", "Білок"),
    "dairy": ("🥛", "Молочні"),
    "fat": ("🥑", "Жири"),
    "other": ("💧", "Інше"),
}

GROUP_ORDER = list(GROUPS)

# Мʼясо, риба, яйця і бобові живуть окремими полицями, бо шукати індичку серед
# сорока позицій незручно. Для балансу тарілки це і далі ОДИН слот білка:
# group у продукті лишається protein, ділить тільки поле sub.
SUBGROUPS: dict[str, tuple[str, str]] = {
    "meat": ("🍖", "Мʼясо"),
    "fish": ("🐟", "Риба і морепродукти"),
    "egg": ("🥚", "Яйця"),
    "legume": ("🫘", "Бобові"),
}

SUBS_OF: dict[str, tuple[str, ...]] = {"protein": ("meat", "fish", "egg", "legume")}

ALLERGEN_MARK = "❗"

CHOKING: dict[str, str] = {
    "low": "низький",
    "med": "середній",
    "high": "високий",
}

AGE_BANDS = (
    ("m6", 6, 8, "6-8 міс"),
    ("m9", 9, 11, "9-11 міс"),
    ("m12", 12, 999, "12 міс+"),
)

STATUS = {
    "trying": ("🆕", "щойно ввели"),
    "ok": ("✅", "освоєно"),
    "watch": ("👀", "під наглядом"),
    "avoid": ("⛔", "уникаємо"),
}

REACTIONS = [
    ("rash", "🔴 Висип на тілі"),
    ("cheeks", "🟠 Червоні щоки"),
    ("mouth", "😮 Почервоніння навколо рота"),
    ("vomit", "🤮 Блювання"),
    ("diarrhea", "💩 Рідкий стілець"),
    ("constip", "🚧 Закреп"),
    ("gas", "🌀 Гази, кольки"),
    ("refuse", "🙅 Відмовився їсти"),
    ("breath", "🚨 Набряк, утруднене дихання"),
]

REACTION_LABEL = dict(REACTIONS)

MEAL_KINDS = [
    ("breakfast", "🌅 Сніданок"),
    ("lunch", "🍽 Обід"),
    ("dinner", "🌙 Вечеря"),
    ("snack", "🍏 Перекус"),
]

MEAL_LABEL = dict(MEAL_KINDS)


def _load(name: str) -> Any:
    return yaml.safe_load((DATA_DIR / name).read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def products() -> dict[str, dict]:
    items = _load("products.yaml")["products"]
    return {p["code"]: p for p in items}


@functools.lru_cache(maxsize=1)
def plates() -> list[dict]:
    return _load("plates.yaml")["plates"]


@functools.lru_cache(maxsize=1)
def recipes() -> list[dict]:
    return _load("recipes.yaml")["recipes"]


def product(code: str) -> dict | None:
    return products().get(code)


# ─────────────────────── групи і полиці ───────────────────────


def split_key(key: str) -> tuple[str, str | None]:
    """«protein.fish» -> («protein», «fish»), «veg» -> («veg», None)."""
    group, _, sub = key.partition(".")
    return group, (sub or None)


def key_of(code: str) -> str:
    p = product(code)
    if not p:
        return "veg"
    return f"{p['group']}.{p['sub']}" if p.get("sub") else p["group"]


def nav_keys() -> list[str]:
    """Порядок полиць для меню: білок розкладений на мʼясо/рибу/яйця/бобові."""
    keys: list[str] = []
    for g in GROUP_ORDER:
        subs = SUBS_OF.get(g)
        keys += [f"{g}.{s}" for s in subs] if subs else [g]
    return keys


def default_key(key: str) -> str:
    """«protein» -> «protein.meat»: група з полицями завжди відкривається на першій."""
    group, sub = split_key(key)
    subs = SUBS_OF.get(group)
    return f"{group}.{subs[0]}" if subs and not sub else key


def key_title(key: str) -> tuple[str, str]:
    group, sub = split_key(key)
    if sub:
        emoji, title = SUBGROUPS[sub]
        return emoji, f"{GROUPS[group][1]}: {title.lower()}"
    return GROUPS[group]


def by_group(group: str, sub: str | None = None, rare: bool = True) -> list[dict]:
    return [p for p in products().values()
            if p["group"] == group and (sub is None or p.get("sub") == sub)
            and (rare or not p.get("rare"))]


def by_key(key: str, rare: bool = True) -> list[dict]:
    group, sub = split_key(key)
    return by_group(group, sub, rare)


def is_rare(code: str) -> bool:
    """Екзотика: лежить у бібліотеці, але за замовчуванням схована.

    Кейл, мангольд, топінамбур і решта того, чого немає в звичайному магазині.
    Генератор тарілок і список покупок їх не пропонують, але якщо такий продукт
    таки лежить у холодильнику - з ним усе працює як зі звичайним.
    """
    p = product(code)
    return bool(p and p.get("rare"))


def rare_count(key: str) -> int:
    return sum(1 for p in by_key(key) if p.get("rare"))


def band_for(months: int) -> str:
    for key, lo, hi, _ in AGE_BANDS:
        if lo <= months <= hi:
            return key
    return "m6" if months < 6 else "m12"


def band_title(band: str) -> str:
    for key, _, _, title in AGE_BANDS:
        if key == band:
            return title
    return band


def serving(code: str, months: int) -> str:
    p = product(code)
    if not p:
        return ""
    return p["serve"].get(band_for(months), "")


def plates_for(band: str) -> list[dict]:
    return [p for p in plates() if p["age"] == band]


def recipes_for(months: int) -> list[dict]:
    return [r for r in recipes() if r["age_from"] <= max(months, 6)]


def recipes_with(codes: list[str]) -> list[dict]:
    """Рецепти, у яких є хоч один із названих продуктів. Спершу ті, де їх більше."""
    wanted = set(codes)
    hits = [(len(wanted & set(r["products"])), r) for r in recipes()]
    return [r for n, r in sorted(hits, key=lambda x: -x[0]) if n]


def recipe_missing(rid: str, stock: list[str]) -> tuple[list[str], list[str]]:
    """(що з рецепта вже вдома, чого бракує) за вмістом холодильника."""
    r = recipe(rid)
    if not r:
        return [], []
    have = set(stock)
    return ([c for c in r["products"] if c in have],
            [c for c in r["products"] if c not in have])


def recipe(rid: str) -> dict | None:
    return next((r for r in recipes() if r["id"] == rid), None)


def plate(pid: str) -> dict | None:
    return next((p for p in plates() if p["id"] == pid), None)


def allergens() -> list[str]:
    seen: list[str] = []
    for p in products().values():
        a = p.get("allergen")
        if a and a not in seen:
            seen.append(a)
    return seen


def allergen_codes() -> list[str]:
    return [c for c, p in products().items() if p.get("allergen")]


def mark_allergen(code: str) -> str:
    p = product(code)
    return ALLERGEN_MARK if p and p.get("allergen") else ""


def name(code: str) -> str:
    """Назва без емодзі, з позначкою алергену."""
    p = product(code)
    return f"{p['name']}{mark_allergen(code)}" if p else code


def label(code: str) -> str:
    p = product(code)
    return f"{p['emoji']} {p['name']}{mark_allergen(code)}" if p else code
