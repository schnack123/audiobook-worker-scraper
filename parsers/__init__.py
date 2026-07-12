"""Parser registry keyed by URL hostname."""
from urllib.parse import urlparse

from parsers.base import ChapterRef, NovelInfo, Parser, ParserError
from parsers.qidian import QidianParser
from parsers.readwn import ReadwnParser
from parsers.wtrlab import WtrLabParser

_PARSERS: list[Parser] = [ReadwnParser(), QidianParser(), WtrLabParser()]

SUPPORTED_HOSTS = tuple(host for p in _PARSERS for host in p.hosts)


def get_parser(url: str) -> Parser:
    host = (urlparse(url).hostname or "").lower()
    for parser in _PARSERS:
        if any(host == h or host.endswith("." + h) for h in parser.hosts):
            return parser
    raise ParserError(f"No parser for host {host!r} (supported: {SUPPORTED_HOSTS})")


__all__ = [
    "ChapterRef",
    "NovelInfo",
    "Parser",
    "ParserError",
    "SUPPORTED_HOSTS",
    "get_parser",
]
