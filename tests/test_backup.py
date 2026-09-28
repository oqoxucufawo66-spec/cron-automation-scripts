import os
import tarfile
import time
from datetime import datetime
from pathlib import Path

from scripts.backup_cleanup import clean_old_files, create_archive, main, rotate_archives

DAY = 86_400


def _touch(path: Path, age_days: float, content: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    ts = time.time() - age_days * DAY
    os.utime(path, (ts, ts))
    return path


def test_create_archive_excludes_junk_and_nested_backup_dir(tmp_path: Path) -> None:
    src = tmp_path / "site"
    _touch(src / "index.html", 0)
    _touch(src / "__pycache__" / "x.pyc", 0)
    _touch(src / "backups" / "old.tar.gz", 0)  # dest inside source
    archive = create_archive([src], src / "backups", now=datetime(2026, 9, 28, 3, 30))
    assert archive.name == "backup_20260928_033000.tar.gz"
    with tarfile.open(archive) as tar:
        names = tar.getnames()
    assert "site/index.html" in names
    assert not any("__pycache__" in n or n.startswith("site/backups") for n in names)


def test_create_archive_dry_run_writes_nothing(tmp_path: Path) -> None:
    src = tmp_path / "data"
    _touch(src / "a.txt", 0)
    archive = create_archive([src], tmp_path / "backups", dry_run=True)
    assert not archive.exists() and not (tmp_path / "backups").exists()


def test_rotate_keeps_last_n_even_if_old(tmp_path: Path) -> None:
    for i, age in enumerate([1, 20, 30, 40]):
        _touch(tmp_path / f"backup_2026090{i}_000000.tar.gz", age)
    _touch(tmp_path / "unrelated.tar.gz", 100)
    removed = rotate_archives(tmp_path, keep_days=14, keep_last=2)
    assert sorted(r.path.name for r in removed) == [
        "backup_20260902_000000.tar.gz",
        "backup_20260903_000000.tar.gz",
    ]
    assert (tmp_path / "unrelated.tar.gz").exists()
    assert len(list(tmp_path.glob("backup_*"))) == 2


def test_clean_old_files(tmp_path: Path) -> None:
    old = _touch(tmp_path / "logs" / "old.log", 40, "12345")
    new = _touch(tmp_path / "logs" / "new.log", 1)
    other = _touch(tmp_path / "logs" / "keep.txt", 40)
    dry = clean_old_files(tmp_path / "logs", pattern="*.log", older_than_days=30, dry_run=True)
    assert [r.path for r in dry] == [old] and old.exists()
    removed = clean_old_files(tmp_path / "logs", pattern="*.log", older_than_days=30)
    assert [r.size for r in removed] == [5]
    assert not old.exists() and new.exists() and other.exists()


def test_main_end_to_end(tmp_path: Path) -> None:
    src = tmp_path / "data"
    _touch(src / "file.txt", 0)
    dest = tmp_path / "bk"
    assert main(["--source", str(src), "--dest", str(dest)]) == 0
    assert len(list(dest.glob("backup_*.tar.gz"))) == 1
    assert main(["--source", str(tmp_path / "missing"), "--dest", str(dest)]) == 1
