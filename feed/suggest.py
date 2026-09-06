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
    "yalovychyna": ("говядина", "яловичина"),
    "svynyna": ("свинка",),
    "yaitse": ("яйця", "яєчня", "омлет", "жовток", "білок"),
    "kartoplia": ("картошка", "картопелька"),
    "grechka": ("гречана",),
    "vivsianka": ("вівсяні", "геркулес", "овсянка"),
    "oliia": ("оливкова", "соняшникова"),
    "maslo": ("вершкове",),
    "ghi": ("гхі", "топлене", "ghee"),
    "syr_kyslo": ("творог", "сирок"),
    "syr_tverdyi": ("сир твердий",),
    "yogurt": ("йогурт",),
    "makarony": ("паста", "спагеті", "локшина"),
    "khlib": ("тост", "хлібець"),
    "kvasolia_struch": ("стручкова",),
    "sochevytsia": ("чечевиця", "сочевиця", "червона сочевиця"),
    "soch_zelena": ("зелена сочевиця", "коричнева сочевиця"),
    "pechinka": ("печінка",),
    "triska": ("хек", "мінтай", "риба"),
    "losos": ("сьомга", "форель"),
    "tsvitna": ("цвітна",),
    "brokoli": ("броколі", "брокколі"),
    "garbuz": ("тиква", "гарбузов"),
    "buriak": ("свекла",),
    "kabachok": ("цукіні",),
    "goroshok": ("горошок", "горох"),
    "gorikhy": ("горіх", "волоський", "мигдаль"),
    "arakhis": ("арахіс",),
    "tahini": ("кунжут",),
    "polunytsia": ("клубніка",),
    "chornytsia": ("голубіка",),
    "pecheryts": ("гриби", "гриб", "печериці", "шампіньйони"),
    "perets": ("перець", "паприка", "болгарський"),
    "salat": ("латук", "салат"),
    "zelen": ("кріп", "петрушка", "зелень"),
    "kukurudza": ("качан",),
    "krevetky": ("креветка",),
    "midii": ("мідія",),
    "olyvky": ("оливки", "маслини"),
    "smetana": ("сметана",),
    "vershky": ("вершки",),
    "mozarela": ("моцарела", "моцарелла"),
    "bryndza": ("бринза", "фета"),
    "koz_syr": ("козячий",),
    "yaitse_per": ("перепелине", "перепелині"),
    "telyatyna": ("телятина",),
    "pechinka_y": ("печінка яловича",),
    "horokh": ("горох лущений",),
    "rys_buryi": ("бурий рис",),
    "rys_loksh": ("рисова локшина", "фунчоза"),
    "kompot": ("узвар",),
    "smorodyna": ("порічки",),
    "zhuravlyna": ("клюква",),
    "kapusta": ("капуста",),
    "kartoplia_x": (),
}


def _norm(word: str) -> str:
    word = unicodedata.normalize("NFKC", word).lower().replace("ʼ", "'").replace("’", "'")
    return re.sub(r"[^а-яїієґa-z']", "", word)


def stem(word: str) -> str:
    """Груба основа слова: обрізаємо українські закінчення."""
    word = _norm(word)
    for end in ("ами", "ями", "ові", "ею", "ою", "ів", "ам", "ах", "ий", "ої", "ою",
                "ку", "ці", "ка", "ки", "ко", "ом", "ем", "у", "ю", "и", "і", "а", "я", "е", "о"):
        if len(word) - len(end) >= 3 and word.endswith(end):
            return word[: -len(end)]
    return word


def _phrases(code: str) -> set[tuple[str, str]]:
    """Пари сусідніх слів назви: «зелена сочевиця» має знайти зелену сочевицю,
    а не зелений горошок плюс червону сочевицю."""
    product = catalog.product(code)
    out: set[tuple[str, str]] = set()
    sources = [product["name"], *ALIASES.get(code, ())]
    for src in sources:
        words = [stem(w) for w in re.split(r"[\s(),]+", src) if len(w) > 2]
        words = [w for w in words if w]
        for a, b in zip(words, words[1:]):
            out.add((a, b))
            out.add((b, a))
    return out


def _keys(code: str) -> set[str]:
    product = catalog.product(code)
    words = [w for w in re.split(r"[\s(),]+", product["name"]) if len(w) > 2]
    words += list(ALIASES.get(code, ()))
    return {stem(w) for w in words if stem(w)}


def parse_products(text: str, limit: int = 6) -> list[str]:
    """Витягує коди продуктів з довільного тексту на кшталт «морква і курка».

    Спершу шукаємо точний збіг слова, і лише потім збіг за початком: інакше
    «оливки» чіплялись за «оливу» в назві олії, бо олія стоїть у файлі вище.
    """
    index: list[tuple[str, set[str]]] = [(code, _keys(code)) for code in catalog.products()]
    keys_of = dict(index)
    phrases: list[tuple[str, set[tuple[str, str]]]] = [(code, _phrases(code))
                                                       for code in catalog.products()]
    tokens = [stem(raw) for raw in re.split(r"[\s,;.+/&]+|\bі\b|\bта\b|\bи\b", text)]
    found: list[str] = []
    used: set[str] = set()
    i = 0
    while i < len(tokens) and len(found) < limit:
        token = tokens[i]
        if len(token) < 3 or token in used:
            i += 1
            continue
        pair = (token, tokens[i + 1]) if i + 1 < len(tokens) else None
        best = next((code for code, ph in phrases if pair and pair in ph), None)
        step = 2 if best else 1
        if not best:
            best = next((code for code, keys in index if token in keys), None)
        if not best and len(token) >= 4:
            best = next((code for code, keys in index
                         if any(key.startswith(token) or token.startswith(key) for key in keys)),
                        None)
        if best and best not in found:
            found.append(best)
            # слова вже знайденого продукту не мають ловити ще один: «бурий рис»
            # це один продукт, а не бурий рис плюс рис
            used |= keys_of[best]
        i += step
    return found


def slot_of(code: str) -> str | None:
    if code in STARCHY:
        return "grain"
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
               exclude: set[str] = frozenset(),
               only: set[str] | None = None) -> list[str]:
    out = []
    for code, product in catalog.products().items():
        if code in exclude or product["group"] == "other":
            continue
        if code in NEVER or product.get("plate") is False:
            continue
        # екзотику не пропонуємо самі. Але якщо вона лежить у холодильнику
        # (only задано), то це вже свідомий вибір - працюємо з нею як зі звичайною
        if product.get("rare") and only is None:
            continue
        if only is not None and code not in only:
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


# ─────────────────────── холодильник ───────────────────────

# скільки чого тримати вдома на тиждень, щоб щодня складалася тарілка
WEEK_TARGET = {"plant": 6, "grain": 3, "protein": 4, "fat": 2}

# всередині білка тиждень має бути різним: не сім днів самої курки
PROTEIN_TARGET = {"meat": 2, "fish": 1, "egg": 1, "legume": 1}


def stock_plates(months: int, intro: dict, stock: list[str], count: int = 6) -> list[list[str]]:
    """Тарілки ТІЛЬКИ з того, що лежить у холодильнику.

    Слот, під який удома нічого немає, просто лишається порожнім - тарілку однаково
    показуємо, а чого бракує, порахує missing_slots().
    """
    pool = set(stock)
    plates: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for i in range(count * 60):
        if len(plates) >= count:
            break
        mode = "main" if i % 3 else "breakfast"
        plate: list[str] = []
        for slot, _, _ in SLOTS:
            picks = candidates(months, intro, slot, mode, exclude=set(plate), only=pool)
            if not picks:
                continue
            plate.append(random.choices(picks, weights=[_weight(c, intro) for c in picks])[0])
        if len(plate) < 2:
            continue
        key = tuple(sorted(plate))
        if key not in seen:
            seen.add(key)
            plates.append(plate)
    plates.sort(key=lambda pl: (len(missing_slots(pl)), -len(pl)))
    return plates


def _times(n: int) -> str:
    return f"{n} раз" if n == 1 else f"{n} рази"


def _eats_well(code: str, intro: dict) -> bool:
    row = intro.get(code)
    return bool(row) and row["status"] == "ok"


def shopping(months: int, intro: dict, stock: list[str], limit: int = 14) -> list[tuple[str, str]]:
    """Що докупити на тиждень уперед. Повертає пари (код, причина).

    Логіка проста і перевіряється очима: рахуємо, скільки в холодильнику вже
    закриває кожен слот, добираємо до тижневої норми, всередині білка тримаємо
    різноманіття, і додаємо рівно один новий продукт та один алерген -
    більше нового за тиждень вводити не можна через правило трьох днів.
    """
    have = set(stock)
    picked: list[tuple[str, str]] = []
    taken: set[str] = set(have)

    def pool(slot: str, sub: str | None = None) -> list[str]:
        out = []
        for code in candidates(months, intro, slot, "main") + candidates(months, intro, slot, "breakfast"):
            if code in taken or code in out:
                continue
            if sub and catalog.product(code).get("sub") != sub:
                continue
            out.append(code)
        # спершу те, що дитина вже їсть, далі знайоме, нове лишаємо на кінець
        out.sort(key=lambda c: (0 if _eats_well(c, intro) else 1 if c in intro else 2,
                                catalog.product(c)["from_month"]))
        return out

    def take(code: str, why: str) -> None:
        taken.add(code)
        picked.append((code, why))

    # 1. білок: спершу різноманіття всередині групи
    for sub, need in PROTEIN_TARGET.items():
        already = sum(1 for c in have if (catalog.product(c) or {}).get("sub") == sub)
        for code in pool("protein", sub)[: max(need - already, 0)]:
            take(code, f"{catalog.SUBGROUPS[sub][1].lower()} на тиждень: {_times(need)}")

    # 2. решта слотів до тижневої норми
    for slot, need in WEEK_TARGET.items():
        already = sum(1 for c in have if slot_of(c) == slot)
        already += sum(1 for c, _ in picked if slot_of(c) == slot)
        for code in pool(slot)[: max(need - already, 0)]:
            take(code, f"{SLOT_LABEL[slot]}: на тиждень тримаємо {need} різних, удома {already}")

    # 3. рівно один новий продукт тижня
    if not any(c not in intro for c, _ in picked):
        fresh = [c for slot, _ in WEEK_TARGET.items() for c in pool(slot) if c not in intro]
        if fresh:
            take(fresh[0], "новий продукт тижня: тримаємо 2-3 дні поспіль")

    # 4. алерген, якого ще не пробували
    allergen = next((c for c in catalog.allergen_codes()
                     if c not in intro and c not in taken
                     and catalog.product(c)["from_month"] <= months
                     and catalog.product(c).get("plate") is not False), None)
    if allergen:
        take(allergen, "алерген, який ще не вводили: чекати не треба")

    return picked[:limit]


# ─────────────────────── що ввести наступним ───────────────────────

THREE_DAYS = 3 * 86400


def holding(intro: dict, now_ts: int) -> list[str]:
    """Продукти, які зараз «на карантині» правила трьох днів.

    Поки триває знайомство з новим продуктом або він під наглядом після реакції,
    інший новий вводити не можна: інакше не зрозуміло, на що була реакція.
    """
    out = []
    for code, row in intro.items():
        if row["status"] == "watch":
            out.append(code)
        elif row["status"] == "trying" and now_ts - row["first_ts"] < THREE_DAYS:
            out.append(code)
    return out


def slot_coverage(intro: dict) -> dict[str, int]:
    """Скільки продуктів кожного слоту вже введено."""
    counts = {key: 0 for key, _, _ in SLOTS}
    for code, row in intro.items():
        if row["status"] == "avoid" or not catalog.product(code):
            continue
        slot = slot_of(code)
        if slot:
            counts[slot] += 1
    return counts


def next_products(months: int, intro: dict, stock: list[str] | None = None,
                  limit: int = 3) -> list[tuple[str, str]]:
    """Що логічно ввести наступним. Пари (код, чому саме він).

    Порядок простий і його видно очима: спершу алерген, якого ще не пробували
    (їх вводять рано і регулярно), далі слот, у якому в раціоні найменше
    продуктів, і всередині - те, що вже лежить удома.
    """
    stock = set(stock or [])
    counts = slot_coverage(intro)
    lean = sorted(counts, key=lambda s: counts[s])

    def ok(code: str) -> bool:
        p = catalog.product(code)
        return bool(p) and code not in intro and code not in NEVER \
            and p.get("plate") is not False and not p.get("rare") \
            and p["group"] != "other" and p["from_month"] <= months

    # порядок у каталозі йде від базових продуктів до рідших, тому він
    # кращий тайбрейк, ніж алфавіт: інакше «вуглевод» починається з амаранту
    rank = {code: i for i, code in enumerate(catalog.products())}

    def order(code: str) -> tuple:
        p = catalog.product(code)
        return (0 if code in stock else 1, p["from_month"], rank[code])

    picked: list[tuple[str, str]] = []
    taken: set[str] = set()

    def take(code: str, why: str) -> None:
        taken.add(code)
        at_home = " Уже лежить у холодильнику." if code in stock else ""
        picked.append((code, why + at_home))

    allergens = sorted((c for c in catalog.allergen_codes() if ok(c)), key=order)
    if allergens:
        code = allergens[0]
        take(code, f"Алерген ({catalog.product(code)['allergen']}), якого ще не пробували. "
                   "Їх вводять рано і потім дають регулярно.")

    for slot in lean:
        if len(picked) >= limit:
            break
        pool = sorted((c for c in catalog.products()
                       if ok(c) and c not in taken and slot_of(c) == slot), key=order)
        if not pool:
            continue
        take(pool[0], f"У раціоні найменше закритий слот «{SLOT_LABEL[slot]}»: "
                      f"продуктів там {counts[slot]}.")

    return picked[:limit]
