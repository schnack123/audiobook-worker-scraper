"""webnovel.com (Qidian International) parser, ported from WebToEpub's
QidianParser.

The chapter list lives on `/book/<slug_id>/catalog`; locked (paid) chapters
are marked with a `#i-lock` svg and are skipped entirely. Description and
cover come from the book main page's og: meta tags (the catalog page lacks
them). Chapter pages render content into `div.chapter_content` - the browser
waits for `.cha-words` so the JSON fallback WebToEpub needs is unnecessary.
"""
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from parsers.base import ChapterRef, NovelInfo, Parser, ParserError

_BOOK_PATH = re.compile(r"(/book/[^/?#]+)")


class QidianParser(Parser):
    hosts = ("webnovel.com",)
    toc_ready_selector = "div.volume-item, ul.content-list"
    chapter_ready_selector = "div.chapter_content .cha-words"
    delay_range = (3.0, 6.0)

    def _book_url(self, novel_url: str) -> str:
        match = _BOOK_PATH.search(novel_url)
        if not match:
            raise ParserError(f"Not a webnovel.com book URL: {novel_url}")
        return urljoin(novel_url, match.group(1))

    def toc_url(self, novel_url: str) -> str:
        return self._book_url(novel_url) + "/catalog"

    def metadata_url(self, novel_url: str) -> str | None:
        return self._book_url(novel_url)

    def parse_novel(self, html: str, url: str) -> NovelInfo:
        soup = BeautifulSoup(html, "lxml")
        title_el = soup.select_one("h1")
        if title_el is None:
            raise ParserError(f"No novel title found on {url}")
        author_el = soup.select_one("a.c_primary")
        return NovelInfo(
            title=title_el.get_text(strip=True),
            author=author_el.get_text(strip=True) if author_el else None,
            chapters=self.parse_toc_page(html, url),
        )

    def parse_metadata(self, html: str, url: str, info: NovelInfo) -> None:
        soup = BeautifulSoup(html, "lxml")
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            info.cover_url = urljoin(url, og_image["content"])
        desc_el = soup.select_one("div.det-abt p.c_000")
        if desc_el is not None:
            info.description = desc_el.get_text("\n", strip=True)
        else:
            og_desc = soup.find("meta", property="og:description")
            if og_desc and og_desc.get("content"):
                info.description = og_desc["content"]

    def parse_toc_page(self, html: str, url: str) -> list[ChapterRef]:
        soup = BeautifulSoup(html, "lxml")
        links = soup.select("ul.content-list a")
        if not links:
            links = soup.select("div.volume-item ol a")
        refs = []
        for link in links:
            href = link.get("href")
            if not href:
                continue
            strong = link.select_one("strong")
            title = strong.get_text(strip=True) if strong else link.get_text(strip=True)
            num_el = link.select_one("i")
            if strong is not None and num_el is not None:
                num = num_el.get_text(strip=True)
                if num:
                    title = f"{num}: {title}"
            lock = link.select_one("svg use")
            locked = bool(lock) and (lock.get("xlink:href") or lock.get("href")) == "#i-lock"
            refs.append(ChapterRef(title=title, url=urljoin(url, href), locked=locked))
        return refs

    def parse_chapter(self, html: str, url: str) -> tuple[str | None, str]:
        soup = BeautifulSoup(html, "lxml")
        content = soup.select_one("div.chapter_content")
        if content is None:
            raise ParserError(f"No chapter content found on {url}")
        title_el = content.select_one("h1")
        title = title_el.get_text(strip=True) if title_el else None
        # Author notes, comment widgets, vote/score forms are not story text
        for junk in content.select(
            "div.m-thou, form.cha-score, div.cha-bts, div.user-links-wrap, div.tac, "
            "i.para-comment_num, i.para-comment, pirate, script, style"
        ):
            junk.decompose()
        words = content.select_one(".cha-words") or content
        paragraphs = [p.get_text(" ", strip=True) for p in words.find_all("p")]
        text = "\n\n".join(p for p in paragraphs if p)
        return title, text
