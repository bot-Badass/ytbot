"""Пошук по прикорму звичайним текстом: «курка», «гарбузовий суп», «морква і рис».

Один вхід на три різні наміри, бо людина не зобовʼязана знати, що з цього продукт,
а що страва. Спершу шукаємо рецепт за назвою, далі продукти в тексті, і вже за
знайденими продуктами добираємо решту рецептів.
"""
from __future__ import annotations

import re

from . import catalog, suggest

# службові слова, які самі по собі нічого не шукають
STOP = {"що", "як", "для", "мене", "нас", "вже", "хочу", "можна", "треба", "дитині",
        "приготувати", "зварити", "зробити", "рецепт", "рецепти", "страва", "їсти"}

MAX_RECIPES = 8


def _words(text: str) -> list[str]:
    out = []
    for raw in re.split(r"[\s,;.!?()\-/]+", text.lower()):
        if len(raw) < 3 or raw in STOP:
            continue
        word = suggest.stem(raw)
        if word and word not in out:
            out.append(word)
    return out


def recipes_by_name(text: str, limit: int = MAX_RECIPES) -> list[dict]:
    """Рецепти, назва яких збігається зі словами запиту. Більше збігів - вище."""
    words = _words(text)
    if not words:
        return []
    # вага збігу це довжина слова: «гарбузов» точніше вказує на страву, ніж «суп»,
    # тому гарбузовий суп має обійти суп-пюре з сочевиці
    scored: list[tuple[int, dict]] = []
    for r in catalog.recipes():
        title = [suggest.stem(w) for w in re.split(r"[\s,;.()\-/]+", r["title"]) if len(w) > 2]
        score = sum(len(w) for w in words
                    if any(t.startswith(w) or w.startswith(t) for t in title if t))
        if score:
            scored.append((score, r))
    scored.sort(key=lambda x: -x[0])
    return [r for _, r in scored][:limit]


def find(text: str) -> dict:
    """Що знайшлось за довільним текстом.

    Повертає {"codes": [коди продуктів], "recipes": [рецепти], "named": bool}.
    named=True означає, що рецепт знайшовся саме за назвою страви - тоді його
    показуємо першим, а не ховаємо під добір тарілки.
    """
    named = recipes_by_name(text)
    codes = suggest.parse_products(text, limit=4)
    by_product = catalog.recipes_with(codes) if codes else []

    merged: list[dict] = []
    for r in named + by_product:
        if r not in merged:
            merged.append(r)
    return {"codes": codes, "recipes": merged[:MAX_RECIPES], "named": bool(named)}
