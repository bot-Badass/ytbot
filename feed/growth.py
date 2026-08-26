"""Перцентилі росту за стандартами ВООЗ (LMS, подобово, 0-5 років)."""
from __future__ import annotations

import functools
import json
import math
from datetime import date
from pathlib import Path

WHO_PATH = Path(__file__).resolve().parent.parent / "data" / "who.json"


@functools.lru_cache(maxsize=1)
def _tables() -> dict:
    return json.loads(WHO_PATH.read_text(encoding="utf-8"))


def age_days(birth: date, when: date | None = None) -> int:
    return ((when or date.today()) - birth).days


def age_months(birth: date, when: date | None = None) -> int:
    return int(age_days(birth, when) // 30.4375)


def age_text(birth: date, when: date | None = None) -> str:
    days = age_days(birth, when)
    if days < 0:
        return "ще не народилась"
    months = int(days // 30.4375)
    rest = days - int(months * 30.4375)
    if months == 0:
        return f"{days} дн."
    return f"{months} міс {rest} дн." if rest else f"{months} міс"


def _lms(sex: str, kind: str, day: int) -> tuple[float, float, float] | None:
    table = _tables().get(sex, {}).get(kind)
    if not table:
        return None
    day = max(0, min(day, len(table) - 1))
    l, m, s = table[day]
    return l, m, s


def zscore(sex: str, kind: str, day: int, value: float) -> float | None:
    lms = _lms(sex, kind, day)
    if not lms or value <= 0:
        return None
    l, m, s = lms
    if abs(l) < 1e-9:
        return math.log(value / m) / s
    return ((value / m) ** l - 1) / (l * s)


def value_at_z(sex: str, kind: str, day: int, z: float) -> float | None:
    lms = _lms(sex, kind, day)
    if not lms:
        return None
    l, m, s = lms
    if abs(l) < 1e-9:
        return m * math.exp(s * z)
    base = 1 + l * s * z
    if base <= 0:
        return None
    return m * base ** (1 / l)


def percentile(z: float) -> float:
    return 100 * 0.5 * (1 + math.erf(z / math.sqrt(2)))


def verdict(z: float) -> str:
    if z < -3:
        return "⛔ значно нижче норми ВООЗ - показати педіатру"
    if z < -2:
        return "⚠️ нижче норми ВООЗ - варто обговорити з педіатром"
    if z <= 2:
        return "✅ у межах норми ВООЗ"
    if z <= 3:
        return "⚠️ вище норми ВООЗ - варто обговорити з педіатром"
    return "⛔ значно вище норми ВООЗ - показати педіатру"


def summary(sex: str, kind: str, day: int, value: float, unit: str) -> str:
    z = zscore(sex, kind, day, value)
    if z is None:
        return ""
    lo = value_at_z(sex, kind, day, -2)
    hi = value_at_z(sex, kind, day, 2)
    pct = percentile(z)
    band = f"{lo:.1f}-{hi:.1f} {unit}" if lo and hi else ""
    return (f"{value:.2f} {unit} · {pct:.0f}-й перцентиль (z={z:+.2f})\n"
            f"Норма для віку: {band}\n{verdict(z)}")
