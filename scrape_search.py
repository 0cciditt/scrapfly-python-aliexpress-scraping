"""Scrape AliExpress search results."""

import json
import math
import asyncio
from typing import Dict, List
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

from scrapfly import ScrapeApiResponse, ScrapeConfig

from config import BASE_CONFIG, SCRAPFLY, normalize_url


def add_or_replace_url_parameters(url: str, **params):
    """Add url parameters or replace them with new values."""
    parsed_url = urlparse(url)
    query_params = dict(parse_qsl(parsed_url.query))
    query_params.update(params)
    updated_url = parsed_url._replace(query=urlencode(query_params))
    return urlunparse(updated_url)


def extract_search(result: ScrapeApiResponse) -> Dict:
    """Extract json data from search page."""
    script_with_data = result.selector.xpath('//script[contains(.,"_init_data_=")]')
    data = json.loads(script_with_data.re(r"_init_data_\s*=\s*{\s*data:\s*({.+}) }")[0])
    return data["data"]["root"]["fields"]


def parse_search(result: ScrapeApiResponse):
    """Parse search page response for product preview results."""
    data = extract_search(result)
    return data["mods"]["itemList"]["content"]


async def scrape_search(url: str, max_pages: int = 2) -> List[Dict]:
    """Scrape all search results and return parsed search result data."""
    url = normalize_url(url)
    print(f"scraping search url {url}")
    first_page_result = await SCRAPFLY.async_scrape(ScrapeConfig(url, **BASE_CONFIG))
    _first_page_data = extract_search(first_page_result)
    page_size = _first_page_data["pageInfo"]["pageSize"]
    total_pages = int(math.ceil(_first_page_data["pageInfo"]["totalResults"] / page_size))
    if total_pages > max_pages:
        total_pages = max_pages

    product_previews = parse_search(first_page_result)
    print(f"search {url} found {total_pages} pages")
    other_pages = [
        ScrapeConfig(
            add_or_replace_url_parameters(first_page_result.context["url"], page=page),
            **BASE_CONFIG,
        )
        for page in range(2, total_pages + 1)
    ]
    async for result in SCRAPFLY.concurrent_scrape(other_pages):
        product_previews.extend(parse_search(result))
    print(f"search {url} scraped {len(product_previews)} results")
    return product_previews


async def main():
    search_results = await scrape_search(
        url="https://www.aliexpress.com/w/wholesale-drills.html?catId=0&SearchText=drills",
        max_pages=2,
    )

    with open("search_results.json", "w", encoding="utf-8") as f:
        json.dump(search_results, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    asyncio.run(main())
