import csv
from pathlib import Path

from scripts.parse_to_csv import FIELDS, Item, parse_page, write_table

FIXTURE = Path(__file__).parent / "fixtures" / "quotes_page.html"
BASE = "https://quotes.toscrape.com/"


def test_parse_page_extracts_items_and_next_link() -> None:
    items, next_url = parse_page(
        FIXTURE.read_text(encoding="utf-8"), BASE, "2026-09-28T07:00:00+03:00"
    )
    assert len(items) == 3  # broken block is skipped
    first = items[0]
    assert first.text == "Simple is better than complex."
    assert first.author == "Tim Peters"
    assert first.tags == "python, zen"
    assert first.author_url == "https://quotes.toscrape.com/author/Tim-Peters"
    assert items[2].author_url == "" and items[2].tags == ""
    assert next_url == "https://quotes.toscrape.com/page/2/"


def test_parse_page_last_page_has_no_next() -> None:
    items, next_url = parse_page("<html><body></body></html>", BASE, "t")
    assert items == [] and next_url is None


def test_write_csv_roundtrip_with_special_chars(tmp_path: Path) -> None:
    items, _ = parse_page(FIXTURE.read_text(encoding="utf-8"), BASE, "t")
    out = tmp_path / "out" / "quotes.csv"
    write_table(items, out, excel=True)
    raw = out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # BOM for Excel
    with out.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert list(rows[0]) == FIELDS
    assert rows[1]["text"] == 'Код, запятые, "кавычки" — всё экранируется.'
    assert rows[1]["author"] == "Тестовый Автор"


def test_write_tsv(tmp_path: Path) -> None:
    out = tmp_path / "quotes.tsv"
    write_table([Item("t", "a\tb", "x", "", "")], out, fmt="tsv")
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0].split("\t") == FIELDS
    assert '"a\tb"' in lines[1]  # tab inside a value is quoted
