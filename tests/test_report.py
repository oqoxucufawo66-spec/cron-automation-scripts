import time
from pathlib import Path

from scripts.common import human_size, split_message
from scripts.parse_to_csv import Item, write_table
from scripts.report_to_telegram import BackupStats, backup_stats, build_report, csv_stats


def test_csv_stats(tmp_path: Path) -> None:
    path = tmp_path / "q.csv"
    write_table(
        [
            Item("t", "q1", "Alice", "python, zen", ""),
            Item("t", "q2", "Alice", "python", ""),
            Item("t", "q3", "Bob <b>", "", ""),
        ],
        path,
    )
    stats = csv_stats(path)
    assert stats.rows == 3
    assert stats.top_authors[0] == ("Alice", 2)
    assert stats.top_tags[0] == ("python", 2)


def test_build_report_escapes_html_and_flags_missing(tmp_path: Path) -> None:
    path = tmp_path / "q.csv"
    write_table([Item("t", "q", "Bob <b>", "", "")], path)
    report = build_report(
        csv_info=csv_stats(path),
        backups=backup_stats(tmp_path / "no-backups"),
        disk_path=tmp_path,
    )
    assert "Bob &lt;b&gt; (1)" in report
    assert "Архивов нет" in report
    assert "Диск" in report


def test_build_report_stale_backup(tmp_path: Path) -> None:
    now = time.time()
    backups = BackupStats(1, 2048, "backup_x.tar.gz", 2048, now - 3 * 86_400)
    report = build_report(csv_info=None, backups=backups, disk_path=tmp_path, now=now)
    assert "⚠️ Последний" in report and "3 дн назад" in report
    assert "не найден" in report


def test_helpers() -> None:
    assert human_size(512) == "512 B"
    assert human_size(1536) == "1.5 KB"
    chunks = split_message("line\n" * 2000, limit=100)
    assert all(len(c) <= 100 for c in chunks)
    assert "".join(chunks).count("line") == 2000
