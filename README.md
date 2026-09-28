# cron-automation-scripts

Набор Python-скриптов для запуска по расписанию (**cron** или **systemd timer**):

1. **Парсер сайта**: `requests` + `BeautifulSoup`, результат в CSV/TSV, который сразу открывается в Google Sheets и Excel.
2. **Бэкап и очистка**: `tar.gz`-архивы папок, ротация старых архивов, удаление старых логов.
3. **Отчёт в Telegram**: сводка по результатам парсинга, состоянию бэкапов и диску сервера.

В комплекте готовые `crontab` и unit-файлы systemd.

![Python](https://img.shields.io/badge/python-3.11%2B-blue) ![License: MIT](https://img.shields.io/badge/license-MIT-green)

## Возможности

**`parse_to_csv`: парсер**
- Проходит по страницам пагинации. Есть повторы при ошибках 429/5xx, пауза между запросами и свой User-Agent.
- Сохраняет в CSV (UTF-8) или TSV, который можно вставить прямо в Google Sheets. Флаг `--excel` добавляет BOM, чтобы кириллица в Excel отображалась правильно.
- Запись атомарная: пока файл пишется, никто не увидит его наполовину готовым. Даты в ISO 8601 с часовым поясом.
- В демо парсится [quotes.toscrape.com](https://quotes.toscrape.com), учебная песочница для парсинга. Под свой сайт меняются `parse_page()` и поля `Item`.

**`backup_cleanup`: бэкап и очистка**
- Упаковывает одну или несколько папок в `backup_YYYYmmdd_HHMMSS.tar.gz`, пропуская мусор (`__pycache__`, `.git`, `.venv`, `node_modules`).
- Если папка бэкапов лежит внутри архивируемой, она в архив не попадает.
- Ротация: удаляет архивы старше N дней, но последние K сохраняет всегда.
- Удаляет старые файлы по маске, например `*.log` старше 30 дней.
- `--dry-run` показывает, что будет сделано, ничего не трогая. `--notify` присылает итог в Telegram.

**`report_to_telegram`: ежедневный отчёт**
- Берёт последний CSV парсера: число строк, топ авторов и тегов.
- Проверяет бэкапы: последний архив, его размер и возраст. Если архив старше 26 ч, ставит предупреждение.
- Показывает занятость диска (🟢/🟡/🔴) и load average.
- Длинные сообщения режет на части по лимиту Telegram. `--dry-run` печатает отчёт в консоль.

**Общее**
- Настройки в `.env`. Относительные пути считаются от папки проекта, так что cron-запуск из любой директории работает.
- Логи пишутся в консоль и в файл. Коды выхода понятны cron и systemd: `0` — успех, `1` — ошибка, `2` — нет данных или настроек.
- 13 тестов `pytest` без сети, код проверен `ruff`, type hints.

## Скриншоты

> Здесь будут скриншоты: CSV, импортированный в Google Sheets, и отчёт в Telegram.
> Пример отчёта (`--dry-run`, реальный запуск):
>
> ```
> 📋 Ежедневный отчёт — 28.09.2026 17:43
> Сервер: my-server
>
> 🕸 Парсер
> Файл: quotes_2026-09-28.csv (0 мин назад)
> Строк: 20
> Топ авторов: Albert Einstein (4), J.K. Rowling (2), Marilyn Monroe (2), ...
> Топ тегов: #life (6), #inspirational (5), #love (5), ...
>
> 🗄 Бэкапы
> ✅ Последний: backup_20260928_174340.tar.gz, 2.5 KB, 0 мин назад
> Всего архивов: 1 (2.5 KB)
>
> 💾 Диск
> 🟢 Занято 8%: 9.5 GB из 125.9 GB, свободно 110.0 GB
> ```

## Структура

```
scripts/
  common.py              # .env, логирование, отправка в Telegram, утилиты
  parse_to_csv.py        # парсер -> CSV/TSV
  backup_cleanup.py      # бэкап, ротация, очистка
  report_to_telegram.py  # отчёт в Telegram
deploy/
  crontab.example        # расписание cron (с flock от параллельных запусков)
  systemd/*.service      # oneshot-сервисы
  systemd/*.timer        # таймеры (Persistent=true: пропущенный запуск догоняется)
tests/                   # pytest + HTML-фикстура
```

## Установка

Нужен Python 3.11+ и Linux-сервер (скрипты работают и на macOS/Windows, но cron и systemd есть только в Linux).

```bash
sudo git clone https://github.com/oqoxucufawo66-spec/cron-automation-scripts.git /opt/cron-automation-scripts
cd /opt/cron-automation-scripts
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env          # впишите TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID
```

## Использование

Скрипты запускаются из папки проекта:

```bash
# Парсер: 2 страницы в output/quotes_<дата>.csv
.venv/bin/python -m scripts.parse_to_csv --max-pages 2
.venv/bin/python -m scripts.parse_to_csv --format tsv          # для вставки в Google Sheets
.venv/bin/python -m scripts.parse_to_csv --excel -o output/quotes.csv

# Бэкап: сначала посмотреть, что будет сделано
.venv/bin/python -m scripts.backup_cleanup --source output --dest backups --clean logs --dry-run
.venv/bin/python -m scripts.backup_cleanup --source /var/www/site --dest /backups --keep-days 14 --keep-last 5 --notify

# Отчёт
.venv/bin/python -m scripts.report_to_telegram --dry-run
.venv/bin/python -m scripts.report_to_telegram
```

Все параметры: `--help` у каждого скрипта.

### Как загрузить в Google Sheets

**Файл → Импорт → Загрузить**, выберите CSV, разделитель «Запятая». Другой способ: откройте `.tsv`, скопируйте всё и вставьте в таблицу, колонки разложатся сами.

### Расписание: cron

```bash
crontab -e      # вставьте строки из deploy/crontab.example
mkdir -p logs
```

По умолчанию: парсер в 07:00, бэкап в 03:30, отчёт в 09:00. `flock -n` не даёт запустить задачу повторно, пока предыдущий запуск не закончился.

### Расписание: systemd timer

```bash
sudo useradd --system --home /opt/cron-automation-scripts automation
sudo chown -R automation: /opt/cron-automation-scripts
sudo cp deploy/systemd/* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now parse-to-csv.timer backup-cleanup.timer report-to-telegram.timer
systemctl list-timers | grep -E "parse|backup|report"
journalctl -u report-to-telegram.service -n 50
```

### Настройки (`.env`)

| Переменная | По умолчанию | Описание |
|---|---|---|
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | — | для отчётов и `--notify` |
| `PARSER_START_URL` | `https://quotes.toscrape.com/` | стартовая страница |
| `PARSER_OUTPUT_DIR` | `output` | куда сохранять CSV |
| `PARSER_MAX_PAGES` | `0` (все) | лимит страниц |
| `PARSER_DELAY` | `1.0` | пауза между запросами, сек |
| `BACKUP_SOURCES` | — | что архивировать, через запятую |
| `BACKUP_DEST` | `backups` | куда класть архивы |
| `BACKUP_KEEP_DAYS` / `BACKUP_KEEP_LAST` | `14` / `3` | ротация |
| `CLEAN_PATTERN` / `CLEAN_DAYS` | `*.log` / `30` | очистка |
| `REPORT_DISK_PATH` | `/` | какой диск показывать в отчёте |
| `REPORT_MAX_BACKUP_AGE_HOURS` | `26` | порог «бэкап устарел» |
| `LOG_LEVEL`, `LOG_FILE` | `INFO`, — | логирование |

## Разработка

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -q
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

## Заказать похожее

Нужно парсить сайт по расписанию, выгружать данные в таблицу, делать бэкапы или получать отчёты в Telegram? Напишу скрипты под вашу задачу и настрою их на сервере.
**Заказать похожее: Telegram [@Vladimirk005kk](https://t.me/Vladimirk005kk)**

---

## English

**cron-automation-scripts** is a set of scheduled Python jobs with ready-made crontab and systemd timer files.

- `parse_to_csv`: a requests + BeautifulSoup scraper with pagination, retries and polite delays. It writes CSV/TSV ready for Google Sheets or Excel (optional BOM, atomic write).
- `backup_cleanup`: tar.gz backups with smart excludes, rotation (keep N days but always the last K archives), old-file cleanup, `--dry-run` and an optional Telegram summary.
- `report_to_telegram`: a daily report covering the latest scrape stats, backup freshness, disk usage and load average.
- Config lives in `.env`. Exit codes are cron-friendly, tests run offline with pytest, and the code is type-hinted and ruff-clean.

Quick start: `pip install -r requirements.txt && cp .env.example .env && python -m scripts.report_to_telegram --dry-run`

Contact for custom automation: Telegram [@Vladimirk005kk](https://t.me/Vladimirk005kk)

License: MIT © Vladimir Matusevichus
