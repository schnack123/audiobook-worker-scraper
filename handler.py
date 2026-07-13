"""Scraper worker: browser-based ingest for fanmtl.com / webnovel.com / wtr-lab.com.

Job types:
- web_scrape:     TOC sync + download chapter text -> {Novel}/Text/{N}.txt
- check_chapters: TOC sync only (new chapter rows, metadata, cover) - lets the
                  frontend show "N new chapters" without downloading anything.

Chapters on fanmtl/webnovel are URL-addressed: rows are matched by
chapters.source_url and numbered by TOC position, so numbering stays stable
across updates and new chapters append at the end. wtr-lab chapters are
number-addressed (ChapterRef.number from the site's API). Pre-scraper rows
(numbered, no source_url - old WTR ingest worker, EPUBs, v1 imports) are
adopted on first sync: by site number on wtr-lab, by TOC position on
URL-addressed sites, so linking a source URL to an old novel continues where
it left off instead of duplicating chapters. Locked (paid) webnovel chapters
are skipped entirely - they get no rows and appear as new chapters if they
ever unlock.
"""
import asyncio
import io
import logging
import random

import httpx
from sqlalchemy import select

from audiobook_core.core.db import session_scope
from audiobook_core.core.s3 import get_s3, text_key, thumbnail_key
from audiobook_core.models import Chapter, Job, JobType, Novel
from audiobook_core.workers.execution import JobExecution
from browser import ScraperBlockedError, ScraperBrowser
from parsers import ChapterRef, NovelInfo, get_parser

logger = logging.getLogger(__name__)

# Abort after this many consecutive chapter failures (site layout change/block)
MAX_CONSECUTIVE_FAILURES = 5
MIN_CHAPTER_CHARS = 50


async def run_scraper_job(job: Job, execution: JobExecution) -> list[int]:
    if job.type == JobType.CHECK_CHAPTERS:
        return await _run_check(job, execution)
    if job.type == JobType.WEB_SCRAPE:
        return await _run_web_scrape(job, execution)
    raise ValueError(f"Scraper worker got unexpected job type {job.type}")


async def _resolve_source(job: Job) -> tuple[str, str]:
    async with session_scope() as session:
        novel = (await session.execute(select(Novel).where(Novel.id == job.novel_id))).scalar_one()
        source_url = job.payload.get("source_url") or novel.source_url
        novel_name = novel.name
    if not source_url:
        raise ValueError("Job has no source URL (payload.source_url or novel.source_url)")
    return novel_name, source_url


async def _fetch_novel_info(browser: ScraperBrowser, parser, source_url: str) -> NovelInfo:
    """Fetch and combine all TOC pages plus the metadata page (if separate)."""
    toc_url = parser.toc_url(source_url)
    html = await browser.get_html(toc_url, parser.toc_ready_selector)
    info = parser.parse_novel(html, toc_url)
    for extra_url in parser.extra_toc_urls(html, toc_url):
        extra_html = await browser.get_html(extra_url, parser.toc_ready_selector)
        info.chapters.extend(parser.parse_toc_page(extra_html, extra_url))
    # Defensive de-dupe: pagination edges can repeat entries
    seen: set[str] = set()
    info.chapters = [c for c in info.chapters if not (c.url in seen or seen.add(c.url))]

    metadata_url = parser.metadata_url(source_url)
    if metadata_url and metadata_url != toc_url:
        meta_html = await browser.get_html(metadata_url, "h1")
        parser.parse_metadata(meta_html, metadata_url, info)
    return info


async def _sync_toc(job: Job, info: NovelInfo, source_url: str) -> tuple[dict[int, ChapterRef], set[int]]:
    """Upsert novel metadata + chapter rows from the live TOC.

    Returns (number -> ChapterRef for every unlocked TOC chapter,
    numbers that already have text)."""
    unlocked = [c for c in info.chapters if not c.locked]
    locked_count = len(info.chapters) - len(unlocked)

    async with session_scope() as session:
        novel = (await session.execute(select(Novel).where(Novel.id == job.novel_id))).scalar_one()
        novel.source_url = source_url
        if info.title:
            novel.title = info.title
        if info.author:
            novel.author = info.author
        if info.description and not novel.description:
            novel.description = info.description

        rows = (
            await session.execute(
                select(Chapter).where(Chapter.novel_id == job.novel_id).order_by(Chapter.number)
            )
        ).scalars().all()
        by_url = {c.source_url: c for c in rows if c.source_url}
        by_number = {c.number: c for c in rows}
        next_number = max((c.number for c in rows), default=0) + 1

        number_to_ref: dict[int, ChapterRef] = {}
        scraped: set[int] = set()
        new_count = 0
        for position, ref in enumerate(unlocked, start=1):
            row = by_url.get(ref.url)
            if row is None:
                if ref.number is not None:
                    # Number-addressed site: adopt the existing numbered row
                    # (possibly created by the old WTR ingest worker)
                    row = by_number.get(ref.number)
                else:
                    # URL-addressed site: adopt a pre-scraper row (numbered,
                    # no source_url) at the same TOC position, so linking a
                    # source URL to an old novel doesn't duplicate chapters.
                    candidate = by_number.get(position)
                    if candidate is not None and candidate.source_url is None:
                        row = candidate
                if row is not None:
                    row.source_url = ref.url
            if row is None:
                number = ref.number if ref.number is not None else next_number
                row = Chapter(novel_id=job.novel_id, number=number, source_url=ref.url)
                session.add(row)
                by_number[number] = row
                next_number = max(next_number, number + 1)
                new_count += 1
            if not row.title:
                row.title = ref.title
            number_to_ref[row.number] = ref
            if row.text_hash is not None:
                scraped.add(row.number)

    logger.info(
        "TOC sync '%s': %d chapters on site (%d locked skipped), %d new rows, %d already have text",
        info.title, len(info.chapters), locked_count, new_count, len(scraped),
    )
    return number_to_ref, scraped


async def _ensure_thumbnail(job: Job, cover_url: str | None) -> None:
    """Best-effort cover download (plain HTTP - covers are rarely challenged)."""
    if not cover_url:
        return
    async with session_scope() as session:
        novel = (await session.execute(select(Novel).where(Novel.id == job.novel_id))).scalar_one()
        if novel.has_thumbnail:
            return
        novel_name = novel.name
    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"},
            follow_redirects=True, timeout=30,
        ) as client:
            resp = await client.get(cover_url)
            resp.raise_for_status()
            data = resp.content

        from PIL import Image

        def _process() -> tuple[bytes, bytes]:
            img = Image.open(io.BytesIO(data)).convert("RGB")
            full = io.BytesIO()
            img.save(full, format="JPEG", quality=90)
            small_img = img.copy()
            small_img.thumbnail((400, 400))
            small = io.BytesIO()
            small_img.save(small, format="JPEG", quality=80)
            return full.getvalue(), small.getvalue()

        full_bytes, small_bytes = await asyncio.to_thread(_process)
        s3 = get_s3()
        await s3.aput_bytes(thumbnail_key(novel_name), full_bytes, "image/jpeg")
        await s3.aput_bytes(thumbnail_key(novel_name, small=True), small_bytes, "image/jpeg")
        async with session_scope() as session:
            novel = (await session.execute(select(Novel).where(Novel.id == job.novel_id))).scalar_one()
            novel.has_thumbnail = True
        logger.info("Downloaded cover for '%s'", novel_name)
    except Exception as e:
        logger.warning("Cover download failed (%s): %s", cover_url, e)


# --------------------------------------------------------------------------
# check_chapters: TOC-only refresh
# --------------------------------------------------------------------------

async def _run_check(job: Job, execution: JobExecution) -> list[int]:
    _, source_url = await _resolve_source(job)
    parser = get_parser(source_url)
    browser = ScraperBrowser()
    await browser.start()
    try:
        info = await _fetch_novel_info(browser, parser, source_url)
    finally:
        await browser.stop()

    await execution.set_total(1)
    await _sync_toc(job, info, source_url)
    await _ensure_thumbnail(job, info.cover_url)
    await execution.report_progress(1, [])
    return []


# --------------------------------------------------------------------------
# web_scrape: TOC sync + chapter download
# --------------------------------------------------------------------------

async def _fetch_chapter(browser, parser, ref, number, execution) -> tuple[str | None, str]:
    """Fetch one chapter; on a render timeout, restart Chrome and retry after
    the parser's backoff waits.

    Some sites throttle chapter delivery per IP (wtr-lab serves ~5 web
    translations, then renders the page shell without text for ~10 minutes).
    A timeout there means "throttled", not "site broken", so wait out the
    parser's `timeout_backoffs` before giving up on the chapter."""
    backoffs = list(parser.timeout_backoffs) or [0.0]
    for attempt, wait in enumerate([None] + backoffs):
        if wait is not None:
            logger.info(
                "Chapter %d timed out; waiting %.0fs and retrying with a fresh browser (attempt %d/%d)",
                number, wait, attempt, len(backoffs),
            )
            await asyncio.sleep(wait)
            if execution.interrupted:
                raise TimeoutError(f"interrupted while backing off on chapter {number}")
            await browser.restart()
        try:
            html = await browser.get_html(
                ref.url,
                parser.chapter_ready(ref.url),
                is_ready=lambda h: parser.chapter_is_ready(h, ref.url),
            )
            break
        except TimeoutError:
            if attempt == len(backoffs):
                raise
    title, text = parser.parse_chapter(html, ref.url)
    if len(text) < MIN_CHAPTER_CHARS:
        raise ValueError(f"content too short ({len(text)} chars)")
    return title, text


async def _run_web_scrape(job: Job, execution: JobExecution) -> list[int]:
    novel_name, source_url = await _resolve_source(job)
    parser = get_parser(source_url)
    s3 = get_s3()

    browser = ScraperBrowser()
    await browser.start()
    try:
        info = await _fetch_novel_info(browser, parser, source_url)
        number_to_ref, scraped = await _sync_toc(job, info, source_url)
        await _ensure_thumbnail(job, info.cover_url)

        # Resolve targets: explicit list, or range, or everything not yet scraped
        available = set(number_to_ref)
        if job.chapters:
            targets = sorted(set(job.chapters) & available)
        else:
            start = job.payload.get("range_start") or 1
            end = job.payload.get("range_end") or max(available, default=0)
            targets = sorted(n for n in available if start <= n <= end)
        if not job.force:
            targets = [n for n in targets if n not in scraped]

        logger.info(
            "Scraping '%s': %d/%d chapters to fetch (%d already have text)",
            novel_name, len(targets), len(available), len(scraped),
        )
        await execution.set_total(len(targets))

        failed: list[int] = []
        consecutive_failures = 0
        done = 0
        for number in targets:
            if execution.interrupted:
                break
            ref = number_to_ref[number]
            try:
                title, text = await _fetch_chapter(browser, parser, ref, number, execution)
                etag = await s3.aput_text(text_key(novel_name, number), text)
                async with session_scope() as session:
                    chapter = (
                        await session.execute(
                            select(Chapter).where(
                                Chapter.novel_id == job.novel_id, Chapter.number == number
                            )
                        )
                    ).scalar_one()
                    chapter.text_hash = etag
                    if title:
                        chapter.title = title
                consecutive_failures = 0
            except ScraperBlockedError as e:
                logger.error("Blocked on chapter %d: %s", number, e)
                failed.append(number)
                raise RuntimeError(
                    f"Site blocked the scraper at chapter {number} "
                    f"({done}/{len(targets)} done). Retry the job later."
                ) from e
            except Exception as e:
                logger.error("Chapter %d (%s) failed: %s", number, ref.url, e)
                failed.append(number)
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    raise RuntimeError(
                        f"{consecutive_failures} consecutive chapter failures "
                        f"(last: chapter {number}) - aborting; site may have changed or blocked us."
                    ) from e
            done += 1
            await execution.report_progress(done, failed)

            if done < len(targets) and not execution.interrupted:
                await asyncio.sleep(random.uniform(*parser.delay_range))

        return failed
    finally:
        await browser.stop()
