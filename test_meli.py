"""Fetch a MercadoLibre product page via Scrapfly and dump HTML for analysis."""

import asyncio
from scrapfly import ScrapeConfig
from config import SCRAPFLY

async def main():
    url = "https://www.mercadolibre.com.co/lamparas-tipo-spot-con-riel-de-1m-x-3-unid-de-10w-estructura-negro-luz-blanca/p/MCO42090990"

    result = await SCRAPFLY.async_scrape(ScrapeConfig(
        url,
        asp=True,
        country="CO",
        proxy_pool="public_residential_pool",
        render_js=True,
        rendering_wait=5000,
        session=f"meli-test",
    ))

    # Save raw HTML for analysis
    with open("meli_page.html", "w", encoding="utf-8") as f:
        f.write(result.content)

    print(f"Status: {result.status_code}")
    print(f"Final URL: {result.context['url']}")
    print(f"Saved HTML to meli_page.html ({len(result.content)} chars)")

if __name__ == "__main__":
    asyncio.run(main())
