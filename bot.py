#!/usr/bin/env python3
"""YouTube downloader bot.

Приймає посилання на YouTube, показує кнопки якості, віддає готовий файл.
Ліміт розміру береться з MAX_UPLOAD_MB (50 для хмарного Bot API,
2000 якщо піднято локальний telegram-bot-api і виставлено LOCAL_API_URL).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

BASE_DIR = Path(__file__).resolve().parent


def _load_env() -> None:
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_env()

TOKEN = os.environ.get("BOT_TOKEN", "").strip()
LOCAL_API_URL = os.environ.get("LOCAL_API_URL", "").strip().rstrip("/")
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "50"))
MAX_DURATION_MIN = int(os.environ.get("MAX_DURATION_MIN", "180"))
MAX_PARALLEL = int(os.environ.get("MAX_PARALLEL", "2"))
COOKIES_FILE = os.environ.get("COOKIES_FILE", "").strip()
ALLOWED_USERS = {
    int(x) for x in re.split(r"[,\s]+", os.environ.get("ALLOWED_USERS", "")) if x.strip().isdigit()
}
WORK_DIR = Path(os.environ.get("WORK_DIR", "/tmp/ytbot"))
YTDLP = shutil.which("yt-dlp") or str(Path.home() / ".local/bin/yt-dlp")

MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
JOB_TTL = 3600  # скільки живе картка з якостями

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("ytbot")

URL_RE = re.compile(
    r"https?://(?:www\.|m\.|music\.)?(?:youtube\.com/\S+|youtu\.be/\S+)", re.I
)


# ─────────────────────────── стан ───────────────────────────


@dataclass
class Job:
    url: str
    title: str
    duration: int
    uploader: str
    options: dict[str, dict] = field(default_factory=dict)  # key -> {label, size, selector}
    created: float = field(default_factory=time.time)


JOBS: dict[str, Job] = {}
SEM = asyncio.Semaphore(MAX_PARALLEL)


def _gc_jobs() -> None:
    now = time.time()
    for key in [k for k, j in JOBS.items() if now - j.created > JOB_TTL]:
        JOBS.pop(key, None)


def human_size(num: float | None) -> str:
    if not num:
        return "?"
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if num < 1024:
            return f"{num:.0f} {unit}" if unit != "ГБ" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} ТБ"


def human_time(seconds: int | None) -> str:
    if not seconds:
        return "?"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def ytdlp_base_args() -> list[str]:
    args = [YTDLP, "--no-warnings", "--no-playlist", "--ignore-config"]
    if COOKIES_FILE and Path(COOKIES_FILE).exists():
        args += ["--cookies", COOKIES_FILE]
    return args


# ─────────────────────────── yt-dlp ───────────────────────────


def probe(url: str) -> dict:
    """Метадані + список форматів (без завантаження)."""
    import json

    cmd = ytdlp_base_args() + ["-J", url]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if res.returncode != 0:
        raise RuntimeError((res.stderr or res.stdout or "yt-dlp error").strip()[-500:])
    data = json.loads(res.stdout)
    if data.get("_type") == "playlist":
        entries = [e for e in data.get("entries") or [] if e]
        if not entries:
            raise RuntimeError("Порожній плейлист")
        data = entries[0]
    return data


def build_options(info: dict) -> dict[str, dict]:
    """Оцінка розміру для кожної висоти + аудіо."""
    formats = [f for f in info.get("formats") or [] if f.get("protocol") != "mhtml"]

    def size_of(f: dict) -> float:
        return float(f.get("filesize") or f.get("filesize_approx") or 0)

    audio = [f for f in formats if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")]
    video = [f for f in formats if f.get("vcodec") not in (None, "none")]

    # AAC, а не opus: opus у mp4-контейнері ламає QuickTime так само як AV1
    aac = [f for f in audio if str(f.get("acodec") or "").startswith("mp4a")]
    best_audio = max(aac or audio, key=lambda f: float(f.get("abr") or 0), default=None)
    audio_size = size_of(best_audio) if best_audio else 0

    # реальні висоти цього відео, а не фіксований список
    heights = sorted({int(f["height"]) for f in video if f.get("height")})

    options: dict[str, dict] = {}
    for h in heights:
        cands = [f for f in video if (f.get("height") or 0) == h]
        # H.264 програється скрізь; AV1/VP9 у QuickTime дають звук і застиглий кадр.
        # ext=mp4 тут НЕ фільтр: YouTube пакує av01 теж у mp4.
        avc = [f for f in cands if str(f.get("vcodec") or "").startswith("avc1")]
        h264 = bool(avc)
        pool = avc or cands
        pick = min(pool, key=lambda f: size_of(f) or float("inf"))
        est = size_of(pick)
        if pick.get("acodec") in (None, "none"):
            est += audio_size

        options[f"v{h}"] = {
            "label": f"{h}p" if h264 else f"{h}p VP9",
            "size": est,
            "h264": h264,
            "selector": (
                f"bv*[height<={h}][vcodec^=avc1]+ba[acodec^=mp4a]/"
                f"bv*[height<={h}][vcodec^=avc1]+ba/"
                f"b[height<={h}][vcodec^=avc1]/"
                f"bv*[height<={h}]+ba[acodec^=mp4a]/"
                f"bv*[height<={h}]+ba/b[height<={h}]"
            ),
            "audio_only": False,
        }

    if best_audio:
        options["mp3"] = {
            "label": "MP3",
            "size": audio_size,
            "h264": True,
            "selector": "ba/b",
            "audio_only": True,
        }
    return options


def keyboard(token: str, job: Job) -> InlineKeyboardMarkup:
    rows, row = [], []
    for key, opt in job.options.items():
        if opt["audio_only"]:
            continue
        fits = opt["size"] and opt["size"] <= MAX_UPLOAD_BYTES
        mark = "" if fits else ("⚠️ " if opt["size"] else "")
        row.append(
            InlineKeyboardButton(
                f"{mark}{opt['label']} · {human_size(opt['size'])}",
                callback_data=f"dl|{token}|{key}",
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if "mp3" in job.options:
        rows.append(
            [
                InlineKeyboardButton(
                    f"🎵 MP3 · {human_size(job.options['mp3']['size'])}",
                    callback_data=f"dl|{token}|mp3",
                )
            ]
        )
    rows.append([InlineKeyboardButton("✖️ Скасувати", callback_data=f"x|{token}|-")])
    return InlineKeyboardMarkup(rows)


def download(job: Job, key: str, outdir: Path, on_progress) -> Path:
    """Синхронне завантаження, прогрес через колбек (рядок)."""
    opt = job.options[key]
    template = str(outdir / "%(title).80B.%(ext)s")
    cmd = ytdlp_base_args() + [
        "-f", opt["selector"],
        "-o", template,
        "--newline",
        "--no-part",
        "--retries", "5",
        "--fragment-retries", "10",
        "--concurrent-fragments", "4",
    ]
    if opt["audio_only"]:
        cmd += ["-x", "--audio-format", "mp3", "--audio-quality", "0"]
    else:
        # faststart: moov-атом на початок, інакше Telegram не стрімить до кінця файлу
        cmd += [
            "--merge-output-format", "mp4",
            "--postprocessor-args", "Merger+ffmpeg_o:-movflags +faststart",
        ]
    cmd.append(job.url)

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
    )
    tail: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip()
        tail.append(line)
        del tail[:-25]
        m = re.search(r"\[download\]\s+([\d.]+)%.*?of\s+~?\s*([\d.]+\w+)(?:.*?at\s+([\d.]+\w+/s))?", line)
        if m:
            on_progress(f"{m.group(1)}% з {m.group(2)}" + (f" · {m.group(3)}" if m.group(3) else ""))
        elif "[Merger]" in line or "[ExtractAudio]" in line:
            on_progress("обробка (ffmpeg)…")
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError("\n".join(tail[-8:]) or "yt-dlp завершився з помилкою")

    files = [p for p in outdir.iterdir() if p.is_file()]
    if not files:
        raise RuntimeError("yt-dlp нічого не зберіг")
    return max(files, key=lambda p: p.stat().st_size)


def probe_media(path: Path) -> tuple[int, int, int]:
    """(width, height, duration) через ffprobe; нулі якщо не вийшло."""
    def ask(args: list[str]) -> list[str]:
        try:
            res = subprocess.run(
                ["ffprobe", "-v", "error", *args, "-of", "default=nw=1:nk=1", str(path)],
                capture_output=True, text=True, timeout=60,
            )
            return res.stdout.split()
        except Exception:
            return []

    dims = ask(["-select_streams", "v:0", "-show_entries", "stream=width,height"])
    dur = ask(["-show_entries", "format=duration"])
    to_int = lambda vals, i: int(float(vals[i])) if len(vals) > i and vals[i] != "N/A" else 0
    return to_int(dims, 0), to_int(dims, 1), to_int(dur, 0)


def make_thumb(path: Path, outdir: Path, duration: int) -> Path | None:
    thumb = outdir / "thumb.jpg"
    ts = max(1, min(duration // 10, 30)) if duration else 1
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(ts), "-i", str(path), "-frames:v", "1",
             "-vf", "scale=320:-2", str(thumb)],
            capture_output=True, timeout=90, check=True,
        )
        return thumb if thumb.exists() and thumb.stat().st_size < 200_000 else None
    except Exception:
        return None


# ─────────────────────────── хендлери ───────────────────────────


def allowed(update: Update) -> bool:
    if not ALLOWED_USERS:
        return True
    user = update.effective_user
    return bool(user and user.id in ALLOWED_USERS)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        return
    await update.message.reply_text(
        "Кинь посилання на YouTube — покажу доступні якості й віддам файл.\n\n"
        f"Ліміт віддачі: {MAX_UPLOAD_MB} МБ. Максимальна довжина: {MAX_DURATION_MIN} хв.\n"
        "Є ще MP3 — окремою кнопкою."
    )


async def on_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        return
    msg = update.message
    match = URL_RE.search(msg.text or "")
    if not match:
        await msg.reply_text("Не бачу посилання на YouTube.")
        return
    url = match.group(0)

    status = await msg.reply_text("Читаю відео…")
    try:
        info = await asyncio.to_thread(probe, url)
    except Exception as exc:  # noqa: BLE001
        log.warning("probe failed for %s: %s", url, exc)
        await status.edit_text(f"Не вийшло прочитати відео:\n<code>{_esc(str(exc))}</code>",
                               parse_mode=ParseMode.HTML)
        return

    duration = int(info.get("duration") or 0)
    if duration and duration > MAX_DURATION_MIN * 60:
        await status.edit_text(
            f"Відео задовге ({human_time(duration)}), ліміт {MAX_DURATION_MIN} хв."
        )
        return

    options = build_options(info)
    if not options:
        await status.edit_text("Не знайшов придатних форматів.")
        return

    _gc_jobs()
    token = secrets.token_urlsafe(6)
    job = Job(
        url=info.get("webpage_url") or url,
        title=info.get("title") or "video",
        duration=duration,
        uploader=info.get("uploader") or "",
        options=options,
    )
    JOBS[token] = job

    head = (
        f"<b>{_esc(job.title)}</b>\n"
        f"{_esc(job.uploader)} · {human_time(duration)}\n\n"
        "Обери якість:"
    )
    await status.edit_text(head, parse_mode=ParseMode.HTML, reply_markup=keyboard(token, job))


async def on_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    action, token, key = query.data.split("|", 2)

    if action == "x":
        JOBS.pop(token, None)
        await query.edit_message_text("Скасовано.")
        return

    job = JOBS.get(token)
    if not job or key not in job.options:
        await query.edit_message_text("Картка застаріла — надішли посилання ще раз.")
        return

    opt = job.options[key]
    est = opt["size"]
    if est and est > MAX_UPLOAD_BYTES:
        await query.answer(
            f"{human_size(est)} > ліміту {MAX_UPLOAD_MB} МБ — обери нижчу якість",
            show_alert=True,
        )
        return

    await query.edit_message_text(
        f"<b>{_esc(job.title)}</b>\n{opt['label']} — у черзі…",
        parse_mode=ParseMode.HTML,
    )

    async with SEM:
        await _run_job(query, job, key, opt)


async def _run_job(query, job: Job, key: str, opt: dict) -> None:
    loop = asyncio.get_running_loop()
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    outdir = Path(tempfile.mkdtemp(dir=WORK_DIR))
    last_edit = 0.0

    def on_progress(text: str) -> None:
        nonlocal last_edit
        now = time.time()
        if now - last_edit < 4:
            return
        last_edit = now
        asyncio.run_coroutine_threadsafe(
            _safe_edit(query, f"<b>{_esc(job.title)}</b>\n{opt['label']} — {_esc(text)}"),
            loop,
        )

    try:
        await _safe_edit(query, f"<b>{_esc(job.title)}</b>\n{opt['label']} — качаю…")
        path = await asyncio.to_thread(download, job, key, outdir, on_progress)

        size = path.stat().st_size
        if size > MAX_UPLOAD_BYTES:
            await _safe_edit(
                query,
                f"Файл вийшов {human_size(size)} — більше за ліміт {MAX_UPLOAD_MB} МБ.\n"
                "Обери нижчу якість.",
            )
            return

        await _safe_edit(query, f"<b>{_esc(job.title)}</b>\n{opt['label']} — віддаю ({human_size(size)})…")
        chat = query.message.chat

        if opt["audio_only"]:
            await chat.send_action(ChatAction.UPLOAD_DOCUMENT)
            with path.open("rb") as fh:
                await chat.send_audio(
                    audio=InputFile(fh, filename=path.name),
                    title=job.title[:64],
                    performer=job.uploader[:64] or None,
                    duration=job.duration or None,
                    caption=job.title[:1000],
                    read_timeout=900, write_timeout=900, connect_timeout=60, pool_timeout=900,
                )
        else:
            w, h, d = await asyncio.to_thread(probe_media, path)
            thumb = await asyncio.to_thread(make_thumb, path, outdir, d or job.duration)
            await chat.send_action(ChatAction.UPLOAD_VIDEO)
            thumb_fh = thumb.open("rb") if thumb else None
            try:
                with path.open("rb") as fh:
                    await chat.send_video(
                        video=InputFile(fh, filename=path.name),
                        width=w or None,
                        height=h or None,
                        duration=d or job.duration or None,
                        thumbnail=InputFile(thumb_fh, filename="thumb.jpg") if thumb_fh else None,
                        supports_streaming=True,
                        caption=(
                            f"{job.title[:900]}\n{opt['label']} · {human_size(size)}"
                            + ("" if opt.get("h264", True) else
                               "\n⚠️ VP9/AV1 — QuickTime не програє, потрібен VLC")
                        ),
                        read_timeout=1800, write_timeout=1800, connect_timeout=60, pool_timeout=1800,
                    )
            finally:
                if thumb_fh:
                    thumb_fh.close()

        await _safe_edit(query, f"<b>{_esc(job.title)}</b>\n✅ {opt['label']} · {human_size(size)}")

    except Exception as exc:  # noqa: BLE001
        log.exception("job failed: %s", job.url)
        await _safe_edit(
            query,
            f"Не вийшло:\n<code>{_esc(str(exc)[-800:])}</code>",
        )
    finally:
        shutil.rmtree(outdir, ignore_errors=True)


async def _safe_edit(query, text: str) -> None:
    try:
        await query.edit_message_text(text, parse_mode=ParseMode.HTML)
    except TelegramError:
        pass


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("unhandled: %s", context.error, exc_info=context.error)


def main() -> None:
    if not TOKEN:
        raise SystemExit("BOT_TOKEN не заданий (~/ytbot/.env)")

    builder = (
        Application.builder()
        .token(TOKEN)
        .read_timeout(120)
        .write_timeout(1800)
        .connect_timeout(60)
        .pool_timeout(1800)
    )
    if LOCAL_API_URL:
        builder = builder.base_url(f"{LOCAL_API_URL}/bot").base_file_url(f"{LOCAL_API_URL}/file/bot")
        log.info("локальний Bot API: %s (ліміт %s МБ)", LOCAL_API_URL, MAX_UPLOAD_MB)

    app = builder.build()
    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CallbackQueryHandler(on_choice, pattern=r"^(dl|x)\|"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_link))
    app.add_error_handler(on_error)

    log.info("ytbot стартує, ліміт %s МБ, паралельно %s", MAX_UPLOAD_MB, MAX_PARALLEL)
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
