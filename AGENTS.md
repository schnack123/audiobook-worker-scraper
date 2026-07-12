# Agent notes

One of the audiobook pipeline worker services. Keep changes surgical: this
repo owns ONLY its stage handler; everything shared (DB models, queues, S3
layout, job runtime, staleness logic) lives in `audiobook-core`
(../audiobook-core locally) and must be changed there.

- Platform overview, service catalog, and deploy workflow: `audiobook-platform`
  repo (locally at /Users/mats/Cursor/Audiobook creator).
- Consumes the `scraper` Redis queue (job types `web_scrape`,
  `check_chapters`). Scrapes fanmtl.com and webnovel.com with a real Chrome
  (nodriver + Xvfb) to pass Cloudflare; parsers are ported from WebToEpub.
- Parsers (parsers/) are pure HTML-in/data-out and unit-tested against saved
  fixtures in tests/fixtures - update the fixtures if a site changes layout.
- Chapters here are URL-addressed: rows match by `chapters.source_url`,
  numbers are TOC positions and stay stable across updates. Locked (paid)
  webnovel chapters are skipped entirely.
- Handlers receive `(job, execution)` and return a list of failed chapter
  numbers. Respect `execution.interrupted` between chapters and report
  progress via `execution.report_progress`.
- Never rename S3 folders/keys or DB tables - they are v1-compatible contracts.
- Deploy with ./deploy.sh (builds linux/amd64 image, pushes to Docker Hub,
  triggers the Sevalla deployment). Never hook Sevalla to the git repo.
