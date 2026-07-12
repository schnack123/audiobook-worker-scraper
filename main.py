import asyncio

from audiobook_core.workers.runner import worker_main

from handler import run_scraper_job

if __name__ == "__main__":
    asyncio.run(worker_main("scraper", run_scraper_job))
