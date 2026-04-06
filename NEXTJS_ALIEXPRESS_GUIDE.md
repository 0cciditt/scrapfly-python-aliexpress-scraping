# Scraping AliExpress Products with Scrapfly in Next.js

## Overview

This guide ports the Python `scrape_product.py` logic to a Next.js (App Router) API route using the Scrapfly TypeScript SDK. It covers the same features: product info, pricing, specs, seller, FAQs, and 10 user reviews with images.

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

Create `lib/scrapfly.ts`:

```typescript
import { ScrapflyClient, ScrapeConfig } from "scrapfly-sdk";

export const scrapflyClient = new ScrapflyClient({
  key: process.env.SCRAPFLY_KEY!,
});

// Base config matching Python's BASE_CONFIG
// - asp: true → anti-bot bypass (handles CAPTCHAs, fingerprinting)
// - country: "GB" → avoids redirect to aliexpress.us (US proxies trigger this)
// - cookie forces English locale + USD pricing regardless of proxy country
export const BASE_CONFIG = {
  asp: true,
  country: "GB",
  headers: {
    cookie:
      "aep_usuc_f=site=glo&province=&city=&c_tp=USD&region=US&b_locale=en_US&ae_u_p_s=2",
  },
};
```

### Anti-blocking strategy

| Setting | Purpose |
|---|---|
| `asp: true` | Enables Scrapfly's Anti Scraping Protection — bypasses CAPTCHAs, browser fingerprint checks, and bot detection |
| `country: "GB"` | Uses a UK proxy. Avoid `"US"` — AliExpress redirects US visitors to `aliexpress.us` which has a different HTML structure. Any non-US country works (`"DE"`, `"FR"`, `"CA"`, etc.) |
| `proxy_pool: "public_residential_pool"` | Residential IPs are harder to detect than datacenter IPs |
| `session: crypto.randomUUID()` | A unique session per request means each scrape looks like a new visitor. A fixed session reuses the same fingerprint, making it easy to correlate and block |
| `render_js: true` | Full headless browser rendering — required because AliExpress loads product data via JavaScript |
| Cookie header | Forces `site=glo`, `b_locale=en_US`, `c_tp=USD` so the page always returns English content with USD prices, regardless of proxy country |

If you start getting blocked (null results), rotate the `country` value to a different non-US country.

---

## 3. URL Normalizer

Create `lib/aliexpress.ts`:

```typescript
/**
 * Converts localized AliExpress URLs (e.g. es.aliexpress.com, fr.aliexpress.com)
 * to www.aliexpress.com so the English cookie and CSS selectors work correctly.
 */
export function normalizeUrl(url: string): string {
  return url.replace(
    /https?:\/\/\w+\.aliexpress\.(?:com|us)/,
    "https://www.aliexpress.com"
  );
}
```

---

## 4. Price Parser

AliExpress returns prices in the visitor's locale format. A Colombian proxy might return `484.436,92` (European format) while a UK one returns `484,436.92`. This helper handles both:

Add to `lib/aliexpress.ts`:

```typescript
/**
 * Parse price string in any locale format.
 * Handles: "$1,234.56", "1.234,56 US$", "US $484.436,92", etc.
 */
export function parsePrice(text: string | null): number | null {
  if (!text) return null;

  // Get the part after $ (or the whole string if no $)
  const afterDollar = text.includes("$")
    ? text.split("$").pop()!.trim()
    : text.trim();

  // Keep only digits, commas, and dots
  const nums = afterDollar.replace(/[^\d.,]/g, "");
  if (!nums) return null;

  // If the last separator is a comma → European format (1.234,56)
  const lastComma = nums.lastIndexOf(",");
  const lastDot = nums.lastIndexOf(".");

  let normalized: string;
  if (lastComma > lastDot) {
    // European: 1.234,56 → 1234.56
    normalized = nums.replace(/\./g, "").replace(",", ".");
  } else {
    // English: 1,234.56 → 1234.56
    normalized = nums.replace(/,/g, "");
  }

  const value = parseFloat(normalized);
  return isNaN(value) ? null : value;
}

/**
 * Extract just the digits from a localized string.
 * "4584 valoraciones" → 4584, "1,000+ sold" → 1000
 */
export function extractNumber(text: string | null): number | null {
  if (!text) return null;
  const digits = text.replace(/[^\d]/g, "");
  return digits ? parseInt(digits, 10) : null;
}

/**
 * Parse seller follower count strings like "2.0M", "1.5K", "500"
 */
export function parseFollowers(text: string | null): number | null {
  if (!text) return null;
  if (text.includes("M")) {
    return Math.round(parseFloat(text.replace("M", "")) * 1_000_000);
  }
  if (text.includes("K")) {
    return Math.round(parseFloat(text.replace("K", "")) * 1_000);
  }
  const n = parseInt(text, 10);
  return isNaN(n) ? null : n;
}
```

---

## 5. Product Scraper

Create `lib/scrape-product.ts`:

```typescript
import { ScrapflyClient, ScrapeConfig } from "scrapfly-sdk";
import { scrapflyClient, BASE_CONFIG } from "./scrapfly";
import {
  normalizeUrl,
  parsePrice,
  extractNumber,
  parseFollowers,
} from "./aliexpress";

// Python's XPath selectors converted to CSS equivalents
// XPath: //h1[@data-pl]/text()       → CSS: h1[data-pl]
// XPath: //a[@data-pl='store-name']  → CSS: a[data-pl="store-name"]
// XPath: //div[contains(@class,'x')] → CSS: div[class*="x"]

export interface ProductData {
  info: {
    name: string | null;
    productId: number;
    link: string;
    media: string[];
    rate: number | null;
    reviews: number | null;
    soldCount: number;
    availableCount: number | null;
  };
  pricing: {
    priceCurrency: string;
    price: number | null;
    originalPrice: number | string | null;
    discount: string;
  };
  specifications: { name: string | null; value: string | null }[];
  faqs: { question: string | null; answer: string | null }[];
  seller: {
    name: string | null;
    link: string | null;
    id: number | null;
    info: {
      positiveFeedback: string | null;
      followers: number | null;
    };
  };
  reviews: ReviewData[];
}

export interface ReviewData {
  user: string | null;
  country: string | null;
  rating: number | null;
  date: string | null;
  content: string | null;
  images: string[];
}

function parseProduct(
  selector: ReturnType<Awaited<ReturnType<ScrapflyClient["scrape"]>>["result"]["selector"]>,
  finalUrl: string
): Omit<ProductData, "reviews"> {
  // The SDK's .selector() returns a cheerio-based API
  // Use it directly — no need to install cheerio separately

  // --- Info ---
  const reviewsText = selector('a[class*="reviewer--reviews"]').first().text();
  const rateStars = selector('div[class*="rating--wrap"] > div').length;
  const soldText = selector('a[class*="reviewer--sliderItem"] span')
    .filter((_, el) => selector(el).text().includes("sold"))
    .first()
    .text();
  const availableText = selector('div[class*="quantity--info"] div span').first().text();

  const productId = parseInt(finalUrl.split("item/").pop()!.split(".")[0], 10);

  const info = {
    name: selector("h1[data-pl]").first().text() || null,
    productId,
    link: finalUrl,
    media: selector('div[class*="slider--img"] img')
      .map((_, el) => selector(el).attr("src"))
      .get()
      .filter(Boolean) as string[],
    rate: rateStars || null,
    reviews: extractNumber(reviewsText),
    soldCount: extractNumber(soldText) ?? 0,
    availableCount: extractNumber(availableText),
  };

  // --- Pricing ---
  const priceText = selector('span[class*="price-default--current"]').first().text();
  const originalPriceText = selector('span[class*="price-default--original"]').first().text();
  const discountText = selector('span[class*="price--discount"]').first().text();

  const pricing = {
    priceCurrency: "USD $",
    price: parsePrice(priceText),
    originalPrice: originalPriceText
      ? parsePrice(originalPriceText) ?? "No discount"
      : "No discount",
    discount: discountText || "No discount",
  };

  // --- Specifications ---
  const specifications: { name: string | null; value: string | null }[] = [];
  selector('div[class*="specification--prop"]').each((_, el) => {
    specifications.push({
      name: selector(el).find('div[class*="specification--title"] span').first().text() || null,
      value: selector(el).find('div[class*="specification--desc"] span').first().text() || null,
    });
  });

  // --- FAQs ---
  const faqs: { question: string | null; answer: string | null }[] = [];
  selector("div.ask-list ul li").each((_, el) => {
    faqs.push({
      question: selector(el).find("p.ask-content span").first().text() || null,
      answer: selector(el).find("ul.answer-box li p").first().text() || null,
    });
  });

  // --- Seller ---
  const sellerLink = selector('a[data-pl="store-name"]').attr("href") ?? null;
  const sellerFollowersText = selector('div[class*="store-info"] strong').eq(1).text();

  const seller = {
    name: selector('a[data-pl="store-name"]').first().text() || null,
    link: sellerLink ? sellerLink.split("?")[0].replace("//", "") : null,
    id: sellerLink
      ? parseInt(sellerLink.split("store/").pop()!.split("?")[0], 10)
      : null,
    info: {
      positiveFeedback:
        selector('div[class*="store-info"] strong').first().text() || null,
      followers: parseFollowers(sellerFollowersText),
    },
  };

  return { info, pricing, specifications, faqs, seller };
}

export async function scrapeProduct(rawUrl: string): Promise<ProductData> {
  const url = normalizeUrl(rawUrl);
  console.log(`scraping product: ${url}`);

  const apiResult = await scrapflyClient.scrape(
    new ScrapeConfig({
      url,
      ...BASE_CONFIG,
      render_js: true,
      auto_scroll: true,
      rendering_wait: 5000,
      js_scenario: [
        {
          wait_for_selector: {
            selector: "//div[@id='nav-specification']//button",
            timeout: 5000,
          },
        },
        {
          click: {
            selector: "//div[@id='nav-specification']//button",
            ignore_if_not_visible: true,
          },
        },
      ],
      proxy_pool: "public_residential_pool",
      session: `product-${crypto.randomUUID().slice(0, 8)}`,
    })
  );

  const $ = apiResult.result.selector;
  const finalUrl = apiResult.result.url ?? url;
  const data = parseProduct($, finalUrl);

  // Fetch up to 10 reviews (1 API call, no extra cost)
  const productId = String(data.info.productId);
  let reviews: ReviewData[] = [];
  try {
    const reviewResult = await scrapflyClient.scrape(
      new ScrapeConfig({
        url: `https://feedback.aliexpress.com/pc/searchEvaluation.do?productId=${productId}&lang=en_US&country=US&page=1&pageSize=10&filter=all&sort=complex_default`,
      })
    );
    const reviewData = JSON.parse(reviewResult.result.content);
    const rawReviews = reviewData?.data?.evaViewList ?? [];

    reviews = rawReviews.slice(0, 10).map((r: any) => ({
      user: r.buyerName ?? null,
      country: r.buyerCountry ?? null,
      rating: r.buyerEval ?? null,
      date: r.evalDate ?? null,
      content: r.buyerTranslationFeedback || r.buyerFeedback || null,
      images: (r.images ?? [])
        .map((img: any) => (typeof img === "string" ? img : img?.imgUrl ?? ""))
        .filter(Boolean),
    }));
  } catch (e) {
    console.warn(`could not fetch reviews: ${e}`);
  }

  console.log(`successfully scraped product: ${url}`);
  return { ...data, reviews };
}
```

---

## 6. Next.js API Route

Create `app/api/scrape/route.ts`:

```typescript
import { NextRequest, NextResponse } from "next/server";
import { scrapeProduct } from "@/lib/scrape-product";

export async function POST(request: NextRequest) {
  const { url } = await request.json();

  if (!url || !url.includes("aliexpress")) {
    return NextResponse.json({ error: "Invalid AliExpress URL" }, { status: 400 });
  }

  const data = await scrapeProduct(url);
  return NextResponse.json(data);
}
```

### Usage

```bash
curl -X POST http://localhost:3000/api/scrape \
  -H "Content-Type: application/json" \
  -d '{"url": "https://es.aliexpress.com/item/1005009361746399.html"}'
```

---

## 7. Key Differences from Python Version

| Aspect | Python | TypeScript/Next.js |
|---|---|---|
| Selectors | XPath via `parsel` | CSS via SDK's built-in `.selector()` (cheerio under the hood) |
| HTTP client | `scrapfly-sdk` (Python) | `scrapfly-sdk` (TS) |
| Selector access | `result.selector.xpath(...)` | `apiResult.result.selector("css selector")` |
| Response URL | `result.context["url"]` | `result.result.url` |
| Async pattern | `asyncio` | native `async/await` |
| Session ID | `uuid.uuid4()` | `crypto.randomUUID()` |

---

## 8. File Structure

```
your-nextjs-app/
├── .env.local              # SCRAPFLY_KEY=your-key
├── lib/
│   ├── scrapfly.ts         # Client + BASE_CONFIG
│   ├── aliexpress.ts       # normalizeUrl, parsePrice, extractNumber, parseFollowers
│   └── scrape-product.ts   # scrapeProduct() + parseProduct()
└── app/
    └── api/
        └── scrape/
            └── route.ts    # POST endpoint
```
