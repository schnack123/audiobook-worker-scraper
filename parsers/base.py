"""Parser interface for browser-scraped novel sites.

Parsers are pure HTML-in / data-out (ported from WebToEpub's site parsers),
so they are unit-testable against saved fixtures. All fetching is done by the
handler via browser.py; parsers only tell it which URLs to fetch and how to
know a page finished rendering (ready_selector).
"""
from dataclasses import dataclass, field


class ParserError(Exception):
    pass


@dataclass
class ChapterRef:
    """One chapter as listed on the site's table of contents, in TOC order."""
    title: str
    url: str
    locked: bool = False  # paid/locked chapters are skipped entirely


@dataclass
class NovelInfo:
    title: str
    author: str | None = None
    description: str | None = None
    cover_url: str | None = None
    chapters: list[ChapterRef] = field(default_factory=list)


class Parser:
    # Hostname suffixes this parser handles (matched against the URL host)
    hosts: tuple[str, ...] = ()
    # CSS selector that signals the TOC / chapter page finished rendering
    toc_ready_selector: str = "body"
    chapter_ready_selector: str = "body"
    # Seconds to sleep between chapter fetches (min, max)
    delay_range: tuple[float, float] = (5.0, 10.0)

    def toc_url(self, novel_url: str) -> str:
        """URL of the page holding the chapter list (defaults to the novel URL)."""
        return novel_url

    def chapter_ready(self, chapter_url: str) -> str:
        """Ready selector for one chapter page (override when it depends on
        the URL, e.g. infinite readers that preload neighboring chapters)."""
        return self.chapter_ready_selector

    def chapter_is_ready(self, html: str, url: str) -> bool:
        """Content-level readiness check, polled after the ready selector
        matches. Override for SPAs whose skeleton DOM already satisfies the
        selector (empty placeholder lines etc.)."""
        return True

    def metadata_url(self, novel_url: str) -> str | None:
        """Extra page to fetch for description/cover when the TOC page lacks
        them (None = TOC page has everything)."""
        return None

    def parse_novel(self, html: str, url: str) -> NovelInfo:
        """Title/author/description/cover + the chapters on this TOC page."""
        raise NotImplementedError

    def parse_metadata(self, html: str, url: str, info: NovelInfo) -> None:
        """Fill description/cover from the metadata_url page (in place)."""

    def extra_toc_urls(self, html: str, url: str) -> list[str]:
        """URLs of additional TOC pages (paginated chapter lists)."""
        return []

    def parse_toc_page(self, html: str, url: str) -> list[ChapterRef]:
        raise NotImplementedError

    def parse_chapter(self, html: str, url: str) -> tuple[str | None, str]:
        """Returns (chapter_title, plain_text)."""
        raise NotImplementedError
