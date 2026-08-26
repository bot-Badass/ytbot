"""Генератор тарілок: «накидай варіантів» і «у мене є морква і курка, що додати».

Одна логіка на два питання. Тарілка вважається зібраною, коли закриті чотири
слоти: рослинне, вуглевод, білок, жир. Генератор добирає продукти, дозволені за
віком, не позначені «уникаємо», і сумісні за стилем страви (щоб лосось не
опинився в одній тарілці з грушею).
"""
from __future__ import annotations

import random
import re
import unicodedata

from . import catalog

SLOTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("plant", "рослинне", ("veg", "fruit")),
    ("grain", "вуглевод", ("grain",)),
    ("protein", "білок", ("protein", "dairy")),
    ("fat", "жир", ("fat",)),
)

SLOT_LABEL = {key: label for key, label, _ in SLOTS}
SLOT_GROUPS = {key: groups for key, _, groups in SLOTS}

MODES = {"main": "обід і вечеря", "breakfast": "сніданок і перекус"}

# приправа, а не порція - у згенерованій тарілці їй не місце
NEVER = {"tsybulia"}

# крохмалисті овочі закривають слот вуглеводу, а не рослинного:
# інакше генератор видає картоплю з макаронами
STARCHY = {"kartoplia", "batat"}

# слова, за якими продукт не впізнати з назви
ALIASES: dict[str, tuple[str, ...]] = {
    "kurka": ("курча", "куряче", "куряча", "філе"),
    "yalovychyna": ("телятина", "говядина", "яловичина"),
    "svynyna": ("свинка",),
    "yaitse": ("яйця", "яєчня", "омлет", "жовток", "білок"),
    "kartoplia": ("картошка", "картопелька"),
    "grechka": ("гречана",),
    "vivsianka": ("вівсяні", "геркулес", "овсянка"),
    "oliia": ("оливкова", "соняшникова", "олива"),
    "maslo": ("вершкове",),
    "syr_kyslo": ("творог", "сирок"),
    "syr_tverdyi": ("сир твердий",),
    "yogurt": ("йогурт",),
    "makarony": ("паста", "спагеті", "локшина"),
    "khlib": ("тост", "хлібець"),
    "kvasolia_struch": ("стручкова",),
    "sochevytsia": ("чечевиця",),
    "pechinka": ("печінка",),
    "triska": ("хек", "мінтай", "риба"),
    "losos": ("сьомга", "форель"),
    "tsvitna": ("цвітна",),
    "brokoli": ("броколі", "брокколі"),
    "garbuz": ("тиква",),
    "buriak": ("свекла",),
    "kabachok": ("цукіні",),
    "goroshok": ("горошок", "горох"),
    "gorikhy": ("горіх", "волоський", "мигдаль"),
    "arakhis": ("арахіс",),
    "tahini": ("кунжут",),
    "polunytsia": ("клубніка",),
    "chornytsia": ("голубіка",),
}


def _norm(word: str) -> str:
    word = unicodedata.normalize("NFKC", word).lower().replace("ʼ", "'").replace("’", "'")
    return re.sub(r"[^а-яїієґa-z']", "", word)


def _stem(word: str) -> str:
    """Груба основа слова: обрізаємо українські закінчення."""
    word = _norm(word)
    for end in ("ами", "ями", "ові", "ею", "ою", "ів", "ам", "ах", "ий", "ої", "ою",
                "ку", "ці", "ка", "ки", "ко", "ом", "ем", "у", "ю", "и", "і", "а", "я", "е", "о"):
        if len(word) - len(end) >= 3 and word.endswith(end):
            return word[: -len(end)]
    return word


def _keys(code: str) -> set[str]:
    product = catalog.product(code)
    words = [w for w in re.split(r"[\s(),]+", product["name"]) if len(w) > 2]
    words += list(ALIASES.get(code, ()))
    return {_stem(w) for w in words if _stem(w)}


def parse_products(text: str, limit: int = 6) -> list[str]:
    """Витягує коди продуктів з довільного тексту на кшталт «морква і курка»."""
    index: list[tuple[str, set[str]]] = [(code, _keys(code)) for code in catalog.products()]
    found: list[str] = []
    for raw in re.split(r"[\s,;.+/&]+|\bі\b|\bта\b|\bи\b", text):
        token = _stem(raw)
        if len(token) < 3:
            continue
        best = None
        for code, keys in index:
            for key in keys:
                if token == key or (len(token) >= 4 and (key.startswith(token) or token.startswith(key))):
                    best = code
                    break
            if best:
                break
        if best and best not in found:
            found.append(best)
        if len(found) >= limit:
            break
    return found


def slot_of(code: str) -> str | None:
    group = catalog.product(code)["group"]
    for key, _, groups in SLOTS:
        if group in groups:
            return key
    return None


def missing_slots(codes: list[str]) -> list[str]:
    filled = {slot_of(c) for c in codes}
    return [key for key, _, _ in SLOTS if key not in filled]


def mode_of(codes: list[str]) -> str:
    """Визначає, чи це основна страва, чи сніданок, за тим, що вже в тарілці."""
    styles = {catalog.product(c)["style"] for c in codes if catalog.product(c)}
    if "main" in styles:
        return "main"
    if "breakfast" in styles:
        return "breakfast"
    return random.choice(["main", "breakfast"])


def candidates(months: int, intro: dict, slot: str, mode: str,
               exclude: set[str] = frozenset()) -> list[str]:
    out = []
    for code, product in catalog.products().items():
        if code in exclude or product["group"] == "other":
            continue
        if code in NEVER:
            continue
        if code in STARCHY:
            if slot != "grain":
                continue
        elif product["group"] not in SLOT_GROUPS[slot]:
            continue
        if product["from_month"] > months:
            continue
        if product["style"] not in (mode, "both"):
            continue
        row = intro.get(code)
        if row and row["status"] == "avoid":
            continue
        out.append(code)
    return out


def _weight(code: str, intro: dict) -> float:
    """Знайоме частіше за нове: у тарілці має бути максимум один новий продукт."""
    row = intro.get(code)
    if not row:
        return 1.0
    if row["status"] == "watch":
        return 0.4
    return 3.0


def complete(months: int, intro: dict, have: list[str], count: int = 3) -> list[list[str]]:
    """Добирає до наявних продуктів те, чого бракує. Повертає готові тарілки."""
    mode = mode_of(have)
    need = missing_slots(have)
    if not need:
        return [list(have)]
    seen: set[tuple[str, ...]] = set()
    plates: list[list[str]] = []
    for _ in range(count * 40):
        if len(plates) >= count:
            break
        plate = list(have)
        fresh = sum(1 for c in plate if c not in intro)
        ok = True
        for slot in need:
            pool = candidates(months, intro, slot, mode, exclude=set(plate))
            if fresh >= 1:
                known = [c for c in pool if c in intro]
                pool = known or pool
            if not pool:
                ok = False
                break
            pick = random.choices(pool, weights=[_weight(c, intro) for c in pool])[0]
            if pick not in intro:
                fresh += 1
            plate.append(pick)
        key = tuple(sorted(plate))
        if ok and key not in seen:
            seen.add(key)
            plates.append(plate)
    return plates


def generate(months: int, intro: dict, count: int = 6, mode: str | None = None) -> list[list[str]]:
    """Свіжі збалансовані тарілки з нуля."""
    plates: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for i in range(count * 40):
        if len(plates) >= count:
            break
        this_mode = mode or ("main" if i % 3 else "breakfast")
        plate: list[str] = []
        fresh = 0
        ok = True
        for slot, _, _ in SLOTS:
            pool = candidates(months, intro, slot, this_mode, exclude=set(plate))
            if fresh >= 1:
                known = [c for c in pool if c in intro]
                pool = known or pool
            if not pool:
                ok = False
                break
            pick = random.choices(pool, weights=[_weight(c, intro) for c in pool])[0]
            if pick not in intro:
                fresh += 1
            plate.append(pick)
        key = tuple(sorted(plate))
        if ok and key not in seen:
            seen.add(key)
            plates.append(plate)
    return plates


def encode(codes: list[str]) -> str:
    return ".".join(codes)


def decode(payload: str) -> list[str]:
    return [c for c in payload.split(".") if catalog.product(c)]
