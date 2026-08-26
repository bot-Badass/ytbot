#!/usr/bin/env python3
"""Збирає data/who.json з офіційних таблиць ВООЗ (data/who_src/*.xlsx).

Джерело: WHO Child Growth Standards, expanded tables (z-scores), 0-5 років,
подобово. Беремо колонки L, M, S - решта (SD*) перераховується формулою LMS.
Запуск: python3 tools/build_who.py
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
BASE = Path(__file__).resolve().parent.parent
SRC = BASE / "data" / "who_src"
OUT = BASE / "data" / "who.json"
MAX_DAY = 1856  # 5 років


def sheet_rows(path: Path) -> list[list[str]]:
    zf = zipfile.ZipFile(path)
    shared: list[str] = []
    if "xl/sharedStrings.xml" in zf.namelist():
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
        shared = ["".join(t.text or "" for t in si.iter(NS + "t")) for si in root.iter(NS + "si")]
    root = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
    rows = []
    for row in root.iter(NS + "row"):
        vals = []
        for cell in row.iter(NS + "c"):
            node = cell.find(NS + "v")
            if node is None:
                vals.append("")
            elif cell.get("t") == "s":
                vals.append(shared[int(node.text)])
            else:
                vals.append(node.text)
        rows.append(vals)
    return rows


def lms_by_day(path: Path) -> list[list[float]]:
    rows = sheet_rows(path)
    header = [h.strip().lower() for h in rows[0]]
    i_day, i_l, i_m, i_s = (header.index(k) for k in ("day", "l", "m", "s"))
    table: dict[int, list[float]] = {}
    for row in rows[1:]:
        if not row or not row[i_day]:
            continue
        day = int(float(row[i_day]))
        if day > MAX_DAY:
            continue
        table[day] = [round(float(row[i_l]), 6), round(float(row[i_m]), 4), round(float(row[i_s]), 6)]
    return [table[d] for d in range(max(table) + 1)]


def main() -> None:
    data = {}
    for sex, tag in (("girls", "girls"), ("boys", "boys")):
        data[sex] = {
            "wfa": lms_by_day(SRC / f"wfa_{tag}.xlsx"),
            "lhfa": lms_by_day(SRC / f"lhfa_{tag}.xlsx"),
        }
    OUT.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    days = len(data["girls"]["wfa"])
    print(f"{OUT}: {days} днів × 2 показники × 2 статі, {OUT.stat().st_size // 1024} КБ")


if __name__ == "__main__":
    main()
