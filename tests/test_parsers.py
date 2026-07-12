"""Parser unit tests against saved HTML fixtures of the two reference novels:

- fanmtl: https://www.fanmtl.com/novel/kks23487.html
- webnovel: https://www.webnovel.com/book/...36129432200017805 (+/catalog)
"""
from pathlib import Path

import pytest

from parsers import SUPPORTED_HOSTS, ParserError, get_parser
from parsers.qidian import QidianParser
from parsers.readwn import ReadwnParser

FIXTURES = Path(__file__).parent / "fixtures"

FANMTL_URL = "https://www.fanmtl.com/novel/kks23487.html"
WEBNOVEL_URL = "https://www.webnovel.com/book/star-wars-the-chosen-one's-endless-grind_36129432200017805"


def read(name: str) -> str:
    return (FIXTURES / name).read_text()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_registry():
    assert isinstance(get_parser(FANMTL_URL), ReadwnParser)
    assert isinstance(get_parser("https://fanmtl.com/novel/x.html"), ReadwnParser)
    assert isinstance(get_parser(WEBNOVEL_URL), QidianParser)
    assert set(SUPPORTED_HOSTS) == {"fanmtl.com", "webnovel.com"}
    with pytest.raises(ParserError):
        get_parser("https://example.com/novel/1")


# ---------------------------------------------------------------------------
# fanmtl (Readwn)
# ---------------------------------------------------------------------------

def test_fanmtl_parse_novel():
    parser = ReadwnParser()
    info = parser.parse_novel(read("fanmtl_novel.html"), FANMTL_URL)
    assert info.title == "Wizard: Starting with Dealing with Magical Plants"
    assert info.author == "佚名"
    assert info.description and "wizards" in info.description
    assert info.cover_url == "https://www.fanmtl.com/d/file/kk101/llssednawgl.jpg"
    assert len(info.chapters) > 0
    first = info.chapters[0]
    assert first.url == "https://www.fanmtl.com/novel/kks23487_1.html"
    assert "Chapter 1" in first.title
    assert not any(c.locked for c in info.chapters)


def test_fanmtl_extra_toc_urls():
    parser = ReadwnParser()
    urls = parser.extra_toc_urls(read("fanmtl_novel.html"), FANMTL_URL)
    # Pagination links on the novel page are 0-indexed: max page id is 1
    assert urls == ["https://www.fanmtl.com/e/extend/fy.php?page=1&wjm=kks23487"]


def test_fanmtl_toc_page2():
    parser = ReadwnParser()
    refs = parser.parse_toc_page(
        read("fanmtl_toc_page2.html"),
        "https://www.fanmtl.com/e/extend/fy.php?page=1&wjm=kks23487",
    )
    assert refs[0].url == "https://www.fanmtl.com/novel/kks23487_102.html"
    assert refs[-1].url.endswith("_195.html")
    assert len(refs) == 94  # chapters 102-195


def test_fanmtl_full_toc_has_no_duplicates():
    parser = ReadwnParser()
    refs = parser.parse_toc_page(read("fanmtl_novel.html"), FANMTL_URL)
    refs += parser.parse_toc_page(read("fanmtl_toc_page2.html"), FANMTL_URL)
    # The site lists 194 chapters (slug 80 is skipped on fanmtl itself)
    assert len(refs) == len({r.url for r in refs}) == 194


def test_fanmtl_parse_chapter():
    parser = ReadwnParser()
    title, text = parser.parse_chapter(
        read("fanmtl_chapter.html"), "https://www.fanmtl.com/novel/kks23487_1.html"
    )
    assert title == "Chapter 1 The Wizarding World, [The Highly Effective Scholar]"
    assert text.startswith("Alvaro continent")
    assert len(text) > 1000
    assert "script" not in text.lower() or "javascript" not in text.lower()
    assert "\n\n" in text  # paragraph separation


# ---------------------------------------------------------------------------
# webnovel (Qidian)
# ---------------------------------------------------------------------------

def test_qidian_urls():
    parser = QidianParser()
    expected = (
        "https://www.webnovel.com/book/"
        "star-wars-the-chosen-one's-endless-grind_36129432200017805"
    )
    assert parser.toc_url(WEBNOVEL_URL) == expected + "/catalog"
    assert parser.toc_url(expected + "/catalog") == expected + "/catalog"
    assert parser.metadata_url(WEBNOVEL_URL) == expected


def test_qidian_parse_novel():
    parser = QidianParser()
    info = parser.parse_novel(read("webnovel_catalog.html"), WEBNOVEL_URL + "/catalog")
    assert info.title == "Star Wars: The Chosen One's Endless Grind"
    assert info.author == "whitedeath0"
    assert len(info.chapters) == 25  # 2 auxiliary + 23 numbered
    # Auxiliary-volume chapters have no number prefix
    assert info.chapters[0].title == "Anakin's skill description"
    assert info.chapters[2].title == "1: The First Awakening"
    assert info.chapters[2].url.endswith("/the-first-awakening_96997669322410840")
    assert not any(c.locked for c in info.chapters)  # this book is all free


def test_qidian_parse_metadata():
    parser = QidianParser()
    info = parser.parse_novel(read("webnovel_catalog.html"), WEBNOVEL_URL + "/catalog")
    parser.parse_metadata(read("webnovel_book.html"), WEBNOVEL_URL, info)
    assert info.cover_url and "book-pic.webnovel.com/bookcover/36129432200017805" in info.cover_url
    assert info.description and info.description.startswith("A lifelong Star Wars fan")


def test_qidian_locked_chapter_detection():
    parser = QidianParser()
    html = """
    <div class="volume-item"><ol>
      <li><a href="/book/x_1/free-one_11"><i>1</i><strong>Free one</strong></a></li>
      <li><a href="/book/x_1/paid-one_12"><i>2</i><strong>Paid one</strong>
        <svg><use xlink:href="#i-lock"></use></svg></a></li>
    </ol></div>
    """
    refs = parser.parse_toc_page(html, "https://www.webnovel.com/book/x_1/catalog")
    assert [r.locked for r in refs] == [False, True]


def test_qidian_parse_chapter():
    parser = QidianParser()
    title, text = parser.parse_chapter(
        read("webnovel_chapter.html"),
        WEBNOVEL_URL + "/the-first-awakening_96997669322410840",
    )
    assert title == "Chapter 1: The First Awakening"
    assert text.startswith("I jolted awake with a silent scream.")
    assert len(text) > 1000
    # Author's thoughts (m-thou) must not leak into story text
    assert "CREATORS' THOUGHT" not in text
