"""Scrape AliExpress product reviews."""

import json
import asyncio
from typing import Dict

from scrapfly import ScrapeApiResponse, ScrapeConfig

from config import SCRAPFLY


def parse_review_page(result: ScrapeApiResponse):
    """Parse a review API response."""
    data = json.loads(result.content)["data"]
    return {
        "max_pages": data["totalPage"],
        "reviews": data["evaViewList"],
        "evaluation_stats": data["productEvaluationStatistic"],
    }


async def scrape_product_reviews(
    product_id: str, max_scrape_pages: int = None
) -> Dict:
    """Scrape all reviews of an AliExpress product."""

    def scrape_config_for_page(page):
        url = (
            f"https://feedback.aliexpress.com/pc/searchEvaluation.do"
            f"?productId={product_id}&lang=en_US&country=US"
            f"&page={page}&pageSize=10&filter=all&sort=complex_default"
        )
        return ScrapeConfig(url)

    first_page_result = await SCRAPFLY.async_scrape(scrape_config_for_page(1))
    data = parse_review_page(first_page_result)
    max_pages = data["max_pages"]

    if max_scrape_pages and max_scrape_pages < max_pages:
        max_pages = max_scrape_pages

    print(
        f"scraping reviews pagination of product {product_id}, "
        f"{max_pages - 1} pages remaining"
    )
    to_scrape = [scrape_config_for_page(page) for page in range(2, max_pages + 1)]
    async for result in SCRAPFLY.concurrent_scrape(to_scrape):
        data["reviews"].extend(parse_review_page(result)["reviews"])
    print(f"scraped {len(data['reviews'])} from review pages")
    data.pop("max_pages")
    return data


async def main():
    review_results = await scrape_product_reviews(
        product_id="1005006717259012",
        max_scrape_pages=3,
    )

    with open("product_reviews.json", "w", encoding="utf-8") as f:
        json.dump(review_results, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    asyncio.run(main())
