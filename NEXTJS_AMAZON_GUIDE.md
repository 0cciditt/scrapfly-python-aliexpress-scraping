# Scraping Amazon Products with Scrapfly in Next.js

## Overview

This guide ports the Python `scrape_amazon.py` logic to a Next.js (App Router) API route using the Scrapfly TypeScript SDK. It covers: product info, pricing, features, overview, specifications, and up to 10 user reviews (prioritized by images first, then text only). Works across all 21 Amazon country domains.

---

## 1. Installation

```bash
npm install scrapfly-sdk
```

- `scrapfly-sdk` — Scrapfly TypeScript SDK (uses cheerio internally, exposed via `.selector()` for CSS queries)

Add your API key to `.env.local`:

```
SCRAPFLY_KEY=your-api-key-here
```

---

## 2. Scrapfly Client Config

Create `lib/scrapfly.ts` (skip if you already have it from another scraper):

```typescript
import { ScrapflyClient } from "scrapfly-sdk";

export const scrapflyClient = new ScrapflyClient({
  key: process.env.SCRAPFLY_KEY!,
});
```

---

## 3. Anti-blocking Strategy

| Setting | Purpose |
|---|---|
| `asp: true` | Enables Scrapfly's Anti Scraping Protection — bypasses Amazon's bot detection, CAPTCHAs, and fingerprinting |
| `country: detected` | Proxy country auto-detected from URL domain (e.g. `amazon.de` → `"DE"`). Uses a matching local proxy so Amazon serves the correct localized page |
| `proxy_pool: "public_residential_pool"` | Residential IPs are harder to detect than datacenter IPs |
| `render_js: false` | **Not needed** — Amazon serves full product HTML server-side (no JS rendering required) |
| `session: crypto.randomUUID()` | Unique session per request — each scrape looks like a new visitor, preventing fingerprint correlation and blocking |

**Key difference from MercadoLibre:** Amazon does NOT require `render_js` or `rendering_wait`. The product page is fully server-rendered HTML, which saves API cost and makes scraping faster.

---

## 4. Country Detection

Amazon operates across 21 country-specific domains. The scraper auto-detects the country from the URL and uses a matching proxy.

Create `lib/amazon.ts`:

```typescript
/**
 * Amazon country domains → Scrapfly proxy country codes.
 * Each country site has its own domain, currency, and product catalog.
 */
const AMAZON_COUNTRIES: Record<string, string> = {
  "amazon.com": "US",       // United States
  "amazon.co.uk": "GB",     // United Kingdom
  "amazon.de": "DE",        // Germany
  "amazon.fr": "FR",        // France
  "amazon.es": "ES",        // Spain
  "amazon.it": "IT",        // Italy
  "amazon.co.jp": "JP",     // Japan
  "amazon.ca": "CA",        // Canada
  "amazon.com.au": "AU",    // Australia
  "amazon.com.br": "BR",    // Brazil
  "amazon.com.mx": "MX",    // Mexico
  "amazon.in": "IN",        // India
  "amazon.nl": "NL",        // Netherlands
  "amazon.sg": "SG",        // Singapore
  "amazon.se": "SE",        // Sweden
  "amazon.pl": "PL",        // Poland
  "amazon.com.be": "BE",    // Belgium
  "amazon.com.tr": "TR",    // Turkey
  "amazon.sa": "SA",        // Saudi Arabia
  "amazon.ae": "AE",        // UAE
  "amazon.eg": "EG",        // Egypt
};

/**
 * Detect the Scrapfly proxy country from the Amazon URL domain.
 * Falls back to "US" if domain is not recognized.
 */
export function detectCountry(url: string): string {
  for (const [domain, country] of Object.entries(AMAZON_COUNTRIES)) {
    if (url.includes(domain)) {
      return country;
    }
  }
  return "US";
}
```

---

## 5. URL Cleaning

Amazon URLs can be extremely long with tracking parameters. The scraper simplifies them to just `/dp/ASIN`:

Add to `lib/amazon.ts`:

```typescript
/**
 * Simplify Amazon URL to just /dp/ASIN.
 * Handles both /dp/ASIN and /gp/product/ASIN formats.
 *
 * Input:  "https://www.amazon.es/Recortadora-afeitadora/.../dp/B0DCFP3Z14/?pd_rd_w=Zq476&..."
 * Output: "https://www.amazon.es/dp/B0DCFP3Z14"
 */
export function cleanUrl(url: string): string {
  const asinMatch = url.match(/\/(?:dp|gp\/product)\/([A-Z0-9]{10})/);
  if (asinMatch) {
    const domainMatch = url.match(/(https?:\/\/[^/]+)/);
    const domain = domainMatch ? domainMatch[1] : "https://www.amazon.com";
    return `${domain}/dp/${asinMatch[1]}`;
  }
  return url.split("?")[0].split("#")[0];
}
```

---

## 6. Price Parser

Amazon uses different price formats across countries (e.g. `$1,234.56` in US, `1.234,56€` in Europe). The parser auto-detects the format:

Add to `lib/amazon.ts`:

```typescript
/**
 * Parse Amazon price strings across all locales.
 * Handles both formats:
 *   "$1,234.56"   → 1234.56  (US/UK: comma=thousands, dot=decimal)
 *   "1.234,56 €"  → 1234.56  (EU: dot=thousands, comma=decimal)
 *
 * Detection: compares position of last comma vs last dot.
 * If comma comes after dot → EU format. Otherwise → US format.
 */
export function parseAmazonPrice(text: string | null): number | null {
  if (!text) return null;
  const nums = text.replace(/[^\d.,]/g, "");
  if (!nums) return null;

  const lastComma = nums.lastIndexOf(",");
  const lastDot = nums.lastIndexOf(".");

  let normalized: string;
  if (lastComma > lastDot) {
    // EU format: 1.234,56 → remove dots, replace comma with dot
    normalized = nums.replace(/\./g, "").replace(",", ".");
  } else {
    // US format: 1,234.56 → remove commas
    normalized = nums.replace(/,/g, "");
  }

  const value = parseFloat(normalized);
  return isNaN(value) ? null : value;
}
```

---

## 7. TypeScript Interfaces

Add to `lib/amazon.ts`:

```typescript
export interface AmazonProduct {
  info: {
    name: string | null;
    asin: string | null;
    link: string;
    brand: string | null;
    images: string[];
    rating: number | null;
    reviewCount: number | null;
    availability: string | null;
    categories: string[];
  };
  pricing: {
    currency: string | null;
    price: number | null;
    originalPrice: number | null;
    discount: string;
  };
  features: string[];
  overview: Record<string, string>;
  specifications: { name: string; value: string }[];
  reviews: AmazonReview[];
}

export interface AmazonReview {
  user: string | null;
  rating: number | null;
  title: string | null;
  date: string | null;
  content: string | null;
  images: string[];
}
```

---

## 8. Product Scraper

Create `lib/scrape-amazon.ts`:

```typescript
import { ScrapeConfig } from "scrapfly-sdk";
import { scrapflyClient } from "./scrapfly";
import {
  detectCountry,
  cleanUrl,
  parseAmazonPrice,
  AmazonProduct,
  AmazonReview,
} from "./amazon";

// Python XPath → CSS selector conversions used in this file:
// XPath: //span[@id="productTitle"]/text()                     → CSS: #productTitle
// XPath: //a[@id="bylineInfo"]/text()                          → CSS: #bylineInfo
// XPath: //*[contains(@class,"a-price")]//span[...]            → CSS: .a-price .a-offscreen
// XPath: //span[contains(@class,"reviewCountTextLinkedHistogram")]/@title → CSS: .reviewCountTextLinkedHistogram
// XPath: //*[@id="acrCustomerReviewText"]/text()               → CSS: #acrCustomerReviewText
// XPath: //div[@id="availability"]//span/text()                → CSS: #availability span
// XPath: //*[@id="wayfinding-breadcrumbs_feature_div"]//a      → CSS: #wayfinding-breadcrumbs_feature_div a
// XPath: //*[@id="feature-bullets"]//li//span[...]             → CSS: #feature-bullets li .a-list-item
// XPath: //*[@id="productOverview_feature_div"]//tr            → CSS: #productOverview_feature_div tr
// XPath: //table[contains(@class,"a-keyvalue")]//tr            → CSS: table.a-keyvalue tr
// XPath: //*[@data-hook="review"]                              → CSS: [data-hook="review"]

function parseProduct(
  $: ReturnType<Awaited<ReturnType<typeof scrapflyClient.scrape>>["result"]["selector"]>,
  html: string,
  finalUrl: string
): AmazonProduct {
  // --- ASIN ---
  let asin = $('input#ASIN').val() as string | undefined || null;
  if (!asin) {
    const asinMatch = finalUrl.match(/\/dp\/([A-Z0-9]{10})/);
    asin = asinMatch ? asinMatch[1] : null;
  }

  // --- Title ---
  const title = $('#productTitle').text().trim() || null;

  // --- Brand ---
  let brand = $('#bylineInfo').text().trim() || null;
  if (brand) {
    brand = brand
      .replace("Visit the ", "")
      .replace(" Store", "")
      .replace("Brand: ", "")
      .trim();
  }

  // --- Price ---
  const priceText = $('.a-price .a-offscreen').first().text() || null;
  const originalPriceText =
    $('[class*="basisPrice"] .a-offscreen').first().text() || null;
  const discountText =
    $('[class*="savingsPercentage"]').first().text() || null;

  // Currency symbol
  let currency: string | null = null;
  if (priceText) {
    const currMatch = priceText.match(/[^\d.,\s]+/);
    currency = currMatch ? currMatch[0].trim() : null;
  }

  const pricing = {
    currency,
    price: parseAmazonPrice(priceText),
    originalPrice: parseAmazonPrice(originalPriceText),
    discount: discountText || "No discount",
  };

  // --- Rating ---
  const ratingTitle = $('[class*="reviewCountTextLinkedHistogram"]').attr("title") || "";
  let rating: number | null = null;
  const ratingMatch = ratingTitle.match(/([\d.]+)/);
  if (ratingMatch) {
    rating = parseFloat(ratingMatch[1]);
  }

  const reviewCountText = $('#acrCustomerReviewText').text();
  let reviewCount: number | null = null;
  if (reviewCountText) {
    const rc = reviewCountText.replace(/[^\d]/g, "");
    reviewCount = rc ? parseInt(rc, 10) : null;
  }

  // --- Availability ---
  const availability = $('#availability span').first().text().trim() || null;

  // --- Images (from hiRes JS data in raw HTML) ---
  const hiresMatches = html.match(/"hiRes":"(https:\/\/[^"]+)"/g) || [];
  const imageSet = new Set<string>();
  for (const m of hiresMatches) {
    const urlMatch = m.match(/"hiRes":"(https:\/\/[^"]+)"/);
    if (urlMatch) imageSet.add(urlMatch[1]);
  }
  const images = Array.from(imageSet);

  // --- Categories (breadcrumbs) ---
  const categories: string[] = [];
  $('#wayfinding-breadcrumbs_feature_div a').each((_, el) => {
    const text = $(el).text().trim();
    if (text) categories.push(text);
  });

  const info = {
    name: title,
    asin,
    link: finalUrl,
    brand,
    images,
    rating,
    reviewCount,
    availability,
    categories,
  };

  // --- Features (bullet points) ---
  const features: string[] = [];
  $('#feature-bullets li .a-list-item').each((_, el) => {
    const text = $(el).text().trim();
    if (text) features.push(text);
  });

  // --- Product Overview (key-value table) ---
  const overview: Record<string, string> = {};
  $('#productOverview_feature_div tr').each((_, el) => {
    const label = $(el).find('td').first().find('span').text().trim();
    const value = $(el).find('td').last().find('span').text().trim();
    if (label && value) {
      overview[label] = value;
    }
  });

  // --- Specifications (detailed tables) ---
  const specifications: { name: string; value: string }[] = [];
  $('table[class*="a-keyvalue"] tr').each((_, el) => {
    const th = $(el).find('th').text().trim();
    const td = $(el).find('td').text().trim();
    if (th && td) {
      specifications.push({ name: th, value: td });
    }
  });

  // --- Reviews (up to 10, prioritize: images+text first, then text only) ---
  const reviewsWithImages: AmazonReview[] = [];
  const reviewsTextOnly: AmazonReview[] = [];

  $('[data-hook="review"]').each((_, el) => {
    const userName = $(el).find('[class*="profile-name"]').first().text().trim() || null;

    // Rating
    const ratingText = $(el).find('[class*="review-rating"]').first().text();
    let rRating: number | null = null;
    const rm = ratingText.match(/([\d.]+)/);
    if (rm) rRating = parseFloat(rm[1]);

    // Title — skip "X out of Y stars" text
    let rTitle: string | null = null;
    $(el).find('[data-hook="review-title"]').first().contents().each((_, node) => {
      const t = $(node).text().trim();
      if (t && !t.includes("out of") && !rTitle) {
        rTitle = t;
      }
    });

    // Body
    const bodyParts: string[] = [];
    $(el).find('[data-hook="review-body"]').first().contents().each((_, node) => {
      const t = $(node).text().trim();
      if (t) bodyParts.push(t);
    });
    const rBody = bodyParts.join(" ") || null;

    // Date
    const rDate = $(el).find('[data-hook="review-date"]').text().trim() || null;

    // Review images — upgrade thumbnails to full size
    const reviewImgs: string[] = [];
    $(el).find('img[data-hook="review-image"]').each((_, img) => {
      const src = $(img).attr("src");
      if (src) {
        // Upgrade thumbnail: _SY88_ or similar → _SL1500_
        reviewImgs.push(src.replace(/_SY\d+_/, "_SL1500_"));
      }
    });

    // Skip reviews without text AND without images
    if (!rBody && !reviewImgs.length) return;

    const review: AmazonReview = {
      user: userName,
      rating: rRating,
      title: rTitle,
      date: rDate,
      content: rBody,
      images: reviewImgs,
    };

    if (reviewImgs.length) {
      reviewsWithImages.push(review);
    } else {
      reviewsTextOnly.push(review);
    }
  });

  const reviews = [...reviewsWithImages, ...reviewsTextOnly].slice(0, 10);

  return {
    info,
    pricing,
    features,
    overview,
    specifications,
    reviews,
  };
}

export async function scrapeAmazonProduct(
  rawUrl: string
): Promise<AmazonProduct> {
  const url = cleanUrl(rawUrl);
  const country = detectCountry(url);
  console.log(`scraping product: ${url} (country: ${country})`);

  const apiResult = await scrapflyClient.scrape(
    new ScrapeConfig({
      url,
      asp: true,
      country,
      proxy_pool: "public_residential_pool",
      session: `amazon-${crypto.randomUUID().slice(0, 8)}`,
    })
  );

  const $ = apiResult.result.selector;
  const html = apiResult.result.content;
  const finalUrl = apiResult.result.url ?? url;
  const data = parseProduct($, html, finalUrl);

  console.log(`successfully scraped product: ${url}`);
  return data;
}
```

**Important:** Notice there is NO `render_js` or `rendering_wait` in the `ScrapeConfig`. Amazon pages are server-rendered, so we don't need JavaScript execution — this saves API cost.

---

## 9. Next.js API Route

Create `app/api/scrape-amazon/route.ts`:

```typescript
import { NextRequest, NextResponse } from "next/server";
import { scrapeAmazonProduct } from "@/lib/scrape-amazon";

export async function POST(request: NextRequest) {
  try {
    const { url } = await request.json();

    if (!url || !url.includes("amazon.")) {
      return NextResponse.json(
        { error: "Invalid Amazon URL" },
        { status: 400 }
      );
    }

    const data = await scrapeAmazonProduct(url);
    return NextResponse.json(data);
  } catch (error: any) {
    console.error("Scrape error:", error);
    return NextResponse.json(
      { error: error.message || "Scrape failed" },
      { status: 500 }
    );
  }
}
```

### Usage

```bash
# United States
curl -X POST http://localhost:3000/api/scrape-amazon \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.amazon.com/dp/B0DCFP3Z14"}'

# Spain (full URL with tracking params — gets cleaned automatically)
curl -X POST http://localhost:3000/api/scrape-amazon \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.amazon.es/Recortadora-afeitadora/dp/B0DCFP3Z14/?pd_rd_w=Zq476&content-id=amzn1"}'

# Germany
curl -X POST http://localhost:3000/api/scrape-amazon \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.amazon.de/dp/B09V3KXJPB"}'

# Japan
curl -X POST http://localhost:3000/api/scrape-amazon \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.amazon.co.jp/dp/B0DCFP3Z14"}'
```

---

## 10. Key Differences from Python Version

| Aspect | Python | TypeScript/Next.js |
|---|---|---|
| Selectors | XPath via `parsel` | CSS via SDK's built-in `.selector()` (cheerio) |
| HTTP client | `scrapfly-sdk` (Python) | `scrapfly-sdk` (TS) |
| Image extraction | `re.findall()` on raw HTML | `String.match()` + regex on raw HTML |
| Response URL | `result.context["url"]` | `apiResult.result.url` |
| Async pattern | `asyncio` | native `async/await` |
| Session ID | `uuid.uuid4()` | `crypto.randomUUID()` |
| Country detection | Same logic | Same logic |
| Price parsing | Same locale-aware logic | Same locale-aware logic |

---

## 11. File Structure

```
your-nextjs-app/
├── .env.local                      # SCRAPFLY_KEY=your-key
├── lib/
│   ├── scrapfly.ts                 # Client singleton (shared)
│   ├── amazon.ts                   # detectCountry, cleanUrl, parseAmazonPrice, interfaces
│   └── scrape-amazon.ts            # scrapeAmazonProduct() + parseProduct()
└── app/
    └── api/
        └── scrape-amazon/
            └── route.ts            # POST endpoint
```

---

## 12. Supported Country Domains

| Country | Domain | Proxy Code |
|---|---|---|
| United States | `amazon.com` | US |
| United Kingdom | `amazon.co.uk` | GB |
| Germany | `amazon.de` | DE |
| France | `amazon.fr` | FR |
| Spain | `amazon.es` | ES |
| Italy | `amazon.it` | IT |
| Japan | `amazon.co.jp` | JP |
| Canada | `amazon.ca` | CA |
| Australia | `amazon.com.au` | AU |
| Brazil | `amazon.com.br` | BR |
| Mexico | `amazon.com.mx` | MX |
| India | `amazon.in` | IN |
| Netherlands | `amazon.nl` | NL |
| Singapore | `amazon.sg` | SG |
| Sweden | `amazon.se` | SE |
| Poland | `amazon.pl` | PL |
| Belgium | `amazon.com.be` | BE |
| Turkey | `amazon.com.tr` | TR |
| Saudi Arabia | `amazon.sa` | SA |
| UAE | `amazon.ae` | AE |
| Egypt | `amazon.eg` | EG |

---

## 13. Selector Reference

All selectors used in `parseProduct()`, mapped from the Python XPath equivalents:

| Data | Python XPath | TypeScript CSS |
|---|---|---|
| ASIN | `//input[@id="ASIN"]/@value` | `input#ASIN` (`.val()`) |
| Title | `//span[@id="productTitle"]/text()` | `#productTitle` |
| Brand | `//a[@id="bylineInfo"]/text()` | `#bylineInfo` |
| Price | `//*[contains(@class,"a-price")]//span[contains(@class,"a-offscreen")]` | `.a-price .a-offscreen` |
| Original price | `//*[contains(@class,"basisPrice")]//span[contains(@class,"a-offscreen")]` | `[class*="basisPrice"] .a-offscreen` |
| Discount | `//*[contains(@class,"savingsPercentage")]` | `[class*="savingsPercentage"]` |
| Rating | `//span[contains(@class,"reviewCountTextLinkedHistogram")]/@title` | `[class*="reviewCountTextLinkedHistogram"]` (`.attr("title")`) |
| Review count | `//*[@id="acrCustomerReviewText"]/text()` | `#acrCustomerReviewText` |
| Availability | `//div[@id="availability"]//span/text()` | `#availability span` |
| Images | `re.findall(r'"hiRes":"(https://[^"]+)"')` | `html.match(/"hiRes":"(https:\/\/[^"]+)"/g)` |
| Categories | `//*[@id="wayfinding-breadcrumbs_feature_div"]//a` | `#wayfinding-breadcrumbs_feature_div a` |
| Features | `//*[@id="feature-bullets"]//li//span[contains(@class,"a-list-item")]` | `#feature-bullets li .a-list-item` |
| Overview | `//*[@id="productOverview_feature_div"]//tr` | `#productOverview_feature_div tr` |
| Specs | `//table[contains(@class,"a-keyvalue")]//tr` | `table[class*="a-keyvalue"] tr` |
| Review card | `//*[@data-hook="review"]` | `[data-hook="review"]` |
| Review user | `.//*[contains(@class,"profile-name")]` | `[class*="profile-name"]` |
| Review rating | `.//*[contains(@class,"review-rating")]` | `[class*="review-rating"]` |
| Review title | `.//*[@data-hook="review-title"]` | `[data-hook="review-title"]` |
| Review body | `.//*[@data-hook="review-body"]` | `[data-hook="review-body"]` |
| Review date | `.//*[@data-hook="review-date"]` | `[data-hook="review-date"]` |
| Review images | `.//img[contains(@data-hook,"review-image")]/@src` | `img[data-hook="review-image"]` |

---

## 14. Troubleshooting

**Getting null/empty results?**
- Amazon pages are server-rendered — `render_js` is NOT needed. If you're getting empty HTML, the issue is likely bot detection. Make sure `asp: true` and `proxy_pool: "public_residential_pool"` are set.
- Check that the ASIN in the URL is valid (10 alphanumeric characters).

**Images array is empty?**
- Product images are NOT in standard `<img>` tags. They're embedded in inline JavaScript as `"hiRes":"url"` strings. The scraper extracts them via regex on the raw HTML, not CSS selectors.

**Price is null?**
- Some products show "See price in cart" instead of a visible price. The scraper can only extract displayed prices.
- For European domains (`amazon.de`, `amazon.fr`, etc.), the price parser auto-detects the `1.234,56` format.

**Reviews are empty?**
- Amazon shows a limited number of reviews on the product page itself. The scraper extracts only what's on the page (typically 8-10 reviews).
- Reviews without text AND images are filtered out by design.
- Review image URLs are automatically upgraded from thumbnails (`_SY88_`) to full size (`_SL1500_`).

**Brand shows extra text?**
- The scraper automatically cleans brand names by removing "Visit the ", " Store", and "Brand: " prefixes that Amazon adds to the byline link.

**Getting blocked?**
- Each request already uses a unique session via `crypto.randomUUID()`.
- The URL is automatically cleaned to `/dp/ASIN` — tracking parameters are stripped to avoid triggering bot detection.
- If persistent blocking occurs, the residential proxy pool combined with ASP should handle most cases.
