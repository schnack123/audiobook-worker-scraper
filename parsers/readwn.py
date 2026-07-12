"""fanmtl.com parser (Readwn site family), ported from WebToEpub's ReadwnParser.

TOC pagination: the novel page shows the first chunk of chapters plus
`ul.pagination` links to `/e/extend/fy.php?page=N&wjm=<id>`. Page indices in
those links are 0-based relative to the novel page (link labeled "2" has
page=1), so fetching pages 1..max(page) after the novel page covers the full
list.
"""
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from parsers.base import ChapterRef, NovelInfo, Parser, ParserError


class ReadwnParser(Parser):
    hosts = ("fanmtl.com",)
    toc_ready_selector = "ul.chapter-list"
    chapter_ready_selector = "div.chapter-content"
    delay_range = (5.0, 10.0)

    def parse_novel(self, html: str, url: str) -> NovelInfo:
        soup = BeautifulSoup(html, "lxml")
        title_el = soup.select_one("div.main-head h1")
        if title_el is None:
            raise ParserError(f"No novel title found on {url}")
        author_el = soup.select_one("span[itemprop='author']")
        desc_el = soup.select_one("div.summary .content")
        cover_url = None
        img = soup.select_one("figure.cover img")
        if img is not None:
            src = img.get("data-src") or img.get("src")
            if src and "placeholder" not in src:
                cover_url = urljoin(url, src)
        return NovelInfo(
            title=title_el.get_text(strip=True),
            author=author_el.get_text(strip=True) if author_el else None,
            description=desc_el.get_text("\n", strip=True) if desc_el else None,
            cover_url=cover_url,
            chapters=self.parse_toc_page(html, url),
        )

    def extra_toc_urls(self, html: str, url: str) -> list[str]:
        soup = BeautifulSoup(html, "lxml")
        page_ids = []
        template = None
        for a in soup.select("ul.pagination li a"):
            link = urljoin(url, a.get("href", ""))
            params = parse_qs(urlparse(link).query)
            if "page" in params:
                page_ids.append(int(params["page"][0]))
                template = template or link
        if not page_ids or template is None:
            return []
        urls = []
        parts = urlparse(template)
        for i in range(1, max(page_ids) + 1):
            params = parse_qs(parts.query)
            params["page"] = [str(i)]
            urls.append(urlunparse(parts._replace(query=urlencode(params, doseq=True))))
        return urls

    def parse_toc_page(self, html: str, url: str) -> list[ChapterRef]:
        soup = BeautifulSoup(html, "lxml")
        refs = []
        for link in soup.select("ul.chapter-list a"):
            href = link.get("href")
            if not href:
                continue
            num_el = link.select_one(".chapter-no")
            title_el = link.select_one(".chapter-title")
            title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)
            if num_el is not None:
                num = num_el.get_text(strip=True)
                if num and num not in title:
                    title = f"{num}: {title}"
            refs.append(ChapterRef(title=title, url=urljoin(url, href)))
        return refs

    def parse_chapter(self, html: str, url: str) -> tuple[str | None, str]:
        soup = BeautifulSoup(html, "lxml")
        content = soup.select_one("div.chapter-content")
        if content is None:
            raise ParserError(f"No chapter content found on {url}")
        for junk in content.select(".adsbox, script, style, div[align='center']"):
            junk.decompose()
        title_el = soup.select_one("h2")
        title = title_el.get_text(strip=True) if title_el else None
        paragraphs = [p.get_text(" ", strip=True) for p in content.find_all("p")]
        text = "\n\n".join(p for p in paragraphs if p)
        if not text:
            # Some chapters are plain text nodes rather than <p> tags
            text = content.get_text("\n\n", strip=True)
        return title, text
