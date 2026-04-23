"""Scrape individual AliExpress product pages."""

import json
import asyncio
import re
import uuid
from typing import Dict

from scrapfly import ScrapeApiResponse, ScrapeConfig

from config import BASE_CONFIG, SCRAPFLY, normalize_url
from scrape_reviews import scrape_product_reviews


def parse_product(result: ScrapeApiResponse) -> Dict:
    """Parse product HTML page for product data."""
    selector = result.selector
    reviews = selector.xpath("//a[contains(@class,'reviewer--reviews')]/text()").get()
    rate = selector.xpath("//div[contains(@class,'rating--wrap')]/div").getall()
    sold_count = selector.xpath(
        "//a[contains(@class, 'reviewer--sliderItem')]//span[contains(text(), 'sold')]/text()"
    ).get()
    available_count = selector.xpath(
        "//div[contains(@class,'quantity--info')]/div/span/text()"
    ).get()

    info = {
        "name": selector.xpath("//h1[@data-pl]/text()").get(),
        "productId": int(result.context["url"].split("item/")[-1].split(".")[0]),
        "link": result.context["url"],
        "media": selector.xpath("//div[contains(@class,'slider--img')]/img/@src").getall(),
        "rate": len(rate) if rate else None,
        "reviews": int(re.sub(r"[^\d]", "", reviews)) if reviews else None,
        "soldCount": (
            int(re.sub(r"[^\d]", "", sold_count))
            if sold_count and re.search(r"\d", sold_count)
            else 0
        ),
        "availableCount": (
            int(re.sub(r"[^\d]", "", available_count)) if available_count and re.search(r"\d", available_count) else None
        ),
    }

    price = selector.xpath(
        "//span[contains(@class,'price-default--current')]/text()"
    ).get()
    original_price = selector.xpath(
        "//span[contains(@class,'price-default--original')]//text()"
    ).get()
    discount = selector.xpath(
        "//span[contains(@class,'price--discount')]/text()"
    ).get()
    def parse_price(text):
        """Parse price string in any locale format (e.g. 1,234.56 or 1.234,56)."""
        if not text:
            return None
        # Extract the numeric part after any currency symbol
        nums = re.sub(r"[^\d.,]", "", text.split("$")[-1].strip())
        if not nums:
            return None
        # If last separator is a comma, it's the decimal (European format)
        if "," in nums and ("." not in nums or nums.rfind(",") > nums.rfind(".")):
            nums = nums.replace(".", "").replace(",", ".")
        else:
            nums = nums.replace(",", "")
        return float(nums)

    pricing = {
        "priceCurrency": "USD $",
        "price": parse_price(price),
        "originalPrice": parse_price(original_price) if original_price else "No discount",
        "discount": discount if discount else "No discount",
    }

    specifications = []
    for i in selector.xpath("//div[contains(@class,'specification--prop')]"):
        specifications.append(
            {
                "name": i.xpath(
                    ".//div[contains(@class,'specification--title')]/span/text()"
                ).get(),
                "value": i.xpath(
                    ".//div[contains(@class,'specification--desc')]/span/text()"
                ).get(),
            }
        )

    # --- Variants (Color, Size, etc.) ---
    variants = []
    seen_variant_names = set()
    for prop in selector.xpath("//div[contains(@class,'sku-item--property')]"):
        title_texts = prop.xpath(
            ".//div[contains(@class,'sku-item--title')]//text()"
        ).getall()
        title_raw = "".join(t.strip() for t in title_texts if t.strip()).strip()
        # Format is "Color: TK-0000338" — split on first ":"
        if ":" in title_raw:
            name = title_raw.split(":", 1)[0].strip()
        else:
            name = title_raw or None

        options = []
        for opt in prop.xpath(".//div[@data-sku-col]"):
            opt_classes = opt.xpath("@class").get() or ""
            img_src = opt.xpath(".//img/@src").get()
            img_alt = opt.xpath(".//img/@alt").get()
            text_title = opt.xpath("@title").get()
            text_val = opt.xpath(".//span/text()").get()

            value = img_alt or text_title or text_val
            if not value:
                continue

            options.append(
                {
                    "value": value,
                    "image": img_src,
                    "selected": "sku-item--selected" in opt_classes,
                }
            )

        if options and name not in seen_variant_names:
            seen_variant_names.add(name)
            variants.append({"name": name, "options": options})

    faqs = []
    for i in selector.xpath("//div[@class='ask-list']/ul/li"):
        faqs.append(
            {
                "question": i.xpath(".//p[@class='ask-content']/span/text()").get(),
                "answer": i.xpath(".//ul[@class='answer-box']/li/p/text()").get(),
            }
        )

    seller_link = selector.xpath("//a[@data-pl='store-name']/@href").get()
    seller_followers = selector.xpath(
        "//div[contains(@class,'store-info')]/strong[2]/text()"
    ).get()
    if seller_followers and "M" in seller_followers:
        seller_followers = int(float(seller_followers.replace("M", "")) * 1_000_000)
    elif seller_followers and "K" in seller_followers:
        seller_followers = int(float(seller_followers.replace("K", "")) * 1_000)
    elif seller_followers:
        seller_followers = int(seller_followers)
    else:
        seller_followers = None
    seller = {
        "name": selector.xpath("//a[@data-pl='store-name']/text()").get(),
        "link": (
            seller_link.split("?")[0].replace("//", "") if seller_link else None
        ),
        "id": (
            int(seller_link.split("store/")[-1].split("?")[0]) if seller_link else None
        ),
        "info": {
            "positiveFeedback": selector.xpath(
                "//div[contains(@class,'store-info')]/strong/text()"
            ).get(),
            "followers": seller_followers,
        },
    }

    return {
        "info": info,
        "pricing": pricing,
        "variants": variants,
        "specifications": specifications,
        "faqs": faqs,
        "seller": seller,
    }


async def scrape_product(url: str) -> Dict:
    """Scrape an AliExpress product page by URL."""
    url = normalize_url(url)
    print(f"scraping product: {url}")
    result = await SCRAPFLY.async_scrape(
        ScrapeConfig(
            url,
            **BASE_CONFIG,
            render_js=True,
            auto_scroll=True,
            rendering_wait=5000,
            js_scenario=[
                {
                    "wait_for_selector": {
                        "selector": "//div[@id='nav-specification']//button",
                        "timeout": 5000,
                    }
                },
                {
                    "click": {
                        "selector": "//div[@id='nav-specification']//button",
                        "ignore_if_not_visible": True,
                    }
                },
                {
                    "click": {
                        "selector": '[class*="sku--menuChange"]',
                        "ignore_if_not_visible": True,
                    }
                },
                {"wait": 2000},
            ],
            proxy_pool="public_residential_pool",
            session=f"product-{uuid.uuid4().hex[:8]}",
        )
    )
    data = parse_product(result)

    # Fetch up to 10 user reviews (1 page of 10, no extra cost)
    product_id = str(data["info"]["productId"])
    try:
        review_data = await scrape_product_reviews(product_id, max_scrape_pages=1)
        reviews_raw = review_data.get("reviews", [])[:10]
        data["reviews"] = [
            {
                "user": r.get("buyerName"),
                "country": r.get("buyerCountry"),
                "rating": r.get("buyerEval"),
                "date": r.get("evalDate"),
                "content": r.get("buyerTranslationFeedback") or r.get("buyerFeedback"),
                "images": [
                    img if isinstance(img, str) else img.get("imgUrl", "")
                    for img in r.get("images", [])
                    if img
                ],
            }
            for r in reviews_raw
        ]
    except Exception as e:
        print(f"warning: could not fetch reviews: {e}")
        data["reviews"] = []

    print(f"successfully scraped product: {url}")
    return data


async def main():
    product_results = await scrape_product(
        url="https://es.aliexpress.com/item/1005008028611982.html"
    )

    with open("product.json", "w", encoding="utf-8") as f:
        json.dump(product_results, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    asyncio.run(main())
