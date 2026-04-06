"""Scrape MercadoLibre product pages across all country sites."""

import json
import re
import asyncio
import uuid
from typing import Dict, List, Optional

from scrapfly import ScrapeApiResponse, ScrapeConfig

from config import SCRAPFLY

# MercadoLibre country domains → Scrapfly proxy country codes
MELI_COUNTRIES = {
    "mercadolibre.com.ar": "AR",
    "mercadolibre.com.bo": "BO",
    "mercadolivre.com.br": "BR",
    "mercadolibre.cl": "CL",
    "mercadolibre.com.co": "CO",
    "mercadolibre.co.cr": "CR",
    "mercadolibre.com.do": "DO",
    "mercadolibre.com.ec": "EC",
    "mercadolibre.com.gt": "GT",
    "mercadolibre.com.hn": "HN",
    "mercadolibre.com.mx": "MX",
    "mercadolibre.com.ni": "NI",
    "mercadolibre.com.pa": "PA",
    "mercadolibre.com.py": "PY",
    "mercadolibre.com.pe": "PE",
    "mercadolibre.com.sv": "SV",
    "mercadolibre.com.uy": "UY",
    "mercadolibre.com.ve": "VE",
}


def detect_country(url: str) -> str:
    """Detect the Scrapfly proxy country from the MercadoLibre URL domain."""
    for domain, country in MELI_COUNTRIES.items():
        if domain in url:
            return country
    return "CO"  # default


def clean_url(url: str) -> str:
    """Remove tracking parameters from MercadoLibre URLs."""
    return url.split("#")[0].split("?")[0]


def parse_product(result: ScrapeApiResponse) -> Dict:
    """Parse a MercadoLibre product page."""
    selector = result.selector
    final_url = result.context["url"]

    # --- JSON-LD structured data (most reliable source) ---
    ld_json = {}
    ld_scripts = selector.xpath('//script[@type="application/ld+json"]/text()').getall()
    for script in ld_scripts:
        try:
            data = json.loads(script)
            if data.get("@type") == "Product":
                ld_json = data
                break
        except (json.JSONDecodeError, AttributeError):
            continue

    offers = ld_json.get("offers", {})

    # --- Info ---
    title = (
        selector.xpath('//h1[contains(@class,"ui-pdp-title")]/text()').get()
        or ld_json.get("name")
    )

    subtitle = selector.xpath('//*[contains(@class,"ui-pdp-subtitle")]/text()').get() or ""
    sold_match = re.search(r"\+?([\d.]+)\s*vendidos?", subtitle)
    sold_count = int(sold_match.group(1).replace(".", "")) if sold_match else 0
    condition = subtitle.split("|")[0].strip() if "|" in subtitle else subtitle.strip()

    stock_text = selector.xpath(
        '//*[contains(@class,"ui-pdp-buybox__quantity__available")]/text()'
    ).get()
    stock_match = re.search(r"\+?([\d.]+)", stock_text) if stock_text else None
    available = int(stock_match.group(1).replace(".", "")) if stock_match else None

    # Try multiple image sources — data-zoom has hi-res, src has standard
    images = selector.xpath(
        '//figure[contains(@class,"ui-pdp-gallery__figure")]//img/@data-zoom'
    ).getall()
    if not images:
        images = selector.xpath(
            '//figure[contains(@class,"ui-pdp-gallery__figure")]//img/@src'
        ).getall()
    if not images:
        images = selector.xpath(
            '//*[contains(@class,"ui-pdp-gallery")]//img/@src'
        ).getall()
    # Filter out placeholder/base64 images and SVGs
    images = [
        img for img in images
        if img and not img.startswith("data:") and not img.endswith(".svg")
    ]
    # Fallback to JSON-LD image
    if not images and ld_json.get("image"):
        images = [ld_json["image"]]

    # Rating
    rating_text = selector.xpath(
        '//*[contains(@class,"ui-pdp-review__rating")]/text()'
    ).get()
    rating = float(rating_text) if rating_text else None

    review_count_text = selector.xpath(
        '//*[contains(@class,"ui-pdp-review__amount")]/text()'
    ).get()
    review_count = None
    if review_count_text:
        rc_match = re.search(r"\d+", review_count_text)
        review_count = int(rc_match.group()) if rc_match else None

    info = {
        "name": title,
        "productId": ld_json.get("productID") or ld_json.get("sku"),
        "link": final_url,
        "condition": condition or None,
        "images": images,
        "rating": rating,
        "reviewCount": review_count,
        "soldCount": sold_count,
        "availableCount": available,
    }

    # --- Pricing (from JSON-LD + HTML) ---
    current_price_text = selector.xpath(
        '//*[contains(@class,"ui-pdp-price__second-line")]'
        '//span[contains(@class,"andes-money-amount__fraction")]/text()'
    ).get()
    original_price_text = selector.xpath(
        '//*[contains(@class,"ui-pdp-price__original-value")]'
        '//span[contains(@class,"andes-money-amount__fraction")]/text()'
    ).get()
    discount_text = selector.xpath(
        '//*[contains(@class,"andes-money-amount__discount")]/text()'
    ).get()
    currency_symbol = selector.xpath(
        '//span[contains(@class,"andes-money-amount__currency-symbol")]/text()'
    ).get()

    def parse_meli_price(text: Optional[str]) -> Optional[float]:
        if not text:
            return None
        digits = text.replace(".", "").replace(",", ".")
        try:
            return float(digits)
        except ValueError:
            return None

    pricing = {
        "currency": offers.get("priceCurrency") or currency_symbol,
        "price": parse_meli_price(current_price_text) or offers.get("price"),
        "originalPrice": parse_meli_price(original_price_text),
        "discount": discount_text or "No discount",
    }

    # --- Specifications ---
    specifications = []
    for row in selector.xpath('//table[contains(@class,"andes-table")]//tr'):
        name = row.xpath(".//th//text()").get()
        value = row.xpath(".//td//text()").get()
        if name:
            specifications.append({"name": name.strip(), "value": (value or "").strip()})

    # --- Description ---
    desc_parts = selector.xpath(
        '//*[contains(@class,"ui-pdp-description__content")]//text()'
    ).getall()
    description = "\n".join(p.strip() for p in desc_parts if p.strip()) or None

    # --- Seller ---
    seller_texts = selector.xpath(
        '//*[contains(@class,"ui-pdp-seller__header__title")]//text()'
    ).getall()
    seller_name = None
    for t in seller_texts:
        t = t.strip()
        if t and t != "Vendido por" and t != "Vendido por ":
            seller_name = t
            break

    seller_sales_text = selector.xpath(
        '//*[contains(@class,"ui-pdp-seller__header__title")]'
        '//*[contains(text(),"ventas")]/text()'
    ).get()
    seller_sales = None
    if seller_sales_text:
        sm = re.search(r"\+?([\d.]+)", seller_sales_text)
        seller_sales = int(sm.group(1).replace(".", "")) if sm else None

    seller = {
        "name": seller_name,
        "sales": seller_sales,
    }

    # --- Reviews (up to 10, prioritize: with images+text first, then text only) ---
    reviews_with_images = []
    reviews_text_only = []
    review_elements = selector.xpath(
        '//*[contains(@class,"ui-review-capability-comments__comment")]'
    )
    for r in review_elements:
        # Rating from "Calificación X de 5" text
        rating_text = r.xpath(".//p/text()").get() or ""
        rating_match = re.search(r"(\d+)\s*de\s*5", rating_text)
        if not rating_match:
            continue  # empty/lazy-loaded element, skip

        # Content from dedicated __content element
        r_content = r.xpath(
            './/*[contains(@class,"__content")]/text()'
        ).get()

        # Review images
        review_imgs = r.xpath(".//img/@src").getall()

        # Skip reviews without text and without images
        if not r_content and not review_imgs:
            continue

        r_rating = int(rating_match.group(1))

        # Date fields: first is country, second is date
        date_texts = r.xpath(
            './/*[contains(@class,"__date")]/text()'
        ).getall()
        r_country = date_texts[0] if len(date_texts) >= 1 else None
        r_date = date_texts[1] if len(date_texts) >= 2 else None

        review = {
            "rating": r_rating,
            "country": r_country,
            "date": r_date,
            "content": r_content,
            "images": review_imgs,
        }

        if review_imgs:
            reviews_with_images.append(review)
        else:
            reviews_text_only.append(review)

    reviews = (reviews_with_images + reviews_text_only)[:10]

    return {
        "info": info,
        "pricing": pricing,
        "specifications": specifications,
        "description": description,
        "seller": seller,
        "reviews": reviews,
    }


async def scrape_mercadolibre_product(url: str) -> Dict:
    """Scrape a MercadoLibre product page by URL."""
    url = clean_url(url)
    country = detect_country(url)
    print(f"scraping product: {url} (country: {country})")

    result = await SCRAPFLY.async_scrape(
        ScrapeConfig(
            url,
            asp=True,
            country=country,
            proxy_pool="public_residential_pool",
            render_js=True,
            rendering_wait=5000,
            session=f"meli-{uuid.uuid4().hex[:8]}",
        )
    )

    data = parse_product(result)
    print(f"successfully scraped product: {url}")
    return data


async def main():
    product = await scrape_mercadolibre_product(
        url="https://www.mercadolivre.com.br/creatina-monohidratada-1kg-soldiers-nutrition-100-pura-importada-alta-performance-musculo-treino/p/MLB18725310#polycard_client=search-desktop&search_layout=grid&position=5&type=product&tracking_id=133682d6-c2cf-440a-a35d-7b4806e17482&wid=MLB2766771378&sid=search"
    )

    with open("meli_product.json", "w", encoding="utf-8") as f:
        json.dump(product, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    asyncio.run(main())
