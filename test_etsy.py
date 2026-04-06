"""Fetch an Etsy product page via Scrapfly and dump HTML for analysis."""

import asyncio
from scrapfly import ScrapeConfig
from config import SCRAPFLY

async def main():
    url = "https://www.etsy.com/listing/1394647088/bloques-apilables-juego-de-4"

    result = await SCRAPFLY.async_scrape(ScrapeConfig(
        url,
        asp=True,
        country="US",
        proxy_pool="public_residential_pool",
    ))

    with open("etsy_page.html", "w", encoding="utf-8") as f:
        f.write(result.content)

    print(f"Status: {result.status_code}")
    print(f"Final URL: {result.context['url']}")
    print(f"Saved HTML to etsy_page.html ({len(result.content)} chars)")

if __name__ == "__main__":
    asyncio.run(main())
