"""Fetch an Etsy product page via Scrapfly and dump HTML for analysis."""

import asyncio
from scrapfly import ScrapeConfig
from config import SCRAPFLY

async def main():
    url = "https://www.etsy.com/es/listing/4356424401/set-de-alimentacion-de-silicona?ls=r&external=1&ref_is_trending=0&ref_is_popular=0&ref_group_id=hp_content_grouping_gqc__632239666667&ref=hp_content_grouping-5-3&pro=1&sts=1&content_source=6e2946f723e340e4e351db22f2f41586%253ALTe4cf516d4e9967557e7cde6cea31e9d8e22a53cc&logging_key=6e2946f723e340e4e351db22f2f41586%3ALTe4cf516d4e9967557e7cde6cea31e9d8e22a53cc"

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
