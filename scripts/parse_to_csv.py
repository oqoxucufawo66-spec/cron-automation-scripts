"""Web parser: requests + BeautifulSoup -> CSV/TSV ready for Google Sheets / Excel.

Demo target is https://quotes.toscrape.com, a public sandbox made for scraping practice.
Adapt ``parse_page`` and ``FIELDS`` to your site.

Usage:
    python -m scripts.parse_to_csv                      # all pages -> output/quotes_YYYY-MM-DD.csv
    python -m scripts.parse_to_csv --max-pages 2 --format tsv
    python -m scripts.parse_to_csv --excel -o output/quotes.csv   # UTF-8 BOM for Excel
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from scripts.common import load_env, resolve_path, setup_logging

logger = logging.getLogger("scripts.parse_to_csv")

DEFAULT_URL = "https://quotes.toscrape.com/"
USER_AGENT = (
    "Mozilla/5.0 (compatible; cron-automation-scripts/1.0; +https://github.com/oqoxucufawo66-spec)"
)


@dataclass(frozen=True, slots=True)
class Item:
    scraped_at: str
    text: str
    author: str
    tags: str
    author_url: str


FIELDS = [f.name for f in fields(Item)]


def make_session(retries: int = 3) -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en,ru;q=0.8"})
    retry = Retry(
        total=retries,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def parse_page(html: str, base_url: str, scraped_at: str) -> tuple[list[Item], str | None]:
    """Return items from one page and the absolute URL of the next page (if any)."""
    soup = BeautifulSoup(html, "html.parser")
    items: list[Item] = []
    for block in soup.select("div.quote"):
        text_el = block.select_one("span.text")
        author_el = block.select_one("small.author")
        if text_el is None or author_el is None:
            continue
        link = block.select_one("a[href^='/author/']")
        tags = [a.get_text(strip=True) for a in block.select("div.tags a.tag")]
        items.append(
            Item(
                scraped_at=scraped_at,
                text=text_el.get_text(strip=True).strip('“”"'),
                author=author_el.get_text(strip=True),
                tags=", ".join(tags),
                author_url=urljoin(base_url, str(link["href"])) if link else "",
            )
        )
    next_link = soup.select_one("li.next a[href]")
    next_url = urljoin(base_url, str(next_link["href"])) if next_link else None
    return items, next_url


def crawl(
    start_url: str,
    *,
    session: requests.Session,
    max_pages: int = 0,
    delay: float = 1.0,
    timeout: float = 20,
) -> Iterator[Item]:
    """Follow pagination starting from ``start_url``. ``max_pages=0`` means no limit."""
    scraped_at = datetime.now().astimezone().isoformat(timespec="seconds")
    url: str | None = start_url
    page = 0
    seen: set[str] = set()
    while url and url not in seen and (max_pages <= 0 or page < max_pages):
        seen.add(url)
        page += 1
        logger.info("Page %d: %s", page, url)
        response = session.get(url, timeout=timeout)
        response.raise_for_status()
        items, url = parse_page(response.text, response.url, scraped_at)
        yield from items
        if url and delay > 0:
            time.sleep(delay)  # be polite to the server


def write_table(items: list[Item], path: Path, *, fmt: str = "csv", excel: bool = False) -> None:
    """Write rows with a header. CSV (comma) or TSV (paste straight into Google Sheets)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    encoding = "utf-8-sig" if excel else "utf-8"
    delimiter = "\t" if fmt == "tsv" else ","
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding=encoding) as fh:
        writer = csv.DictWriter(
            fh, fieldnames=FIELDS, delimiter=delimiter, quoting=csv.QUOTE_MINIMAL
        )
        writer.writeheader()
        for item in items:
            writer.writerow(asdict(item))
    tmp.replace(path)  # atomic: readers never see a half-written file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--url", default=os.getenv("PARSER_START_URL", DEFAULT_URL))
    parser.add_argument("-o", "--output", help="Output file (default: output/quotes_<date>.<fmt>)")
    parser.add_argument("--format", choices=("csv", "tsv"), default="csv")
    parser.add_argument(
        "--excel", action="store_true", help="UTF-8 with BOM so Excel shows Cyrillic"
    )
    parser.add_argument("--max-pages", type=int, default=int(os.getenv("PARSER_MAX_PAGES", "0")))
    parser.add_argument("--delay", type=float, default=float(os.getenv("PARSER_DELAY", "1.0")))
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    load_env()
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)

    output_dir = os.getenv("PARSER_OUTPUT_DIR", "output")
    default_name = f"quotes_{datetime.now():%Y-%m-%d}.{args.format}"
    output = resolve_path(args.output or f"{output_dir}/{default_name}")
    try:
        items = list(
            crawl(args.url, session=make_session(), max_pages=args.max_pages, delay=args.delay)
        )
    except requests.RequestException as exc:
        logger.error("Parsing failed: %s", exc)
        return 1
    if not items:
        logger.error("No items found - page layout may have changed")
        return 2
    write_table(items, output, fmt=args.format, excel=args.excel)
    logger.info("Saved %d rows to %s", len(items), output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
