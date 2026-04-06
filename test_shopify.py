"""Fetch a Shopify product JSON and HTML for analysis."""

import asyncio
import json
from scrapfly import ScrapeConfig
from config import SCRAPFLY

async def main():
    base_url = "https://www.anyeluz.com/products/kit-potencializador-anyeluz"

    # Try the .json endpoint first
    result_json = await SCRAPFLY.async_scrape(ScrapeConfig(
        base_url + ".json",
        asp=True,
    ))
    with open("shopify_product.json", "w", encoding="utf-8") as f:
        f.write(result_json.content)
    print(f"JSON Status: {result_json.status_code}")
    print(f"JSON size: {len(result_json.content)} chars")

    # Also fetch HTML for reviews analysis
    result_html = await SCRAPFLY.async_scrape(ScrapeConfig(
        base_url,
        asp=True,
    ))
    with open("shopify_page.html", "w", encoding="utf-8") as f:
        f.write(result_html.content)
    print(f"HTML Status: {result_html.status_code}")
    print(f"HTML size: {len(result_html.content)} chars")

if __name__ == "__main__":
    asyncio.run(main())
