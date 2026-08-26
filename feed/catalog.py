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


def by_group(group: str) -> list[dict]:
    return [p for p in products().values() if p["group"] == group]


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


def label(code: str) -> str:
    p = product(code)
    return f"{p['emoji']} {p['name']}" if p else code
