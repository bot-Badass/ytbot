# ytbot — YouTube downloader bot

Кидаєш боту посилання на YouTube → бот показує кнопки якості з оцінкою розміру →
качає й віддає файл. Окремою кнопкою MP3.

## Керування

```bash
systemctl --user status ytbot          # стан
systemctl --user restart ytbot         # рестарт після правок bot.py
journalctl --user -u ytbot -f          # логи наживо
```

Автостарт увімкнено (`enable`), `Restart=always`, linger=yes → переживає і краш, і ребут.

## Налаштування — `~/ytbot/.env`

| Змінна | Що робить |
|---|---|
| `BOT_TOKEN` | токен бота |
| `LOCAL_API_URL` | порожньо = хмарний API (50 МБ); `http://127.0.0.1:8081` = локальний (2000 МБ) |
| `MAX_UPLOAD_MB` | ліміт віддачі: 50 для хмарного, 2000 для локального |
| `MAX_DURATION_MIN` | відсікає задовгі відео (типово 180) |
| `MAX_PARALLEL` | скільки завантажень одночасно (2 ядра → 2) |
| `ALLOWED_USERS` | порожньо = бот відкритий усім; список user_id = whitelist |
| `COOKIES_FILE` | шлях до cookies.txt, якщо YouTube почне вимагати логін |

## Ліміт 50 МБ → 2 ГБ

Хмарний Bot API не віддає файли більші за 50 МБ. Знімається локальним
`telegram-bot-api`. Що треба:

1. `sudo apt install -y cmake libssl-dev zlib1g-dev gperf`
2. `api_id` + `api_hash` з https://my.telegram.org → вписати в `~/ytbot/local-api.env`
3. `nohup ~/ytbot/setup-local-api.sh > ~/ytbot/build.log 2>&1 &` (збірка ~1.5-3 год на 2 ядрах)
4. `systemctl --user enable --now telegram-bot-api`
5. У `.env`: `LOCAL_API_URL=http://127.0.0.1:8081` і `MAX_UPLOAD_MB=2000`,
   далі `systemctl --user restart ytbot`

Увага: при переході на локальний сервер Telegram вимагає `logOut` бота з хмарного
сервера один раз:
`curl "https://api.telegram.org/bot<TOKEN>/logOut"` — і тільки після цього
локальний сервер прийме токен.

Кеш локального сервера лежить у `~/ytbot/tgapi-data` і росте — час від часу чистити.

## Диск

Тимчасові файли — `/tmp/ytbot`, чистяться після кожної віддачі. Вільно зараз ~13 ГБ.
