"""Scrape Amazon product pages across all country domains."""

import json
import re
import asyncio
import uuid
from typing import Dict, List, Optional

from scrapfly import ScrapeApiResponse, ScrapeConfig

from config import SCRAPFLY

# Amazon country domains → Scrapfly proxy country codes
AMAZON_COUNTRIES = {
    "amazon.com": "US",
    "amazon.co.uk": "GB",
    "amazon.de": "DE",
    "amazon.fr": "FR",
    "amazon.es": "ES",
    "amazon.it": "IT",
    "amazon.co.jp": "JP",
    "amazon.ca": "CA",
    "amazon.com.au": "AU",
    "amazon.com.br": "BR",
    "amazon.com.mx": "MX",
    "amazon.in": "IN",
    "amazon.nl": "NL",
    "amazon.sg": "SG",
    "amazon.se": "SE",
    "amazon.pl": "PL",
    "amazon.com.be": "BE",
    "amazon.com.tr": "TR",
    "amazon.sa": "SA",
    "amazon.ae": "AE",
    "amazon.eg": "EG",
}


def detect_country(url: str) -> str:
    """Detect the Scrapfly proxy country from the Amazon URL domain."""
    for domain, country in AMAZON_COUNTRIES.items():
        if domain in url:
            return country
    return "US"


def clean_url(url: str) -> str:
    """Simplify Amazon URL to just /dp/ASIN."""
    asin_match = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", url)
    if asin_match:
        # Preserve the domain
        domain_match = re.search(r"(https?://[^/]+)", url)
        domain = domain_match.group(1) if domain_match else "https://www.amazon.com"
        return f"{domain}/dp/{asin_match.group(1)}"
    return url.split("?")[0].split("#")[0]


def parse_product(result: ScrapeApiResponse) -> Dict:
    """Parse an Amazon product page."""
    selector = result.selector
    html = result.content
    final_url = result.context["url"]

    # --- ASIN ---
    asin = selector.xpath('//input[@id="ASIN"]/@value').get()
    if not asin:
        asin_match = re.search(r"/dp/([A-Z0-9]{10})", final_url)
        asin = asin_match.group(1) if asin_match else None

    # --- Title ---
    title = selector.xpath('//span[@id="productTitle"]/text()').get()
    title = title.strip() if title else None

    # --- Brand ---
    brand = selector.xpath('//a[@id="bylineInfo"]/text()').get()
    if brand:
        brand = brand.replace("Visit the ", "").replace(" Store", "").replace("Brand: ", "").strip()

    # --- Price ---
    price_text = selector.xpath(
        '//*[contains(@class,"a-price")]//span[contains(@class,"a-offscreen")]/text()'
    ).get()
    original_price_text = selector.xpath(
        '//*[contains(@class,"basisPrice")]//span[contains(@class,"a-offscreen")]/text()'
    ).get()
    discount_text = selector.xpath(
        '//*[contains(@class,"savingsPercentage")]/text()'
    ).get()

    def parse_amazon_price(text: Optional[str]) -> Optional[float]:
        if not text:
            return None
        nums = re.sub(r"[^\d.,]", "", text)
        if not nums:
            return None
        # Handle formats: $1,234.56 or 1.234,56€
        last_comma = nums.rfind(",")
        last_dot = nums.rfind(".")
        if last_comma > last_dot:
            nums = nums.replace(".", "").replace(",", ".")
        else:
            nums = nums.replace(",", "")
        try:
            return float(nums)
        except ValueError:
            return None

    # Currency symbol
    currency = None
    if price_text:
        curr_match = re.search(r"[^\d.,\s]+", price_text)
        currency = curr_match.group().strip() if curr_match else None

    pricing = {
        "currency": currency,
        "price": parse_amazon_price(price_text),
        "originalPrice": parse_amazon_price(original_price_text),
        "discount": discount_text or "No discount",
    }

    # --- Rating ---
    rating_text = selector.xpath(
        '//span[contains(@class,"reviewCountTextLinkedHistogram")]/@title'
    ).get()
    rating = None
    if rating_text:
        rm = re.search(r"([\d.]+)", rating_text)
        rating = float(rm.group(1)) if rm else None

    review_count_text = selector.xpath(
        '//*[@id="acrCustomerReviewText"]/text()'
    ).get()
    review_count = None
    if review_count_text:
        rc = re.sub(r"[^\d]", "", review_count_text)
        review_count = int(rc) if rc else None

    # --- Availability ---
    availability = selector.xpath('//div[@id="availability"]//span/text()').get()
    availability = availability.strip() if availability else None

    # --- Images (from hiRes JS data) ---
    hires = re.findall(r'"hiRes":"(https://[^"]+)"', html)
    images = list(dict.fromkeys(hires))  # deduplicate preserving order

    # --- Categories ---
    cats = selector.xpath(
        '//*[@id="wayfinding-breadcrumbs_feature_div"]//a/text()'
    ).getall()
    categories = [c.strip() for c in cats if c.strip()]

    # --- Features (bullet points) ---
    bullets = selector.xpath(
        '//*[@id="feature-bullets"]//li//span[contains(@class,"a-list-item")]/text()'
    ).getall()
    features = [b.strip() for b in bullets if b.strip()]

    # --- Product overview (key-value table) ---
    overview = {}
    for row in selector.xpath('//*[@id="productOverview_feature_div"]//tr'):
        label = row.xpath(".//td[1]//span/text()").get()
        value = row.xpath(".//td[2]//span/text()").get()
        if label and value:
            overview[label.strip()] = value.strip()

    # --- Specifications (detailed tables) ---
    specifications = []
    for row in selector.xpath(
        '//table[contains(@class,"a-keyvalue")]//tr'
    ):
        th = row.xpath(".//th/text()").get()
        td_texts = row.xpath(".//td//text()").getall()
        td = " ".join(t.strip() for t in td_texts if t.strip())
        if th and td:
            specifications.append({"name": th.strip(), "value": td})

    # --- Variants (Color, Size, Style, Pattern, etc.) ---
    # Amazon uses "twister" for variants. Each dimension has a row:
    #   #inline-twister-row-{dim}      (e.g. color_name, size_name, style_name)
    # Inside: title text, options list. Options can be image swatches
    # (color) or text swatches (size).
    variants = []
    for row in selector.xpath('//*[starts-with(@id,"inline-twister-row-")]'):
        # Dimension title — e.g. "Color:" → "Color"
        title_text = row.xpath(
            './/*[starts-with(@id,"inline-twister-dim-title-")]'
            '//span[contains(@class,"a-color-secondary")]/text()'
        ).get()
        name = None
        if title_text:
            name = title_text.strip().rstrip(":").strip() or None

        options = []
        for li in row.xpath('.//li[contains(@class,"inline-twister-swatch")]'):
            selected = li.xpath("@data-initiallyselected").get() == "true"
            unavailable = li.xpath("@data-initiallyunavailable").get() == "true"
            variant_asin = li.xpath("@data-asin").get()

            # Image swatch (color) — get alt + src; upgrade thumbnail size
            img_alt = li.xpath(".//img/@alt").get()
            img_src = li.xpath(".//img/@src").get()
            if img_src:
                img_src = re.sub(r"\._SS\d+_", "._SL500_", img_src)

            # Text swatch (size, style, pattern)
            text_val = li.xpath(
                './/span[contains(@class,"swatch-title-text-display")]/text()'
            ).get()

            value = (img_alt or text_val or "").strip()
            if not value:
                continue

            options.append(
                {
                    "value": value,
                    "image": img_src,
                    "asin": variant_asin,
                    "selected": selected,
                    "available": not unavailable,
                }
            )

        if options:
            variants.append({"name": name, "options": options})

    info = {
        "name": title,
        "asin": asin,
        "link": final_url,
        "brand": brand,
        "images": images,
        "rating": rating,
        "reviewCount": review_count,
        "availability": availability,
        "categories": categories,
    }

    # --- Reviews (up to 10, prioritize with images+text, then text only) ---
    reviews_with_images = []
    reviews_text_only = []
    for r in selector.xpath('//*[@data-hook="review"]'):
        name = r.xpath('.//*[contains(@class,"profile-name")]//text()').get()

        rating_r = r.xpath(
            './/*[contains(@class,"review-rating")]//text()'
        ).get()
        r_rating = None
        if rating_r:
            rm = re.search(r"([\d.]+)", rating_r)
            r_rating = float(rm.group(1)) if rm else None

        title_parts = r.xpath('.//*[@data-hook="review-title"]//text()').getall()
        r_title = None
        for t in title_parts:
            t = t.strip()
            if t and "out of" not in t:
                r_title = t
                break

        body_parts = r.xpath('.//*[@data-hook="review-body"]//text()').getall()
        r_body = " ".join(t.strip() for t in body_parts if t.strip()) or None

        date_text = r.xpath('.//*[@data-hook="review-date"]/text()').get()

        review_imgs = r.xpath(
            './/img[contains(@data-hook,"review-image")]/@src'
        ).getall()
        # Upgrade thumbnail URLs to full size
        review_imgs = [
            re.sub(r"\._SY\d+_", "._SL1500_", img) for img in review_imgs
        ]

        if not r_body and not review_imgs:
            continue

        review = {
            "user": name,
            "rating": r_rating,
            "title": r_title,
            "date": date_text,
            "content": r_body,
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
        "variants": variants,
        "features": features,
        "overview": overview,
        "specifications": specifications,
        "reviews": reviews,
    }


async def scrape_amazon_product(url: str) -> Dict:
    """Scrape an Amazon product page by URL."""
    url = clean_url(url)
    country = detect_country(url)
    print(f"scraping product: {url} (country: {country})")

    result = await SCRAPFLY.async_scrape(
        ScrapeConfig(
            url,
            asp=True,
            country=country,
            proxy_pool="public_residential_pool",
            session=f"amazon-{uuid.uuid4().hex[:8]}",
        )
    )

    data = parse_product(result)
    print(f"successfully scraped product: {url}")
    return data


async def main():
    product = await scrape_amazon_product(
        url="https://www.amazon.com/-/es/gp/product/B0DY2PB7RB/ref=ox_sc_saved_image_10?smid=A95DP87XYU2J1&th=1"
    )

    with open("amazon_product.json", "w", encoding="utf-8") as f:
        json.dump(product, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    asyncio.run(main())
