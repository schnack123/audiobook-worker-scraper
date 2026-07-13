"""nodriver (undetected Chrome) session for Cloudflare-protected novel sites.

One Chrome instance per job: the handler calls start() once, reuses the
session for every page in the job, and stop()s in a finally block. Runs headed
under Xvfb in the container (headless Chrome is much easier to detect).
"""
import asyncio
import logging
import os

import nodriver

logger = logging.getLogger(__name__)

# Interstitial-only signals: normal pages can embed Cloudflare's
# challenge-platform beacon script, so that string must NOT be used here.
_CHALLENGE_MARKERS = (
    "<title>Just a moment...</title>",
    "_cf_chl_opt",
    'id="challenge-form"',
    "Verify you are human",
)

# How long a page may take to render (including solving a CF challenge)
PAGE_TIMEOUT = 60.0


class ScraperBlockedError(Exception):
    """Cloudflare challenge could not be passed - job should not auto-retry."""


def _is_challenge(html: str) -> bool:
    return any(marker in html for marker in _CHALLENGE_MARKERS)


class ScraperBrowser:
    def __init__(self):
        self._browser: nodriver.Browser | None = None

    async def start(self) -> None:
        args = ["--window-size=1920,1080", "--disable-dev-shm-usage"]
        # Container runtimes usually can't provide Chrome's sandbox primitives
        no_sandbox = os.environ.get("SCRAPER_NO_SANDBOX") == "1"
        self._browser = await nodriver.start(
            headless=False,
            sandbox=not no_sandbox,
            browser_executable_path=os.environ.get("CHROME_BIN") or None,
            browser_args=args,
            lang="en-US",
        )
        logger.info("Chrome started (sandbox=%s)", not no_sandbox)

    async def stop(self) -> None:
        if self._browser is not None:
            try:
                self._browser.stop()
            except Exception as e:
                logger.warning("Browser stop failed: %s", e)
            self._browser = None

    async def restart(self) -> None:
        """Fresh Chrome session (new profile/cookies). Some sites stop serving
        chapter content after a number of pages per session; a restart resets
        that. Bounded so a wedged Chrome can't hang the job forever."""
        await asyncio.wait_for(self._restart(), timeout=180.0)

    async def _restart(self) -> None:
        await self.stop()
        await self.start()

    async def get_html(
        self,
        url: str,
        wait_selector: str | None = None,
        is_ready=None,
    ) -> str:
        """Navigate and return the rendered HTML once `wait_selector` matches
        (and `is_ready(html)` returns True, when given). The content check
        guards against SPAs that briefly satisfy the selector with stale or
        skeleton DOM (e.g. wtr-lab's infinite reader preloading the next
        chapter's empty container). Handles Cloudflare interstitials (waits
        them out, clicks the Turnstile checkbox once if needed)."""
        assert self._browser is not None, "start() not called"
        # Bounded: a wedged Chrome can make .get() hang indefinitely, which
        # would stall the whole job (heartbeats keep it "processing" forever).
        tab = await asyncio.wait_for(self._browser.get(url), timeout=30.0)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + PAGE_TIMEOUT
        verify_attempted = False
        html = ""
        while loop.time() < deadline:
            await asyncio.sleep(1.0)
            try:
                html = await tab.get_content()
            except Exception:
                continue
            if _is_challenge(html):
                if not verify_attempted:
                    verify_attempted = True
                    try:
                        await tab.verify_cf()
                        logger.info("Clicked Cloudflare checkbox for %s", url)
                    except Exception as e:
                        logger.debug("verify_cf failed (may auto-pass): %s", e)
                continue
            if wait_selector is not None:
                try:
                    await tab.select(wait_selector, timeout=3)
                except Exception:
                    continue
                html = await tab.get_content()
            if is_ready is not None and not is_ready(html):
                continue
            return html
        if _is_challenge(html):
            raise ScraperBlockedError(f"Blocked by Cloudflare challenge at {url}")
        raise TimeoutError(f"Page did not render {wait_selector!r} within {PAGE_TIMEOUT}s: {url}")
