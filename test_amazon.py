"""Fetch an Amazon product page via Scrapfly and dump HTML for analysis."""

import asyncio
from scrapfly import ScrapeConfig
from config import SCRAPFLY

async def main():
    url = "https://www.amazon.com/dp/B0CD1DZ6RD"

    result = await SCRAPFLY.async_scrape(ScrapeConfig(
        url,
        asp=True,
        country="US",
        proxy_pool="public_residential_pool",
    ))

    with open("amazon_page.html", "w", encoding="utf-8") as f:
        f.write(result.content)

    print(f"Status: {result.status_code}")
    print(f"Final URL: {result.context['url']}")
    print(f"Saved HTML to amazon_page.html ({len(result.content)} chars)")

if __name__ == "__main__":
    asyncio.run(main())
