"""Backup + cleanup: tar.gz archive of folders, rotation of old archives, removal of old files.

Usage:
    python -m scripts.backup_cleanup --source ./data --dest ./backups
    python -m scripts.backup_cleanup --source /var/www/site --source /etc/nginx \\
        --dest /backups --keep-days 14 --keep-last 5
    python -m scripts.backup_cleanup --no-backup --clean ./logs \\
        --clean-pattern "*.log" --clean-days 30
    python -m scripts.backup_cleanup ... --dry-run --notify
"""

from __future__ import annotations

import argparse
import fnmatch
import logging
import os
import tarfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from scripts.common import (
    human_size,
    load_env,
    resolve_path,
    send_telegram,
    setup_logging,
    telegram_configured,
)

logger = logging.getLogger("scripts.backup_cleanup")

ARCHIVE_PREFIX = "backup_"
ARCHIVE_SUFFIX = ".tar.gz"
DEFAULT_EXCLUDES = ("__pycache__", "*.pyc", ".git", ".venv", "node_modules", "*.tmp")
DAY = 86_400


@dataclass(frozen=True, slots=True)
class Removed:
    path: Path
    size: int


def _remove(path: Path, dry_run: bool) -> Removed:
    size = path.stat().st_size
    if not dry_run:
        path.unlink()
    return Removed(path, size)


@dataclass
class Summary:
    archive: Path | None = None
    archive_size: int = 0
    rotated: list[Removed] = field(default_factory=list)
    cleaned: list[Removed] = field(default_factory=list)

    @property
    def freed_bytes(self) -> int:
        return sum(r.size for r in (*self.rotated, *self.cleaned))

    def as_text(self, dry_run: bool = False) -> str:
        prefix = "[DRY RUN] " if dry_run else ""
        lines = [f"{prefix}🗄 Бэкап и очистка"]
        if self.archive:
            size = "не создан, dry run" if dry_run else human_size(self.archive_size)
            lines.append(f"Архив: {self.archive.name} ({size})")
        lines.append(f"Удалено старых архивов: {len(self.rotated)}")
        lines.append(f"Удалено старых файлов: {len(self.cleaned)}")
        freed = "Будет освобождено" if dry_run else "Освобождено"
        lines.append(f"{freed}: {human_size(self.freed_bytes)}")
        return "\n".join(lines)


def _excluded(name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


def create_archive(
    sources: list[Path],
    dest_dir: Path,
    *,
    excludes: tuple[str, ...] = DEFAULT_EXCLUDES,
    now: datetime | None = None,
    dry_run: bool = False,
) -> Path:
    """Pack ``sources`` into ``dest_dir/backup_YYYYmmdd_HHMMSS.tar.gz``."""
    missing = [str(s) for s in sources if not s.exists()]
    if missing:
        raise FileNotFoundError(f"Source not found: {', '.join(missing)}")
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    archive = dest_dir / f"{ARCHIVE_PREFIX}{stamp}{ARCHIVE_SUFFIX}"
    if dry_run:
        logger.info("[dry-run] would create %s from %s", archive, ", ".join(map(str, sources)))
        return archive

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_resolved = dest_dir.resolve()

    # If the backup folder lives inside a source, skip it (no archives inside archives).
    skip_arcnames: set[str] = set()
    for source in sources:
        src = source.resolve()
        if dest_resolved.is_relative_to(src):
            skip_arcnames.add((Path(src.name) / dest_resolved.relative_to(src)).as_posix())

    def _filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        if _excluded(Path(info.name).name, excludes):
            return None
        if any(info.name == p or info.name.startswith(p + "/") for p in skip_arcnames):
            return None
        return info

    tmp = archive.with_name(archive.name + ".part")
    try:
        with tarfile.open(tmp, "w:gz") as tar:
            for source in sources:
                tar.add(source, arcname=source.resolve().name, filter=_filter)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(archive)
    logger.info("Created %s (%s)", archive, human_size(archive.stat().st_size))
    return archive


def rotate_archives(
    dest_dir: Path,
    *,
    keep_days: int,
    keep_last: int,
    now: float | None = None,
    dry_run: bool = False,
) -> list[Removed]:
    """Delete archives older than ``keep_days``, but always keep the ``keep_last`` newest."""
    if not dest_dir.is_dir():
        return []
    now = time.time() if now is None else now
    archives = sorted(
        (p for p in dest_dir.glob(f"{ARCHIVE_PREFIX}*{ARCHIVE_SUFFIX}") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    removed: list[Removed] = []
    for archive in archives[keep_last:]:
        if now - archive.stat().st_mtime > keep_days * DAY:
            logger.info("%sRemove old archive %s", "[dry-run] " if dry_run else "", archive.name)
            removed.append(_remove(archive, dry_run))
    return removed


def clean_old_files(
    directory: Path,
    *,
    pattern: str,
    older_than_days: int,
    now: float | None = None,
    dry_run: bool = False,
) -> list[Removed]:
    """Recursively delete files matching ``pattern`` older than N days. Empty dirs stay."""
    if not directory.is_dir():
        logger.warning("Clean directory does not exist: %s", directory)
        return []
    now = time.time() if now is None else now
    removed: list[Removed] = []
    for path in directory.rglob(pattern):
        if (
            path.is_file()
            and not path.is_symlink()
            and now - path.stat().st_mtime > older_than_days * DAY
        ):
            logger.debug("%sRemove %s", "[dry-run] " if dry_run else "", path)
            removed.append(_remove(path, dry_run))
    return removed


def build_parser() -> argparse.ArgumentParser:
    env_sources = [s for s in os.getenv("BACKUP_SOURCES", "").split(",") if s.strip()]
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--source", action="append", help="Folder/file to back up (repeatable)")
    parser.add_argument("--dest", default=os.getenv("BACKUP_DEST", "backups"))
    parser.add_argument("--keep-days", type=int, default=int(os.getenv("BACKUP_KEEP_DAYS", "14")))
    parser.add_argument("--keep-last", type=int, default=int(os.getenv("BACKUP_KEEP_LAST", "3")))
    parser.add_argument("--no-backup", action="store_true", help="Only rotate/clean")
    parser.add_argument("--clean", action="append", default=[], help="Folder to clean (repeatable)")
    parser.add_argument("--clean-pattern", default=os.getenv("CLEAN_PATTERN", "*.log"))
    parser.add_argument("--clean-days", type=int, default=int(os.getenv("CLEAN_DAYS", "30")))
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done")
    parser.add_argument("--notify", action="store_true", help="Send summary to Telegram")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.set_defaults(env_sources=env_sources)
    return parser


def main(argv: list[str] | None = None) -> int:
    load_env()
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)

    dest = resolve_path(args.dest)
    summary = Summary()
    try:
        if not args.no_backup:
            sources = [resolve_path(s.strip()) for s in (args.source or args.env_sources)]
            if not sources:
                logger.error("Nothing to back up: pass --source or set BACKUP_SOURCES")
                return 2
            summary.archive = create_archive(sources, dest, dry_run=args.dry_run)
            if summary.archive.exists():
                summary.archive_size = summary.archive.stat().st_size

        summary.rotated = rotate_archives(
            dest, keep_days=args.keep_days, keep_last=max(1, args.keep_last), dry_run=args.dry_run
        )
        for directory in args.clean:
            summary.cleaned += clean_old_files(
                resolve_path(directory),
                pattern=args.clean_pattern,
                older_than_days=args.clean_days,
                dry_run=args.dry_run,
            )
    except (OSError, tarfile.TarError) as exc:
        logger.exception("Backup failed")
        if args.notify and telegram_configured():
            send_telegram(f"❌ Бэкап не выполнен: {exc}", parse_mode=None)
        return 1

    text = summary.as_text(args.dry_run)
    logger.info(text.replace("\n", " | "))
    if args.notify:
        if telegram_configured():
            send_telegram(text, parse_mode=None)
        else:
            logger.warning("--notify given but TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID are not set")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
