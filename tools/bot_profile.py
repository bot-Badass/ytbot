#!/usr/bin/env python3
"""Публічний профіль бота: опис, коротке пояснення, меню команд.

    python3 tools/bot_profile.py            # показати, що зараз і що стане
    python3 tools/bot_profile.py --apply    # записати в Telegram

Опис бачить кожен, хто відкрив бота і ще не натиснув «Почати». Коротке пояснення
показується в профілі і в пошуку. Команди - це меню зліва від поля вводу.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

NAME = "Прикорм · Маленький Всесвіт"

SHORT = ("Щоденник прикорму: що дитина їла, як подавати за віком, "
         "алергени, зростання за ВООЗ.")

DESCRIPTION = (
    "Помічник з прикорму для батьків.\n\n"
    "· щоденник їжі за два дотики, записи лишаються в чаті\n"
    "· 202 продукти: з якого віку, чи алерген, як подавати саме зараз\n"
    "· тарілки і рецепти за віком, генератор варіантів\n"
    "· холодильник: складає меню з того, що вдома, і рахує покупки на тиждень\n"
    "· реакції, правило трьох днів, нагадування\n"
    "· зростання за офіційними таблицями ВООЗ\n\n"
    "Мама і тато ведуть один щоденник. Безкоштовно, без реклами.\n"
    "Довідка на основі ВООЗ, AAP і EFSA, не замінює педіатра."
)

PUBLIC_COMMANDS = [
    ("start", "головне меню прикорму"),
    ("help", "інструкція: як користуватись"),
    ("id", "показати мій Telegram ID"),
]

OWNER_COMMANDS = PUBLIC_COMMANDS + [("yt", "завантажити відео з YouTube")]


def token() -> str:
    tok = os.environ.get("BOT_TOKEN", "").strip()
    if tok:
        return tok
    for line in (BASE / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("BOT_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("BOT_TOKEN не знайдено")


def owners() -> list[int]:
    text = (BASE / ".env").read_text(encoding="utf-8")
    m = re.search(r"^YT_USERS=(.*)$", text, re.M)
    return [int(x) for x in re.split(r"[,\s]+", m.group(1) if m else "") if x.strip().isdigit()]


async def run(apply: bool) -> None:
    from telegram import Bot, BotCommand, BotCommandScopeChat, MenuButtonCommands

    bot = Bot(token())
    async with bot:
        me = await bot.get_me()
        cur_name = (await bot.get_my_name()).name
        cur_desc = (await bot.get_my_description()).description
        cur_short = (await bot.get_my_short_description()).short_description
        cur_cmds = await bot.get_my_commands()

        print(f"бот: @{me.username}\n")
        print("── назва ──")
        print(f"було:  {cur_name}")
        print(f"стане: {NAME}  [{len(NAME)}/64]\n")
        print("── коротке пояснення ──")
        print(f"було:  {cur_short or '(порожньо)'}")
        print(f"стане: {SHORT}  [{len(SHORT)}/120]\n")
        print("── опис ──")
        print(f"було:  {cur_desc or '(порожньо)'}")
        print(f"стане:\n{DESCRIPTION}\n[{len(DESCRIPTION)}/512]\n")
        print("── команди ──")
        print(f"було:  {[c.command for c in cur_cmds] or '(порожньо)'}")
        print(f"стане: {[c for c, _ in PUBLIC_COMMANDS]} "
              f"+ для власників {[c for c, _ in OWNER_COMMANDS]}\n")

        if len(NAME) > 64 or len(SHORT) > 120 or len(DESCRIPTION) > 512:
            raise SystemExit("перевищено ліміт Telegram")
        if not apply:
            print("Нічого не змінено. Додай --apply, щоб записати.")
            return

        if cur_name != NAME:
            await bot.set_my_name(NAME)          # Telegram обмежує зміну назви кількома разами на добу
        await bot.set_my_short_description(SHORT, language_code="uk")
        await bot.set_my_short_description(SHORT)
        await bot.set_my_description(DESCRIPTION, language_code="uk")
        await bot.set_my_description(DESCRIPTION)
        await bot.set_my_commands([BotCommand(c, d) for c, d in PUBLIC_COMMANDS])
        await bot.set_my_commands([BotCommand(c, d) for c, d in PUBLIC_COMMANDS],
                                  language_code="uk")
        for uid in owners():
            await bot.set_my_commands([BotCommand(c, d) for c, d in OWNER_COMMANDS],
                                      scope=BotCommandScopeChat(uid))
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        print("Записано.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    asyncio.run(run(ap.parse_args().apply))
