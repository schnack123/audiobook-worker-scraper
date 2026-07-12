# audiobook-worker-scraper

Browser-based scraper worker for the audiobook pipeline. Consumes the
`scraper` Redis queue (job types `web_scrape` and `check_chapters`) and
produces raw chapter text in S3 (`{Novel}/Text/{N}.txt`):

- **web_scrape**: syncs the site's chapter list, then downloads chapter text
  (incremental - chapters that already have text are skipped unless force)
- **check_chapters**: chapter-list sync only; refreshes metadata/cover and
  creates rows for new chapters so the frontend can show "N new chapters"

Supported sites (parsers ported from
[WebToEpub](https://github.com/dteviot/WebToEpub)):

- fanmtl.com (`parsers/readwn.py`)
- webnovel.com (`parsers/qidian.py`) - locked/paid chapters are skipped
- wtr-lab.com (`parsers/wtrlab.py`) - always fetches the free "web"
  translation (`?service=web`); no login/cookies needed

Pages are fetched with [nodriver](https://github.com/ultrafunkamsterdam/nodriver)
driving a real headed Chrome under Xvfb, which passes Cloudflare checks that
block plain HTTP clients.

Shared contracts (DB models, queues, S3 layout, worker runtime) come from
[audiobook-core](https://github.com/schnack123/audiobook-core). Platform
overview and deploy workflow: see the `audiobook-platform` repo.

## Run locally

```bash
pip install -e ../audiobook-core -r requirements.txt
python main.py   # needs .env / env vars + a local Chrome
```

## Tests

```bash
pytest tests   # parser unit tests against saved HTML fixtures
```

## Deploy

```bash
./deploy.sh            # build linux/amd64 -> push to Docker Hub -> Sevalla deploy
```
