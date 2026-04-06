"""Scrape Etsy product pages."""

import json
import re
import asyncio
import uuid
from typing import Dict, List, Optional

from scrapfly import ScrapeApiResponse, ScrapeConfig

from config import SCRAPFLY


def clean_url(url: str) -> str:
    """Remove tracking parameters and locale prefix from Etsy URLs."""
    # Remove query params and hash
    url = url.split("?")[0].split("#")[0]
    # Normalize /es/listing/ → /listing/
    url = re.sub(r"etsy\.com/\w{2}/listing/", "etsy.com/listing/", url)
    return url


def parse_product(result: ScrapeApiResponse) -> Dict:
    """Parse an Etsy product page."""
    selector = result.selector
    html = result.content
    final_url = result.context["url"]

    # --- JSON-LD (primary data source) ---
    ld_json = {}
    ld_scripts = selector.xpath('//script[@type="application/ld+json"]/text()').getall()
    for script in ld_scripts:
        try:
            data = json.loads(script)
            if data.get("@type") in ("Product", "ProductGroup"):
                ld_json = data
                break
        except (json.JSONDecodeError, AttributeError):
            continue

    # --- Info ---
    title = selector.xpath("//h1/text()").get()
    title = title.strip() if title else ld_json.get("name")

    listing_id = None
    lid_match = re.search(r"/listing/(\d+)", final_url)
    if lid_match:
        listing_id = lid_match.group(1)

    # Images from JSON-LD (hi-res)
    images = []
    for img in ld_json.get("image", []):
        if isinstance(img, dict):
            images.append(img.get("contentURL") or img.get("thumbnail", ""))
        elif isinstance(img, str):
            images.append(img)
    images = [i for i in images if i]

    # Rating from JSON-LD
    agg = ld_json.get("aggregateRating", {})
    rating = float(agg["ratingValue"]) if agg.get("ratingValue") else None
    review_count = int(agg["reviewCount"]) if agg.get("reviewCount") else None

    # Brand/shop from JSON-LD
    brand = ld_json.get("brand", {}).get("name")

    # Shop name from HTML
    shop_name = selector.xpath('//a[contains(@href,"/shop/")]//text()').get()
    shop_name = shop_name.strip() if shop_name else brand

    # Shop link
    shop_link = selector.xpath('//a[contains(@href,"/shop/")]/@href').get()
    if shop_link:
        shop_link = shop_link.split("?")[0]

    # Description from JSON-LD
    description = ld_json.get("description")

    # Categories from breadcrumbs JSON-LD
    categories = []
    for script in ld_scripts:
        try:
            data = json.loads(script)
            if data.get("@type") == "BreadcrumbList":
                for item in data.get("itemListElement", []):
                    categories.append(item.get("name", ""))
                break
        except (json.JSONDecodeError, AttributeError):
            continue

    # --- Pricing ---
    # From JSON-LD variants
    price = None
    currency = None
    variants = ld_json.get("hasVariant", [])
    if variants:
        first_offer = variants[0].get("offers", {})
        if isinstance(first_offer, dict):
            price = float(first_offer["price"]) if first_offer.get("price") else None
            currency = first_offer.get("priceCurrency")

    # Fallback to HTML price
    if not price:
        price_texts = selector.xpath(
            '//*[contains(@class,"wt-text-title-larger")]//text()'
        ).getall()
        for pt in price_texts:
            pm = re.search(r"[\d,.]+", pt.strip())
            if pm:
                price_str = pm.group().replace(",", "")
                try:
                    price = float(price_str)
                except ValueError:
                    pass
                # Currency symbol
                cm = re.search(r"[^\d.,\s]+", pt.strip())
                if cm:
                    currency = cm.group().strip()
                break

    # Variants list
    variant_list = []
    for v in variants:
        v_offers = v.get("offers", {})
        variant_list.append({
            "name": v.get("name"),
            "price": float(v_offers["price"]) if isinstance(v_offers, dict) and v_offers.get("price") else None,
            "availability": (
                "InStock" if isinstance(v_offers, dict) and "InStock" in str(v_offers.get("availability", ""))
                else "OutOfStock"
            ),
        })

    pricing = {
        "currency": currency,
        "price": price,
        "variants": variant_list if variant_list else None,
    }

    info = {
        "name": title,
        "listingId": listing_id,
        "link": final_url,
        "shop": shop_name,
        "shopLink": shop_link,
        "images": images,
        "rating": rating,
        "reviewCount": review_count,
        "categories": categories,
    }

    # --- Reviews (up to 10, prioritize images+text, then text only) ---
    reviews_with_images = []
    reviews_text_only = []
    review_cards = selector.xpath('//*[contains(@class,"review-card")]')

    for c in review_cards:
        # Rating from hidden input
        rating_val = c.xpath('.//input[@name="rating"]/@value').get()
        if not rating_val:
            continue
        r_rating = int(rating_val)

        # User name
        name_raw = c.xpath(
            './/*[contains(@class,"wt-text-link-no-underline")]/text()'
        ).get()
        r_name = name_raw.strip() if name_raw else None

        # Date
        date_texts = c.xpath(
            './/*[contains(@class,"wt-text-body-small")]/text()'
        ).getall()
        r_date = None
        for d in date_texts:
            d = d.strip()
            if re.match(r"\w+ \d+, \d{4}", d):
                r_date = d
                break

        # Content from content-toggle
        content_parts = c.xpath(
            './/*[contains(@class,"wt-content-toggle")]//text()'
        ).getall()
        r_content = " ".join(t.strip() for t in content_parts if t.strip()) or None

        # Review images (exclude avatars which have /iusa/ in path)
        imgs = c.xpath('.//img[contains(@src,"etsystatic")]/@src').getall()
        imgs = list(dict.fromkeys(imgs))
        imgs = [img for img in imgs if "/iusa/" not in img]

        if not r_content and not imgs:
            continue

        review = {
            "user": r_name,
            "rating": r_rating,
            "date": r_date,
            "content": r_content,
            "images": imgs,
        }

        if imgs:
            reviews_with_images.append(review)
        else:
            reviews_text_only.append(review)

    reviews = (reviews_with_images + reviews_text_only)[:10]

    return {
        "info": info,
        "pricing": pricing,
        "description": description,
        "reviews": reviews,
    }


async def scrape_etsy_product(url: str) -> Dict:
    """Scrape an Etsy product page by URL."""
    url = clean_url(url)
    print(f"scraping product: {url}")

    result = await SCRAPFLY.async_scrape(
        ScrapeConfig(
            url,
            asp=True,
            country="US",
            proxy_pool="public_residential_pool",
            session=f"etsy-{uuid.uuid4().hex[:8]}",
        )
    )

    data = parse_product(result)
    print(f"successfully scraped product: {url}")
    return data


async def main():
    product = await scrape_etsy_product(
        url="https://www.etsy.com/es/listing/4435414273/bolsa-de-lona-bordada-personalizada?ls=r&ref=hp_opfy-1-3&pro=1&sts=1&content_source=47f9a197c31068d09d43046285af02c3%253ALTe9f13a4e5db0519700db0b6c608831dfd701f001&logging_key=47f9a197c31068d09d43046285af02c3%3ALTe9f13a4e5db0519700db0b6c608831dfd701f001"
    )

    with open("etsy_product.json", "w", encoding="utf-8") as f:
        json.dump(product, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    asyncio.run(main())
