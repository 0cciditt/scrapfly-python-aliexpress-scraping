"""Scrape Shopify store product pages via the built-in JSON API."""

import json
import re
import asyncio
import uuid
from typing import Dict, List, Optional
from html import unescape

from scrapfly import ScrapeApiResponse, ScrapeConfig

from config import SCRAPFLY


def clean_url(url: str) -> str:
    """Clean Shopify product URL, removing query params and hash."""
    return url.split("?")[0].split("#")[0]


def strip_html(html_str: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r"<[^>]+>", "\n", html_str)
    text = unescape(text)
    # Collapse multiple newlines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_product(raw_json: str) -> Dict:
    """Parse Shopify product JSON API response."""
    data = json.loads(raw_json)
    product = data.get("product", {})

    # --- Info ---
    images = [img.get("src") for img in product.get("images", []) if img.get("src")]

    info = {
        "name": product.get("title"),
        "productId": product.get("id"),
        "handle": product.get("handle"),
        "vendor": product.get("vendor"),
        "productType": product.get("product_type") or None,
        "tags": product.get("tags", "").split(", ") if product.get("tags") else [],
        "images": images,
        "createdAt": product.get("created_at"),
        "updatedAt": product.get("updated_at"),
    }

    # --- Description ---
    body_html = product.get("body_html", "")
    description = strip_html(body_html) if body_html else None

    # --- Variants & Pricing ---
    variants = []
    for v in product.get("variants", []):
        price = float(v["price"]) if v.get("price") else None
        compare_price = float(v["compare_at_price"]) if v.get("compare_at_price") else None
        currency = v.get("price_currency")

        variants.append({
            "id": v.get("id"),
            "title": v.get("title"),
            "sku": v.get("sku") or None,
            "price": price,
            "compareAtPrice": compare_price,
            "currency": currency,
            "available": v.get("available"),
            "requiresShipping": v.get("requires_shipping"),
            "weight": v.get("weight"),
            "weightUnit": v.get("weight_unit"),
        })

    # Primary pricing from first variant
    first_variant = variants[0] if variants else {}
    pricing = {
        "currency": first_variant.get("currency"),
        "price": first_variant.get("price"),
        "compareAtPrice": first_variant.get("compareAtPrice"),
        "discount": None,
    }
    if pricing["price"] and pricing["compareAtPrice"] and pricing["compareAtPrice"] > pricing["price"]:
        savings_pct = round((1 - pricing["price"] / pricing["compareAtPrice"]) * 100)
        pricing["discount"] = f"{savings_pct}% OFF"

    # --- Options (e.g. Size, Color) ---
    options = []
    for opt in product.get("options", []):
        options.append({
            "name": opt.get("name"),
            "values": opt.get("values", []),
        })

    return {
        "info": info,
        "description": description,
        "pricing": pricing,
        "variants": variants if len(variants) > 1 else None,
        "options": options if options and options[0].get("name") != "Title" else None,
    }


async def scrape_shopify_product(url: str) -> Dict:
    """Scrape a Shopify product page using the JSON API."""
    url = clean_url(url)

    # Ensure URL ends with the product path, then append .json
    if not url.endswith(".json"):
        json_url = url + ".json"
    else:
        json_url = url

    print(f"scraping product: {json_url}")

    result = await SCRAPFLY.async_scrape(
        ScrapeConfig(
            json_url,
            asp=True,
            session=f"shopify-{uuid.uuid4().hex[:8]}",
        )
    )

    data = parse_product(result.content)
    print(f"successfully scraped product: {url}")
    return data


async def main():
    product = await scrape_shopify_product(
        url="https://uk-gymshark-com.translate.goog/products/gymshark-power-oversized-t-shirt-ss-tops-brown-ss26?_x_tr_sl=en&_x_tr_tl=es&_x_tr_hl=es&_x_tr_pto=tc&_x_tr_hist=true"
    )

    with open("shopify_result.json", "w", encoding="utf-8") as f:
        json.dump(product, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    asyncio.run(main())
