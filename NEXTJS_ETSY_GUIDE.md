# Scraping Etsy Products with Scrapfly in Next.js

## Overview

This guide ports the Python `scrape_etsy.py` logic to a Next.js (App Router) API route using the Scrapfly TypeScript SDK. It covers: product info, pricing, variants, description, shop details, categories, and up to 10 user reviews (prioritized by images first, then text only). Etsy is a single-domain platform (`etsy.com`) with a fixed US proxy.

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
| `asp: true` | Enables Scrapfly's Anti Scraping Protection — bypasses Etsy's bot detection and CAPTCHAs |
| `country: "US"` | Fixed — Etsy operates on a single domain (`etsy.com`), no country detection needed |
| `proxy_pool: "public_residential_pool"` | Residential IPs are harder to detect than datacenter IPs |
| `render_js: false` | **Not needed** — Etsy serves product data server-side via HTML + JSON-LD (no JS rendering required) |
| `session: crypto.randomUUID()` | Unique session per request — each scrape looks like a new visitor |

**Key differences from other scrapers:**
- **No country detection** — Etsy is a single global domain, always uses US proxy
- **No `render_js`** — product data is server-rendered (saves API cost)
- **JSON-LD is the primary data source** — Etsy embeds structured `ProductGroup` data that provides title, images, rating, variants, and pricing
- **Review images are limited** — actual review photos are client-side rendered and won't appear in the HTML; only text reviews are reliably available

---

## 4. URL Cleaning

Etsy URLs can include locale prefixes (`/es/listing/`, `/fr/listing/`) and tracking parameters. The scraper normalizes them:

Create `lib/etsy.ts`:

```typescript
/**
 * Remove tracking parameters and locale prefix from Etsy URLs.
 *
 * Input:  "https://www.etsy.com/es/listing/4435414273/bolsa-de-lona?ls=r&ref=hp_opfy"
 * Output: "https://www.etsy.com/listing/4435414273/bolsa-de-lona"
 */
export function cleanUrl(url: string): string {
  // Remove query params and hash
  url = url.split("?")[0].split("#")[0];
  // Normalize /es/listing/ → /listing/
  url = url.replace(/etsy\.com\/\w{2}\/listing\//, "etsy.com/listing/");
  return url;
}
```

---

## 5. TypeScript Interfaces

Add to `lib/etsy.ts`:

```typescript
export interface EtsyProduct {
  info: {
    name: string | null;
    listingId: string | null;
    link: string;
    shop: string | null;
    shopLink: string | null;
    images: string[];
    rating: number | null;
    reviewCount: number | null;
    categories: string[];
    highlights: string[];
  };
  pricing: {
    currency: string | null;
    price: number | null;
    variants: EtsyVariant[] | null;
  };
  description: string | null;
  reviews: EtsyReview[];
}

export interface EtsyVariant {
  name: string | null;
  price: number | null;
  availability: "InStock" | "OutOfStock";
}

export interface EtsyReview {
  user: string | null;
  rating: number;
  date: string | null;
  content: string | null;
  images: string[];
}
```

---

## 6. Product Scraper

Create `lib/scrape-etsy.ts`:

```typescript
import { ScrapeConfig } from "scrapfly-sdk";
import { scrapflyClient } from "./scrapfly";
import {
  cleanUrl,
  EtsyProduct,
  EtsyVariant,
  EtsyReview,
} from "./etsy";

// Python XPath → CSS selector conversions used in this file:
// XPath: //script[@type="application/ld+json"]/text()           → CSS: script[type="application/ld+json"]
// XPath: //h1/text()                                            → CSS: h1
// XPath: //a[contains(@href,"/shop/")]//text()                  → CSS: a[href*="/shop/"]
// XPath: //*[contains(@class,"review-card")]                    → CSS: [class*="review-card"]
// XPath: .//input[@name="rating"]/@value                        → CSS: input[name="rating"]
// XPath: .//*[contains(@class,"wt-text-link-no-underline")]     → CSS: [class*="wt-text-link-no-underline"]
// XPath: .//*[contains(@class,"wt-text-body-small")]            → CSS: [class*="wt-text-body-small"]
// XPath: .//*[contains(@class,"wt-content-toggle")]             → CSS: [class*="wt-content-toggle"]
// XPath: .//img[contains(@src,"etsystatic")]/@src               → CSS: img[src*="etsystatic"]

function parseProduct(
  $: ReturnType<Awaited<ReturnType<typeof scrapflyClient.scrape>>["result"]["selector"]>,
  html: string,
  finalUrl: string
): EtsyProduct {
  // --- JSON-LD (primary data source) ---
  let ldJson: any = {};
  let breadcrumbData: any = null;

  $('script[type="application/ld+json"]').each((_, el) => {
    try {
      const data = JSON.parse($(el).text());
      if (
        (data["@type"] === "Product" || data["@type"] === "ProductGroup") &&
        !ldJson.name
      ) {
        ldJson = data;
      }
      if (data["@type"] === "BreadcrumbList") {
        breadcrumbData = data;
      }
    } catch {}
  });

  // --- Info ---
  const title = $("h1").first().text().trim() || ldJson.name || null;

  let listingId: string | null = null;
  const lidMatch = finalUrl.match(/\/listing\/(\d+)/);
  if (lidMatch) listingId = lidMatch[1];

  // Images from JSON-LD (hi-res)
  const images: string[] = [];
  for (const img of ldJson.image || []) {
    if (typeof img === "object") {
      const url = img.contentURL || img.thumbnail || "";
      if (url) images.push(url);
    } else if (typeof img === "string") {
      images.push(img);
    }
  }

  // Rating from JSON-LD
  const agg = ldJson.aggregateRating || {};
  const rating = agg.ratingValue ? parseFloat(agg.ratingValue) : null;
  const reviewCount = agg.reviewCount ? parseInt(agg.reviewCount, 10) : null;

  // Brand/shop from JSON-LD
  const brand = ldJson.brand?.name || null;

  // Shop name from HTML
  let shopName = $('a[href*="/shop/"]').first().text().trim() || brand;

  // Shop link
  let shopLink = $('a[href*="/shop/"]').first().attr("href") || null;
  if (shopLink) shopLink = shopLink.split("?")[0];

  // Description from JSON-LD
  const description = ldJson.description || null;

  // Categories from breadcrumbs JSON-LD
  const categories: string[] = [];
  if (breadcrumbData) {
    for (const item of breadcrumbData.itemListElement || []) {
      if (item.name) categories.push(item.name);
    }
  }

  // --- Highlights (product features/details) ---
  const highlights: string[] = [];
  $('[data-selector="product-details-highlights"] li').each((_, el) => {
    const text = $(el).find('[class*="wt-ml-xs-1"]').text().trim();
    if (text) highlights.push(text);
  });

  const info = {
    name: title,
    listingId,
    link: finalUrl,
    shop: shopName,
    shopLink,
    images,
    rating,
    reviewCount,
    categories,
    highlights,
  };

  // --- Pricing (from JSON-LD variants) ---
  let price: number | null = null;
  let currency: string | null = null;

  const variants: EtsyVariant[] = [];
  const hasVariant = ldJson.hasVariant || [];

  if (hasVariant.length) {
    const firstOffer = hasVariant[0].offers || {};
    if (typeof firstOffer === "object" && firstOffer.price) {
      price = parseFloat(firstOffer.price);
      currency = firstOffer.priceCurrency || null;
    }

    for (const v of hasVariant) {
      const vOffers = v.offers || {};
      variants.push({
        name: v.name || null,
        price:
          typeof vOffers === "object" && vOffers.price
            ? parseFloat(vOffers.price)
            : null,
        availability:
          typeof vOffers === "object" &&
          String(vOffers.availability || "").includes("InStock")
            ? "InStock"
            : "OutOfStock",
      });
    }
  }

  // Fallback to HTML price
  if (!price) {
    $('[class*="wt-text-title-larger"]')
      .contents()
      .each((_, node) => {
        if (price) return; // already found
        const text = $(node).text().trim();
        const pm = text.match(/[\d,.]+/);
        if (pm) {
          const priceStr = pm[0].replace(/,/g, "");
          const parsed = parseFloat(priceStr);
          if (!isNaN(parsed)) price = parsed;
          // Currency symbol
          const cm = text.match(/[^\d.,\s]+/);
          if (cm) currency = cm[0].trim();
        }
      });
  }

  const pricing = {
    currency,
    price,
    variants: variants.length ? variants : null,
  };

  // --- Reviews (up to 10, prioritize: images+text first, then text only) ---
  const reviewsWithImages: EtsyReview[] = [];
  const reviewsTextOnly: EtsyReview[] = [];

  $('[class*="review-card"]').each((_, el) => {
    // Rating from hidden input
    const ratingVal = $(el).find('input[name="rating"]').val() as
      | string
      | undefined;
    if (!ratingVal) return;
    const rRating = parseInt(ratingVal, 10);
    if (isNaN(rRating)) return;

    // User name
    const rName =
      $(el)
        .find('[class*="wt-text-link-no-underline"]')
        .first()
        .text()
        .trim() || null;

    // Date — look for "Month DD, YYYY" pattern
    let rDate: string | null = null;
    $(el)
      .find('[class*="wt-text-body-small"]')
      .each((_, small) => {
        const text = $(small).text().trim();
        if (/\w+ \d+, \d{4}/.test(text)) {
          rDate = text;
          return false; // break
        }
      });

    // Content from content-toggle
    const contentParts: string[] = [];
    $(el)
      .find('[class*="wt-content-toggle"]')
      .first()
      .contents()
      .each((_, node) => {
        const t = $(node).text().trim();
        if (t) contentParts.push(t);
      });
    const rContent = contentParts.join(" ") || null;

    // Review images — filter out user avatars (/iusa/ in path)
    const imgSet = new Set<string>();
    $(el)
      .find('img[src*="etsystatic"]')
      .each((_, img) => {
        const src = $(img).attr("src");
        if (src && !src.includes("/iusa/")) {
          imgSet.add(src);
        }
      });
    const reviewImgs = Array.from(imgSet);

    // Skip reviews without text AND without images
    if (!rContent && !reviewImgs.length) return;

    const review: EtsyReview = {
      user: rName,
      rating: rRating,
      date: rDate,
      content: rContent,
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
    description,
    reviews,
  };
}

export async function scrapeEtsyProduct(
  rawUrl: string
): Promise<EtsyProduct> {
  const url = cleanUrl(rawUrl);
  console.log(`scraping product: ${url}`);

  const apiResult = await scrapflyClient.scrape(
    new ScrapeConfig({
      url,
      asp: true,
      country: "US",
      proxy_pool: "public_residential_pool",
      session: `etsy-${crypto.randomUUID().slice(0, 8)}`,
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

**Important notes:**
- No `render_js` — Etsy is server-rendered, saves API cost
- Fixed `country: "US"` — no dynamic detection needed (single domain)
- Unique session per request via `crypto.randomUUID()`
- Review avatar images filtered out by checking for `/iusa/` in the URL path

---

## 7. Next.js API Route

Create `app/api/scrape-etsy/route.ts`:

```typescript
import { NextRequest, NextResponse } from "next/server";
import { scrapeEtsyProduct } from "@/lib/scrape-etsy";

export async function POST(request: NextRequest) {
  try {
    const { url } = await request.json();

    if (!url || !url.includes("etsy.com")) {
      return NextResponse.json(
        { error: "Invalid Etsy URL" },
        { status: 400 }
      );
    }

    const data = await scrapeEtsyProduct(url);
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
# English listing
curl -X POST http://localhost:3000/api/scrape-etsy \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.etsy.com/listing/4435414273/personalized-embroidered-canvas-bag"}'

# Localized URL (cleaned automatically: /es/listing/ → /listing/)
curl -X POST http://localhost:3000/api/scrape-etsy \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.etsy.com/es/listing/4435414273/bolsa-de-lona-bordada-personalizada?ls=r&ref=hp_opfy"}'
```

---

## 8. Key Differences from Python Version

| Aspect | Python | TypeScript/Next.js |
|---|---|---|
| Selectors | XPath via `parsel` | CSS via SDK's built-in `.selector()` (cheerio) |
| HTTP client | `scrapfly-sdk` (Python) | `scrapfly-sdk` (TS) |
| JSON-LD parsing | `json.loads()` on XPath text | `JSON.parse()` on cheerio text |
| Response URL | `result.context["url"]` | `apiResult.result.url` |
| Async pattern | `asyncio` | native `async/await` |
| Session ID | `uuid.uuid4()` | `crypto.randomUUID()` |
| Country | Fixed `"US"` | Fixed `"US"` |
| Image source | JSON-LD `image[]` | JSON-LD `image[]` (same) |
| Avatar filter | `"/iusa/" not in img` | `!src.includes("/iusa/")` |

---

## 9. File Structure

```
your-nextjs-app/
├── .env.local                  # SCRAPFLY_KEY=your-key
├── lib/
│   ├── scrapfly.ts             # Client singleton (shared)
│   ├── etsy.ts                 # cleanUrl, interfaces
│   └── scrape-etsy.ts          # scrapeEtsyProduct() + parseProduct()
└── app/
    └── api/
        └── scrape-etsy/
            └── route.ts        # POST endpoint
```

---

## 10. Data Sources

Etsy is unique compared to Amazon and MercadoLibre because most product data comes from **JSON-LD structured data** rather than HTML selectors:

| Data | Source | Notes |
|---|---|---|
| Title | HTML `<h1>` → JSON-LD fallback | HTML is preferred for display title |
| Listing ID | URL regex `/listing/(\d+)` | Extracted from final URL |
| Images | JSON-LD `image[]` | Hi-res; can be string or `{contentURL, thumbnail}` objects |
| Rating | JSON-LD `aggregateRating.ratingValue` | Float value |
| Review count | JSON-LD `aggregateRating.reviewCount` | Integer |
| Brand/Shop | JSON-LD `brand.name` + HTML `a[href*="/shop/"]` | Both sources combined |
| Description | JSON-LD `description` | Full product description |
| Categories | JSON-LD `BreadcrumbList.itemListElement[]` | From a separate JSON-LD block |
| Price | JSON-LD `hasVariant[0].offers.price` → HTML fallback | Variants are the primary source |
| Variants | JSON-LD `hasVariant[]` | Name, price, availability per variant |
| Highlights | HTML `[data-selector="product-details-highlights"]` | Materials, style, dimensions, tags (e.g. "Made to Order", "Recycled") |
| Reviews | HTML selectors | CSS classes on review cards |

---

## 11. Selector Reference

All HTML selectors used in `parseProduct()`, mapped from the Python XPath equivalents:

| Data | Python XPath | TypeScript CSS |
|---|---|---|
| JSON-LD scripts | `//script[@type="application/ld+json"]/text()` | `script[type="application/ld+json"]` |
| Title | `//h1/text()` | `h1` |
| Shop name | `//a[contains(@href,"/shop/")]//text()` | `a[href*="/shop/"]` |
| Shop link | `//a[contains(@href,"/shop/")]/@href` | `a[href*="/shop/"]` (`.attr("href")`) |
| Price fallback | `//*[contains(@class,"wt-text-title-larger")]//text()` | `[class*="wt-text-title-larger"]` |
| Highlights | `//*[@data-selector="product-details-highlights"]//li` | `[data-selector="product-details-highlights"] li` |
| Review card | `//*[contains(@class,"review-card")]` | `[class*="review-card"]` |
| Review rating | `.//input[@name="rating"]/@value` | `input[name="rating"]` (`.val()`) |
| Review user | `.//*[contains(@class,"wt-text-link-no-underline")]/text()` | `[class*="wt-text-link-no-underline"]` |
| Review date | `.//*[contains(@class,"wt-text-body-small")]/text()` | `[class*="wt-text-body-small"]` |
| Review content | `.//*[contains(@class,"wt-content-toggle")]//text()` | `[class*="wt-content-toggle"]` |
| Review images | `.//img[contains(@src,"etsystatic")]/@src` | `img[src*="etsystatic"]` |

---

## 12. Troubleshooting

**Getting null/empty results?**
- Etsy pages are server-rendered — `render_js` is NOT needed. If you're getting empty HTML, make sure `asp: true` and `proxy_pool: "public_residential_pool"` are set.
- The JSON-LD block is the primary data source. If it's missing, the product page structure may have changed.

**Images array is empty?**
- Images come from the JSON-LD `image` array, not from HTML `<img>` tags.
- Each image entry can be either a string URL or an object with `contentURL`/`thumbnail` fields — the scraper handles both formats.

**Reviews show no images?**
- This is expected. Actual review photos on Etsy are client-side rendered (loaded via JavaScript after page load). Since we don't use `render_js`, only text reviews are reliably captured.
- Avatar images (`/iusa/` URLs) are intentionally filtered out — they are profile pictures, not review photos.

**Localized URLs not working?**
- The scraper automatically normalizes locale prefixes: `/es/listing/` → `/listing/`, `/fr/listing/` → `/listing/`.
- Query parameters and hash fragments are stripped.

**Price is null?**
- Price is extracted from JSON-LD variants first. If no variants exist, it falls back to the HTML price display.
- Some listings may show "Price varies" — in that case, individual variant prices are available in `pricing.variants`.

**Getting blocked?**
- Each request already uses a unique session via `crypto.randomUUID()`.
- Etsy is generally less aggressive with bot detection than Amazon or MercadoLibre.
- The fixed US proxy works for all Etsy listings regardless of the seller's country.
