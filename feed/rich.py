"""Rich messages (Bot API 10.1+): відправка екранів і запасний шлях у звичайний HTML.

Екрани з ui повертають або звичайний Telegram-HTML (str, рядки через \\n), або Rich -
вже зверстаний rich-HTML (h3, ul, table, details...). Обидва їдуть як sendRichMessage /
editMessageText(rich_message). Якщо Telegram відхилив rich, той самий екран іде
звичайним HTML через to_tg(): клієнт завжди отримує відповідь.

PTB 22.8 знає Bot API 10.0, тому виклики сирі, через bot.do_api_request.
"""
from __future__ import annotations

import html
import html.parser
import logging
import re

from telegram import InlineKeyboardMarkup
from telegram.error import TelegramError

log = logging.getLogger("feed.rich")

RICH_LIMIT = 32768
PLAIN_LIMIT = 4096


class Rich(str):
    """Екран, уже зверстаний у rich-HTML."""


def esc(text) -> str:
    return html.escape(str(text), quote=False)


# ─────────────────────── звичайний HTML -> rich ───────────────────────

def _lead_spaces(line: str) -> str:
    stripped = line.lstrip(" ")
    return " " * (len(line) - len(stripped)) + stripped


def from_tg(text: str) -> str:
    """Telegram-HTML з переносами рядків -> абзаци rich-HTML (відступи пробілами зберігаються)."""
    paras = [p for p in re.split(r"\n\s*\n", text.strip("\n")) if p.strip()]
    return "".join("<p>" + "<br>".join(_lead_spaces(l) for l in p.split("\n")) + "</p>" for p in paras)


def to_rich(text: str) -> str:
    return str(text) if isinstance(text, Rich) else from_tg(text)


# ─────────────────────── rich -> звичайний HTML ───────────────────────

class _Flatten(html.parser.HTMLParser):
    """Rich-HTML -> Telegram-HTML: заголовки жирним, списки маркерами, таблиці рядками."""

    INLINE = {"b", "i", "u", "s", "code", "a", "tg-spoiler"}
    ALIAS = {"strong": "b", "em": "i", "ins": "u", "del": "s", "strike": "s"}

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out: list[str] = []
        self.lists: list[list] = []      # [тип, лічильник]
        self.cells: list[str] | None = None
        self.cell_head = False

    def _nl(self, n: int = 1) -> None:
        tail = "".join(self.out)[-2:]
        have = len(tail) - len(tail.rstrip("\n"))
        if self.out and have < n:
            self.out.append("\n" * (n - have))

    def handle_starttag(self, tag, attrs):
        tag = self.ALIAS.get(tag, tag)
        if tag in self.INLINE:
            href = dict(attrs).get("href")
            self.out.append(f'<a href="{esc(href)}">' if tag == "a" and href else
                            "" if tag == "a" else f"<{tag}>")
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6", "summary"}:
            self._nl(2 if tag != "summary" else 1)
            self.out.append("<b>")
        elif tag in {"p", "details", "table", "pre"}:
            self._nl(2)
            if tag == "pre":
                self.out.append("<pre>")
        elif tag == "blockquote":
            self._nl(2)
            self.out.append("<blockquote>")
        elif tag == "footer":
            self._nl(2)
            self.out.append("<i>")
        elif tag in {"ul", "ol"}:
            self._nl(1)
            self.lists.append([tag, 0])
        elif tag == "li":
            self._nl(1)
            kind = self.lists[-1] if self.lists else ["ul", 0]
            kind[1] += 1
            self.out.append(f"{kind[1]}. " if kind[0] == "ol" else "• ")
        elif tag == "tr":
            self.cells = []
        elif tag in {"td", "th"}:
            self.cell_head = tag == "th"
            self.out.append("\x00")      # початок клітинки
        elif tag == "br":
            self.out.append("\n")
        elif tag == "hr":
            self._nl(2)

    def handle_endtag(self, tag):
        tag = self.ALIAS.get(tag, tag)
        if tag in self.INLINE:
            self.out.append("</a>" if tag == "a" else f"</{tag}>")
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6", "summary"}:
            self.out.append("</b>")
            self._nl(1)
        elif tag == "blockquote":
            self.out.append("</blockquote>")
        elif tag == "footer":
            self.out.append("</i>")
        elif tag == "pre":
            self.out.append("</pre>")
        elif tag in {"ul", "ol"}:
            if self.lists:
                self.lists.pop()
        elif tag in {"td", "th"}:
            start = len(self.out) - 1 - self.out[::-1].index("\x00")
            cell = "".join(self.out[start + 1:]).strip()
            del self.out[start:]
            if self.cells is not None:
                self.cells.append(f"<b>{cell}</b>" if self.cell_head and cell else cell)
        elif tag == "tr":
            self._nl(1)
            self.out.append(" · ".join(c for c in (self.cells or []) if c))
            self.cells = None

    def handle_data(self, data):
        self.out.append(re.sub(r"\s*\n\s*", " ", data) if data.strip() else
                        ("" if "\n" in data else data))

    def handle_entityref(self, name):
        self.out.append(f"&{name};")

    def handle_charref(self, name):
        self.out.append(f"&#{name};")


def to_tg(rich_html: str) -> str:
    p = _Flatten()
    p.feed(rich_html)
    p.close()
    text = "".join(p.out).replace(" ", " ")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ─────────────────────────── відправка ───────────────────────────

def _payload(text: str, kb: InlineKeyboardMarkup | None) -> dict:
    body = {"rich_message": {"html": to_rich(text), "skip_entity_detection": True}}
    if kb is not None:
        body["reply_markup"] = kb.to_dict()
    return body


async def send(bot, chat_id: int, text: str, kb: InlineKeyboardMarkup | None = None) -> None:
    """Новий екран повідомленням. Rich, а при відмові Telegram - звичайним HTML."""
    try:
        await bot.do_api_request("sendRichMessage", api_kwargs={"chat_id": chat_id, **_payload(text, kb)})
        return
    except TelegramError as exc:
        log.warning("rich не пройшов (%s), шлю звичайним HTML", exc)
    plain = text if not isinstance(text, Rich) else to_tg(text)
    await bot.send_message(chat_id, plain[:PLAIN_LIMIT], parse_mode="HTML", reply_markup=kb,
                           disable_web_page_preview=True)


async def edit(bot, chat_id: int, message_id: int, text: str,
               kb: InlineKeyboardMarkup | None = None) -> bool:
    """Перемальовує повідомлення з кнопкою. False - редагувати не вдалось, треба слати нове."""
    try:
        await bot.do_api_request("editMessageText", api_kwargs={
            "chat_id": chat_id, "message_id": message_id, **_payload(text, kb)})
        return True
    except TelegramError as exc:
        if "not modified" in str(exc).lower():
            return True
        log.warning("rich-редагування не пройшло (%s)", exc)
    plain = text if not isinstance(text, Rich) else to_tg(text)
    try:
        await bot.edit_message_text(plain[:PLAIN_LIMIT], chat_id=chat_id, message_id=message_id,
                                    parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
        return True
    except TelegramError as exc:
        return "not modified" in str(exc).lower()
