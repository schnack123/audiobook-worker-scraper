"""wtr-lab.com parser (Next.js machine-translation site).

Novel metadata is server-rendered into the page's __NEXT_DATA__ JSON. The
full chapter list comes from /api/chapters/{raw_id}, fetched through the
browser tab so it reuses the Cloudflare clearance (Chrome renders the JSON
response inside <body><pre>). The novel language + slug are smuggled to
parse_toc_page in the API URL's fragment (never sent to the server).

Chapter pages render the text client-side into div.wtr-line elements. We
always request the free "web" translation (?service=web) since the pipeline
does its own LLM cleaning. The reader preloads following chapters into the
same page, so extraction is scoped to the #chapter-{n} container.

WTR chapters are number-addressed (API `order`), so ChapterRef.number is set
and the handler can adopt chapter rows of pre-existing novels ingested via
the old WTR-LAB ingest worker (numbered rows without source_url).
"""
import json
import re
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup

from parsers.base import ChapterRef, NovelInfo, Parser, ParserError

_NOVEL_PATH = re.compile(r"^/([a-z]{2})/novel/(\d+)(?:/([^/?#]+))?")
_CHAPTER_NO = re.compile(r"/chapter-(\d+)")
# Rendered chapter titles look like "#12Actual title"
_TITLE_PREFIX = re.compile(r"^#\d+\s*")


def _split_novel_url(novel_url: str) -> tuple[str, str, str]:
    """-> (language, raw_id, slug)"""
    match = _NOVEL_PATH.match(urlparse(novel_url).path)
    if not match:
        raise ParserError(f"Not a wtr-lab.com novel URL: {novel_url}")
    lang, raw_id, slug = match.group(1), match.group(2), match.group(3) or "novel"
    return lang, raw_id, slug


class WtrLabParser(Parser):
    hosts = ("wtr-lab.com",)
    # Novel page is SSR (__NEXT_DATA__); extra TOC "page" is the API JSON in <pre>
    toc_ready_selector = "#__NEXT_DATA__, body > pre"
    chapter_ready_selector = "div.chapter-body div.wtr-line"
    delay_range = (4.0, 8.0)
    # wtr-lab serves ~5 web-translation chapters per client, then renders
    # pages without text until the client cools down. Runs that resumed after
    # a 12-18 min idle gap reliably got another batch, while 5-11 min waits
    # did not, so wait out one long fully-idle cooldown before retrying.
    timeout_backoffs = (900.0,)

    def toc_url(self, novel_url: str) -> str:
        lang, raw_id, slug = _split_novel_url(novel_url)
        return f"https://wtr-lab.com/{lang}/novel/{raw_id}/{slug}"

    def chapter_ready(self, chapter_url: str) -> str:
        match = _CHAPTER_NO.search(chapter_url)
        if match:  # the infinite reader preloads other chapters too
            return f"#chapter-{match.group(1)} div.chapter-body div.wtr-line"
        return self.chapter_ready_selector

    def chapter_is_ready(self, html: str, url: str) -> bool:
        """The reader inserts the chapter container (and empty/placeholder
        wtr-line divs) before the decrypted text arrives, so the ready
        selector alone can match a still-loading chapter."""
        try:
            return bool(self.parse_chapter(html, url)[1].strip())
        except ParserError:
            return False

    def _next_data(self, html: str, url: str) -> dict:
        soup = BeautifulSoup(html, "lxml")
        script = soup.select_one("script#__NEXT_DATA__")
        if script is None or not script.string:
            raise ParserError(f"No __NEXT_DATA__ on {url} (not rendered?)")
        return json.loads(script.string)

    def parse_novel(self, html: str, url: str) -> NovelInfo:
        props = self._next_data(html, url)["props"]["pageProps"]
        serie = props.get("serie", {}).get("serie_data", {})
        data = serie.get("data", {})
        if not data.get("title"):
            raise ParserError(f"No novel title in __NEXT_DATA__ on {url}")
        return NovelInfo(
            title=data["title"],
            author=data.get("author") or serie.get("author"),
            description=data.get("description"),
            cover_url=data.get("image"),
            chapters=[],  # full list comes from the chapters API (extra_toc_urls)
        )

    def extra_toc_urls(self, html: str, url: str) -> list[str]:
        lang, raw_id, slug = _split_novel_url(url)
        return [f"https://wtr-lab.com/api/chapters/{raw_id}#{lang}/{slug}"]

    def parse_toc_page(self, html: str, url: str) -> list[ChapterRef]:
        parts = urlparse(url)
        api_match = re.match(r"^/api/chapters/(\d+)$", parts.path)
        if not api_match or not parts.fragment:
            raise ParserError(f"Unexpected wtr-lab TOC page URL: {url}")
        raw_id = api_match.group(1)
        lang, slug = unquote(parts.fragment).split("/", 1)

        soup = BeautifulSoup(html, "lxml")
        pre = soup.select_one("pre")
        if pre is None:
            raise ParserError(f"No JSON <pre> body on {url}")
        chapters = json.loads(pre.get_text()).get("chapters", [])

        refs = []
        for ch in chapters:
            order = ch.get("order")
            if not order:
                continue
            refs.append(
                ChapterRef(
                    title=ch.get("title") or f"Chapter {order}",
                    url=(
                        f"https://wtr-lab.com/{lang}/novel/{raw_id}/{slug}"
                        f"/chapter-{order}?service=web"
                    ),
                    number=int(order),
                )
            )
        return refs

    def parse_chapter(self, html: str, url: str) -> tuple[str | None, str]:
        match = _CHAPTER_NO.search(url)
        soup = BeautifulSoup(html, "lxml")
        container = soup.select_one(f"#chapter-{match.group(1)}") if match else None
        if container is None:
            raise ParserError(f"No chapter container found on {url}")
        lines = [
            line.get_text(" ", strip=True)
            for line in container.select("div.chapter-body div.wtr-line")
        ]
        text = "\n\n".join(line for line in lines if line)
        if not text:
            raise ParserError(f"No chapter text found on {url}")
        title = None
        title_el = container.select_one("span.text-2xl")
        if title_el is not None:
            title = _TITLE_PREFIX.sub("", title_el.get_text(strip=True)).strip() or None
        return title, text
