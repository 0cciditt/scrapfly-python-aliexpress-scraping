# Scraping MercadoLibre Products with Scrapfly in Next.js

## Overview

This guide ports the Python `scrape_mercadolibre.py` logic to a Next.js (App Router) API route using the Scrapfly TypeScript SDK. It covers: product info, pricing, specifications, description, seller, and up to 10 user reviews (prioritized by images first, then text only). Works across all 18 MercadoLibre country sites.

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
import { ScrapflyClient } from "scrapfly-sdk";

export const scrapflyClient = new ScrapflyClient({
  key: process.env.SCRAPFLY_KEY!,
});
```

No shared `BASE_CONFIG` — MercadoLibre uses dynamic country detection per request.

---

## 3. Anti-blocking Strategy

| Setting | Purpose |
|---|---|
| `asp: true` | Enables Scrapfly's Anti Scraping Protection — bypasses MercadoLibre's proof-of-work JS challenge, CAPTCHAs, and bot detection |
| `country: detected` | Proxy country auto-detected from URL domain (e.g. `mercadolibre.com.co` → `"CO"`). Uses a matching local proxy so MercadoLibre doesn't trigger account verification redirects |
| `proxy_pool: "public_residential_pool"` | Residential IPs are harder to detect than datacenter IPs |
| `render_js: true` | Required — MercadoLibre has a proof-of-work JS challenge that must be solved before the page loads |
| `rendering_wait: 5000` | Wait 5 seconds for JS to fully render product data, reviews, and images |
| `session: crypto.randomUUID()` | Unique session per request — each scrape looks like a new visitor, preventing fingerprint correlation and blocking |

---

## 4. Country Detection

MercadoLibre operates across 18 country-specific domains. The scraper auto-detects the country from the URL and uses a matching proxy to avoid geo-blocks and verification screens.

Create `lib/mercadolibre.ts`:

```typescript
/**
 * MercadoLibre country domains → Scrapfly proxy country codes.
 * Each country site has its own domain, currency, and product catalog.
 */
const MELI_COUNTRIES: Record<string, string> = {
  "mercadolibre.com.ar": "AR",  // Argentina
  "mercadolibre.com.bo": "BO",  // Bolivia
  "mercadolivre.com.br": "BR",  // Brasil (note: mercadolivre, not mercadolibre)
  "mercadolibre.cl": "CL",      // Chile
  "mercadolibre.com.co": "CO",  // Colombia
  "mercadolibre.co.cr": "CR",   // Costa Rica
  "mercadolibre.com.do": "DO",  // Dominicana
  "mercadolibre.com.ec": "EC",  // Ecuador
  "mercadolibre.com.gt": "GT",  // Guatemala
  "mercadolibre.com.hn": "HN",  // Honduras
  "mercadolibre.com.mx": "MX",  // México
  "mercadolibre.com.ni": "NI",  // Nicaragua
  "mercadolibre.com.pa": "PA",  // Panamá
  "mercadolibre.com.py": "PY",  // Paraguay
  "mercadolibre.com.pe": "PE",  // Perú
  "mercadolibre.com.sv": "SV",  // El Salvador
  "mercadolibre.com.uy": "UY",  // Uruguay
  "mercadolibre.com.ve": "VE",  // Venezuela
};

/**
 * Detect the Scrapfly proxy country from the MercadoLibre URL domain.
 * Falls back to "CO" (Colombia) if domain is not recognized.
 */
export function detectCountry(url: string): string {
  for (const [domain, country] of Object.entries(MELI_COUNTRIES)) {
    if (url.includes(domain)) {
      return country;
    }
  }
  return "CO";
}

/**
 * Remove tracking parameters (#reco_id, ?polycard_client, etc.) from MercadoLibre URLs.
 */
export function cleanUrl(url: string): string {
  return url.split("#")[0].split("?")[0];
}
```

---

## 5. Price Parser

MercadoLibre displays prices using dots as thousands separators (e.g. `52.382` means 52,382). This is different from standard international formats.

Add to `lib/mercadolibre.ts`:

```typescript
/**
 * Parse MercadoLibre price strings.
 * MeLi uses dots for thousands and commas for decimals:
 *   "52.382" → 52382
 *   "1.234.567" → 1234567
 *   "52.382,50" → 52382.50
 */
export function parseMeliPrice(text: string | null): number | null {
  if (!text) return null;
  // Remove dots (thousands separator), replace comma with dot (decimal)
  const normalized = text.replace(/\./g, "").replace(",", ".");
  const value = parseFloat(normalized);
  return isNaN(value) ? null : value;
}
```

---

## 6. TypeScript Interfaces

Add to `lib/mercadolibre.ts`:

```typescript
export interface MercadoLibreProduct {
  info: {
    name: string | null;
    productId: string | null;
    link: string;
    condition: string | null;
    images: string[];
    rating: number | null;
    reviewCount: number | null;
    soldCount: number;
    availableCount: number | null;
  };
  pricing: {
    currency: string | null;
    price: number | null;
    originalPrice: number | null;
    discount: string;
  };
  specifications: { name: string; value: string }[];
  description: string | null;
  seller: {
    name: string | null;
    sales: number | null;
  };
  reviews: MercadoLibreReview[];
}

export interface MercadoLibreReview {
  rating: number;
  country: string | null;
  date: string | null;
  content: string | null;
  images: string[];
}
```

---

## 7. Product Scraper

Create `lib/scrape-mercadolibre.ts`:

```typescript
import { ScrapeConfig } from "scrapfly-sdk";
import { scrapflyClient } from "./scrapfly";
import {
  detectCountry,
  cleanUrl,
  parseMeliPrice,
  MercadoLibreProduct,
  MercadoLibreReview,
} from "./mercadolibre";

// Python XPath selectors converted to CSS equivalents:
// XPath: //h1[contains(@class,"ui-pdp-title")]     → CSS: h1[class*="ui-pdp-title"]
// XPath: //*[contains(@class,"x")]                  → CSS: [class*="x"]
// XPath: //figure[contains(@class,"x")]//img/@src   → CSS: figure[class*="x"] img
// XPath: //table[contains(@class,"x")]//tr          → CSS: table[class*="x"] tr

function parseProduct(
  $: ReturnType<Awaited<ReturnType<typeof scrapflyClient.scrape>>["result"]["selector"]>,
  html: string,
  finalUrl: string
): MercadoLibreProduct {
  // --- JSON-LD structured data (most reliable source) ---
  let ldJson: any = {};
  $('script[type="application/ld+json"]').each((_, el) => {
    try {
      const data = JSON.parse($(el).text());
      if (data["@type"] === "Product" && !ldJson.name) {
        ldJson = data;
      }
    } catch {}
  });

  const offers = ldJson.offers ?? {};

  // --- Info ---
  const title =
    $('h1[class*="ui-pdp-title"]').first().text().trim() ||
    ldJson.name ||
    null;

  const subtitle =
    $('[class*="ui-pdp-subtitle"]').first().text().trim() || "";
  const soldMatch = subtitle.match(/\+?([\d.]+)\s*vendidos?/);
  const soldCount = soldMatch
    ? parseInt(soldMatch[1].replace(/\./g, ""), 10)
    : 0;
  const condition = subtitle.includes("|")
    ? subtitle.split("|")[0].trim()
    : subtitle.trim() || null;

  const stockText =
    $('[class*="ui-pdp-buybox__quantity__available"]').first().text();
  const stockMatch = stockText ? stockText.match(/\+?([\d.]+)/) : null;
  const availableCount = stockMatch
    ? parseInt(stockMatch[1].replace(/\./g, ""), 10)
    : null;

  // --- Images (try multiple sources in order) ---
  let images: string[] = $('figure[class*="ui-pdp-gallery__figure"] img')
    .map((_, el) => $(el).attr("data-zoom"))
    .get()
    .filter(Boolean) as string[];

  if (!images.length) {
    images = $('figure[class*="ui-pdp-gallery__figure"] img')
      .map((_, el) => $(el).attr("src"))
      .get()
      .filter(Boolean) as string[];
  }

  if (!images.length) {
    images = $('[class*="ui-pdp-gallery"] img')
      .map((_, el) => $(el).attr("src"))
      .get()
      .filter(Boolean) as string[];
  }

  // Filter out placeholder/base64 images and SVGs
  images = images.filter(
    (img) => !img.startsWith("data:") && !img.endsWith(".svg")
  );

  // Fallback to JSON-LD image
  if (!images.length && ldJson.image) {
    images = [ldJson.image];
  }

  // --- Rating ---
  const ratingText = $('[class*="ui-pdp-review__rating"]').first().text();
  const rating = ratingText ? parseFloat(ratingText) : null;

  const reviewCountText = $('[class*="ui-pdp-review__amount"]').first().text();
  let reviewCount: number | null = null;
  if (reviewCountText) {
    const rcMatch = reviewCountText.match(/\d+/);
    reviewCount = rcMatch ? parseInt(rcMatch[0], 10) : null;
  }

  const info = {
    name: title,
    productId: (ldJson.productID || ldJson.sku || null) as string | null,
    link: finalUrl,
    condition,
    images,
    rating,
    reviewCount,
    soldCount,
    availableCount,
  };

  // --- Pricing (from JSON-LD + HTML) ---
  const currentPriceText = $(
    '[class*="ui-pdp-price__second-line"] span[class*="andes-money-amount__fraction"]'
  )
    .first()
    .text();
  const originalPriceText = $(
    '[class*="ui-pdp-price__original-value"] span[class*="andes-money-amount__fraction"]'
  )
    .first()
    .text();
  const discountText = $('[class*="andes-money-amount__discount"]')
    .first()
    .text();
  const currencySymbol = $('span[class*="andes-money-amount__currency-symbol"]')
    .first()
    .text();

  const pricing = {
    currency: (offers.priceCurrency || currencySymbol || null) as string | null,
    price: parseMeliPrice(currentPriceText) ?? offers.price ?? null,
    originalPrice: parseMeliPrice(originalPriceText),
    discount: discountText || "No discount",
  };

  // --- Specifications ---
  const specifications: { name: string; value: string }[] = [];
  $('table[class*="andes-table"] tr').each((_, el) => {
    const name = $(el).find("th").first().text().trim();
    const value = $(el).find("td").first().text().trim();
    if (name) {
      specifications.push({ name, value });
    }
  });

  // --- Description ---
  const descParts: string[] = [];
  $('[class*="ui-pdp-description__content"]')
    .find("*")
    .addBack()
    .contents()
    .each((_, el) => {
      const text = $(el).text().trim();
      if (text) descParts.push(text);
    });
  // Simpler approach: get all text and join
  const descText = $('[class*="ui-pdp-description__content"]').text().trim();
  const description = descText || null;

  // --- Seller ---
  const sellerTexts: string[] = $('[class*="ui-pdp-seller__header__title"]')
    .first()
    .find("*")
    .addBack()
    .contents()
    .map((_, el) => $(el).text().trim())
    .get()
    .filter(Boolean);

  let sellerName: string | null = null;
  for (const t of sellerTexts) {
    if (t && t !== "Vendido por" && t !== "Vendido por ") {
      sellerName = t;
      break;
    }
  }

  let sellerSales: number | null = null;
  $('[class*="ui-pdp-seller__header__title"]')
    .find("*")
    .each((_, el) => {
      const text = $(el).text();
      if (text.includes("ventas")) {
        const sm = text.match(/\+?([\d.]+)/);
        if (sm) {
          sellerSales = parseInt(sm[1].replace(/\./g, ""), 10);
        }
      }
    });

  const seller = {
    name: sellerName,
    sales: sellerSales,
  };

  // --- Reviews (up to 10, prioritize: images+text first, then text only) ---
  const reviewsWithImages: MercadoLibreReview[] = [];
  const reviewsTextOnly: MercadoLibreReview[] = [];

  $('[class*="ui-review-capability-comments__comment"]').each((_, el) => {
    if (reviewsWithImages.length + reviewsTextOnly.length >= 20) return; // collect pool

    // Rating from "Calificación X de 5" text in the first <p>
    const ratingP = $(el).find("p").first().text();
    const ratingMatch = ratingP.match(/(\d+)\s*de\s*5/);
    if (!ratingMatch) return; // empty/lazy-loaded element, skip

    // Content from dedicated __content element
    const rContent =
      $(el).find('[class*="__content"]').first().text().trim() || null;

    // Review images
    const reviewImgs: string[] = $(el)
      .find("img")
      .map((_, img) => $(img).attr("src"))
      .get()
      .filter(Boolean) as string[];

    // Skip reviews without text and without images
    if (!rContent && !reviewImgs.length) return;

    const rRating = parseInt(ratingMatch[1], 10);

    // Date fields: first is country, second is date
    const dateTexts: string[] = $(el)
      .find('[class*="__date"]')
      .map((_, d) => $(d).text().trim())
      .get()
      .filter(Boolean) as string[];

    const rCountry = dateTexts.length >= 1 ? dateTexts[0] : null;
    const rDate = dateTexts.length >= 2 ? dateTexts[1] : null;

    const review: MercadoLibreReview = {
      rating: rRating,
      country: rCountry,
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
    specifications,
    description,
    seller,
    reviews,
  };
}

export async function scrapeMercadoLibreProduct(
  rawUrl: string
): Promise<MercadoLibreProduct> {
  const url = cleanUrl(rawUrl);
  const country = detectCountry(url);
  console.log(`scraping product: ${url} (country: ${country})`);

  const apiResult = await scrapflyClient.scrape(
    new ScrapeConfig({
      url,
      asp: true,
      country,
      proxy_pool: "public_residential_pool",
      render_js: true,
      rendering_wait: 5000,
      session: `meli-${crypto.randomUUID().slice(0, 8)}`,
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

---

## 8. Next.js API Route

Create `app/api/scrape-mercadolibre/route.ts`:

```typescript
import { NextRequest, NextResponse } from "next/server";
import { scrapeMercadoLibreProduct } from "@/lib/scrape-mercadolibre";

export async function POST(request: NextRequest) {
  try {
    const { url } = await request.json();

    if (!url || !url.includes("mercadolib")) {
      return NextResponse.json(
        { error: "Invalid MercadoLibre URL" },
        { status: 400 }
      );
    }

    const data = await scrapeMercadoLibreProduct(url);
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
# Colombia
curl -X POST http://localhost:3000/api/scrape-mercadolibre \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.mercadolibre.com.co/lamparas-tipo-spot-con-riel-de-1m-x-3-unid-de-10w-estructura-negro-luz-blanca/p/MCO42090990"}'

# México
curl -X POST http://localhost:3000/api/scrape-mercadolibre \
  -H "Content-Type: application/json" \
  -d '{"url": "https://articulo.mercadolibre.com.mx/MLM-123456789-producto-ejemplo-_JM"}'

# Brasil (note: mercadolivre.com.br)
curl -X POST http://localhost:3000/api/scrape-mercadolibre \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.mercadolivre.com.br/produto-exemplo/p/MLB12345678"}'
```

---

## 9. Key Differences from Python Version

| Aspect | Python | TypeScript/Next.js |
|---|---|---|
| Selectors | XPath via `parsel` | CSS via SDK's built-in `.selector()` (cheerio) |
| HTTP client | `scrapfly-sdk` (Python) | `scrapfly-sdk` (TS) |
| Selector access | `result.selector.xpath(...)` | `apiResult.result.selector("css selector")` |
| Response URL | `result.context["url"]` | `apiResult.result.url` |
| Async pattern | `asyncio` | native `async/await` |
| Session ID | `uuid.uuid4()` | `crypto.randomUUID()` |
| Country detection | Same logic | Same logic |

---

## 10. File Structure

```
your-nextjs-app/
├── .env.local                          # SCRAPFLY_KEY=your-key
├── lib/
│   ├── scrapfly.ts                     # Client singleton
│   ├── mercadolibre.ts                 # detectCountry, cleanUrl, parseMeliPrice, interfaces
│   └── scrape-mercadolibre.ts          # scrapeMercadoLibreProduct() + parseProduct()
└── app/
    └── api/
        └── scrape-mercadolibre/
            └── route.ts                # POST endpoint
```

---

## 11. Supported Country Domains

| Country | Domain | Proxy Code |
|---|---|---|
| Argentina | `mercadolibre.com.ar` | AR |
| Bolivia | `mercadolibre.com.bo` | BO |
| Brasil | `mercadolivre.com.br` | BR |
| Chile | `mercadolibre.cl` | CL |
| Colombia | `mercadolibre.com.co` | CO |
| Costa Rica | `mercadolibre.co.cr` | CR |
| Dominicana | `mercadolibre.com.do` | DO |
| Ecuador | `mercadolibre.com.ec` | EC |
| Guatemala | `mercadolibre.com.gt` | GT |
| Honduras | `mercadolibre.com.hn` | HN |
| Mexico | `mercadolibre.com.mx` | MX |
| Nicaragua | `mercadolibre.com.ni` | NI |
| Panama | `mercadolibre.com.pa` | PA |
| Paraguay | `mercadolibre.com.py` | PY |
| Peru | `mercadolibre.com.pe` | PE |
| El Salvador | `mercadolibre.com.sv` | SV |
| Uruguay | `mercadolibre.com.uy` | UY |
| Venezuela | `mercadolibre.com.ve` | VE |

---

## 12. Troubleshooting

**Getting null/empty results?**
- MercadoLibre has a proof-of-work JS challenge. Make sure `render_js: true` and `rendering_wait: 5000` are set.
- If redirected to account verification, the proxy country might not match the domain. Ensure `detectCountry()` is returning the correct code.

**Reviews are empty?**
- Many review elements are lazy-loaded (empty HTML shells). The parser skips these automatically by checking for the rating pattern `(\d+) de 5`.
- Reviews without text AND images are filtered out by design.

**Images array is empty?**
- The scraper tries 3 sources in order: `data-zoom` attr → `src` attr → generic gallery `src`.
- Placeholder images (`data:` URIs) and SVGs are filtered out.
- Falls back to JSON-LD `image` field if all selectors fail.

**Getting blocked?**
- Rotate the session — each request already uses `crypto.randomUUID()`.
- If persistent, try a different proxy country that still matches the region.
