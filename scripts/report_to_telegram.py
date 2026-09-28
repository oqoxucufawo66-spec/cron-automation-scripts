"""Daily report to Telegram: parser results, backups status, disk usage.

Usage:
    python -m scripts.report_to_telegram --dry-run        # print the report, send nothing
    python -m scripts.report_to_telegram                  # send to TELEGRAM_CHAT_ID
    python -m scripts.report_to_telegram --csv output/quotes_2026-09-28.csv
"""

from __future__ import annotations

import argparse
import csv
import html
import logging
import os
import shutil
import socket
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from scripts.backup_cleanup import ARCHIVE_PREFIX, ARCHIVE_SUFFIX
from scripts.common import (
    human_size,
    load_env,
    resolve_path,
    send_telegram,
    setup_logging,
    telegram_configured,
)

logger = logging.getLogger("scripts.report_to_telegram")


@dataclass(frozen=True, slots=True)
class CsvStats:
    path: Path
    rows: int
    top_authors: list[tuple[str, int]]
    top_tags: list[tuple[str, int]]
    modified: float


@dataclass(frozen=True, slots=True)
class BackupStats:
    count: int
    total_size: int
    latest_name: str | None
    latest_size: int
    latest_mtime: float | None


def latest_file(directory: Path, pattern: str) -> Path | None:
    files = [p for p in directory.glob(pattern) if p.is_file()] if directory.is_dir() else []
    return max(files, key=lambda p: p.stat().st_mtime, default=None)


def csv_stats(path: Path, top: int = 5) -> CsvStats:
    sample = path.read_text(encoding="utf-8-sig")[:2048]
    delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
    authors: Counter[str] = Counter()
    tags: Counter[str] = Counter()
    rows = 0
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh, delimiter=delimiter):
            rows += 1
            if author := (row.get("author") or "").strip():
                authors[author] += 1
            for tag in (row.get("tags") or "").split(","):
                if tag := tag.strip():
                    tags[tag] += 1
    return CsvStats(
        path=path,
        rows=rows,
        top_authors=authors.most_common(top),
        top_tags=tags.most_common(top),
        modified=path.stat().st_mtime,
    )


def backup_stats(directory: Path) -> BackupStats:
    archives = (
        [p for p in directory.glob(f"{ARCHIVE_PREFIX}*{ARCHIVE_SUFFIX}") if p.is_file()]
        if directory.is_dir()
        else []
    )
    latest = max(archives, key=lambda p: p.stat().st_mtime, default=None)
    return BackupStats(
        count=len(archives),
        total_size=sum(p.stat().st_size for p in archives),
        latest_name=latest.name if latest else None,
        latest_size=latest.stat().st_size if latest else 0,
        latest_mtime=latest.stat().st_mtime if latest else None,
    )


def ago(timestamp: float, now: float | None = None) -> str:
    seconds = max(0, int((time.time() if now is None else now) - timestamp))
    if seconds < 3600:
        return f"{seconds // 60} мин назад"
    if seconds < 86_400:
        return f"{seconds // 3600} ч назад"
    return f"{seconds // 86_400} дн назад"


def build_report(
    *,
    csv_info: CsvStats | None,
    backups: BackupStats,
    disk_path: Path,
    max_backup_age_hours: float = 26,
    now: float | None = None,
) -> str:
    now = time.time() if now is None else now
    e = html.escape
    lines = [
        f"📋 <b>Ежедневный отчёт</b> — {datetime.fromtimestamp(now):%d.%m.%Y %H:%M}",
        f"Сервер: <code>{e(socket.gethostname())}</code>",
        "",
        "<b>🕸 Парсер</b>",
    ]
    if csv_info is None:
        lines.append("⚠️ Файл с результатами не найден")
    else:
        lines.append(f"Файл: <code>{e(csv_info.path.name)}</code> ({ago(csv_info.modified, now)})")
        lines.append(f"Строк: <b>{csv_info.rows}</b>")
        if csv_info.top_authors:
            authors = ", ".join(f"{e(name)} ({n})" for name, n in csv_info.top_authors)
            lines.append(f"Топ авторов: {authors}")
        if csv_info.top_tags:
            lines.append("Топ тегов: " + ", ".join(f"#{e(t)} ({n})" for t, n in csv_info.top_tags))

    lines += ["", "<b>🗄 Бэкапы</b>"]
    if backups.latest_name is None or backups.latest_mtime is None:
        lines.append("⚠️ Архивов нет")
    else:
        stale = now - backups.latest_mtime > max_backup_age_hours * 3600
        icon = "⚠️" if stale else "✅"
        lines.append(
            f"{icon} Последний: <code>{e(backups.latest_name)}</code>, "
            f"{human_size(backups.latest_size)}, {ago(backups.latest_mtime, now)}"
        )
        lines.append(f"Всего архивов: {backups.count} ({human_size(backups.total_size)})")

    usage = shutil.disk_usage(disk_path)
    percent = usage.used / usage.total * 100 if usage.total else 0
    icon = "🔴" if percent >= 90 else "🟡" if percent >= 80 else "🟢"
    lines += [
        "",
        "<b>💾 Диск</b>",
        f"{icon} Занято {percent:.0f}%: {human_size(usage.used)} из {human_size(usage.total)}, "
        f"свободно {human_size(usage.free)}",
    ]
    if hasattr(os, "getloadavg"):
        load1, load5, load15 = os.getloadavg()
        lines.append(f"Load average: {load1:.2f} / {load5:.2f} / {load15:.2f}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--csv", help="CSV/TSV to summarize (default: newest in PARSER_OUTPUT_DIR)")
    parser.add_argument("--backups", default=os.getenv("BACKUP_DEST", "backups"))
    parser.add_argument("--disk", default=os.getenv("REPORT_DISK_PATH", "/"))
    parser.add_argument(
        "--max-backup-age",
        type=float,
        default=float(os.getenv("REPORT_MAX_BACKUP_AGE_HOURS", "26")),
    )
    parser.add_argument("--dry-run", action="store_true", help="Print instead of sending")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    load_env()
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)

    if args.csv:
        csv_path: Path | None = resolve_path(args.csv)
    else:
        output_dir = resolve_path(os.getenv("PARSER_OUTPUT_DIR", "output"))
        csv_path = latest_file(output_dir, "*.csv") or latest_file(output_dir, "*.tsv")

    csv_info = None
    if csv_path is not None and csv_path.is_file():
        try:
            csv_info = csv_stats(csv_path)
        except (OSError, csv.Error, UnicodeDecodeError):
            logger.exception("Cannot read %s", csv_path)

    report = build_report(
        csv_info=csv_info,
        backups=backup_stats(resolve_path(args.backups)),
        disk_path=Path(args.disk),
        max_backup_age_hours=args.max_backup_age,
    )

    if args.dry_run:
        print(report)
        return 0
    if not telegram_configured():
        logger.error("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env (or use --dry-run)")
        print(report)
        return 2
    try:
        send_telegram(report)
    except Exception:
        logger.exception("Failed to send report")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
