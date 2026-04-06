# Next.js Multi-Platform Scraper Implementation Plan

## Context

We have 4 working Python scrapers (AliExpress, MercadoLibre, Amazon, Etsy) using the Scrapfly Python SDK with XPath selectors. We need to port all of them to a Next.js application using the Scrapfly TypeScript SDK with CSS selectors. An existing reference implementation for AliExpress exists in `NEXTJS_GUIDE.md`. The plan must be detailed enough for any LLM to implement production-ready code without additional context.

---

## File Structure

```
lib/
├── scrapfly.ts                 # Scrapfly client singleton
├── utils.ts                    # Shared: parsePrice, extractNumber, parseFollowers, detectPlatform
├── types.ts                    # All TypeScript interfaces (per platform)
└── scrapers/
    ├── aliexpress.ts           # scrapeAliExpressProduct()
    ├── amazon.ts               # scrapeAmazonProduct()
    ├── mercadolibre.ts         # scrapeMercadoLibreProduct()
    └── etsy.ts                 # scrapeEtsyProduct()
app/
└── api/
    └── scrape/
        └── route.ts            # Unified POST endpoint (auto-detects platform from URL)
.env.local                      # SCRAPFLY_KEY=...
```

---

## File 1: `lib/scrapfly.ts`

```typescript
import { ScrapflyClient } from "scrapfly-sdk";
export const scrapflyClient = new ScrapflyClient({ key: process.env.SCRAPFLY_KEY! });
```

No shared BASE_CONFIG — each platform has its own config requirements.

---

## File 2: `lib/utils.ts`

4 exported functions:

### `parsePrice(text: string | null): number | null`
- Handles `$1,234.56` (English) and `1.234,56` (European) formats
- Split on `$`, take part after it (or whole string if no `$`)
- Strip everything except digits, commas, dots
- If `lastIndexOf(",") > lastIndexOf(".")` → European: remove dots, replace comma with dot
- Otherwise English: remove commas
- Return `parseFloat` result

### `extractNumber(text: string | null): number | null`
- Strip all non-digit chars: `text.replace(/[^\d]/g, "")`
- Return `parseInt` or null
- Handles: "4584 valoraciones" → 4584, "1,000+ sold" → 1000

### `parseFollowers(text: string | null): number | null`
- "2.0M" → multiply by 1,000,000
- "1.5K" → multiply by 1,000
- Plain number → parseInt

### `detectPlatform(url: string): "aliexpress" | "amazon" | "mercadolibre" | "etsy" | null`
- Contains `aliexpress` → `"aliexpress"`
- Contains `amazon` → `"amazon"`
- Contains `mercadolib` (catches mercadolibre + mercadolivre) → `"mercadolibre"`
- Contains `etsy.com` → `"etsy"`
- Otherwise → `null`

---

## File 3: `lib/types.ts`

### AliExpressProduct
```typescript
{
  info: { name, productId: number, link, media: string[], rate, reviews, soldCount, availableCount }
  pricing: { priceCurrency, price, originalPrice, discount }
  specifications: { name, value }[]
  faqs: { question, answer }[]
  seller: { name, link, id, info: { positiveFeedback, followers } }
  reviews: { user, country, rating, date, content, images: string[] }[]
}
```

### AmazonProduct
```typescript
{
  info: { name, asin, link, brand, images: string[], rating, reviewCount, availability, categories: string[] }
  pricing: { currency, price, originalPrice, discount }
  features: string[]
  overview: Record<string, string>
  specifications: { name, value }[]
  reviews: { user, rating, title, date, content, images: string[] }[]
}
```

### MercadoLibreProduct
```typescript
{
  info: { name, productId, link, condition, images: string[], rating, reviewCount, soldCount, availableCount }
  pricing: { currency, price, originalPrice, discount }
  specifications: { name, value }[]
  description: string | null
  seller: { name, sales }
  reviews: { rating, country, date, content, images: string[] }[]
}
```

### EtsyProduct
```typescript
{
  info: { name, listingId, link, shop, shopLink, images: string[], rating, reviewCount, categories: string[] }
  pricing: { currency, price, variants: { name, price, availability }[] | null }
  description: string | null
  reviews: { user, rating, date, content, images: string[] }[]
}
```

---

## File 4: `lib/scrapers/aliexpress.ts`

**Source:** `scrape_product.py` + `NEXTJS_GUIDE.md`

### URL Normalization
`url.replace(/https?:\/\/\w+\.aliexpress\.(?:com|us)/, "https://www.aliexpress.com")`

### ScrapeConfig
- `asp: true`, `country: "GB"`, cookie header (forces English + USD)
- `render_js: true`, `auto_scroll: true`, `rendering_wait: 5000`
- `js_scenario`: wait for + click `//div[@id='nav-specification']//button`
- `proxy_pool: "public_residential_pool"`
- `session: aliexpress-${crypto.randomUUID().slice(0, 8)}`

### CSS Selectors (XPath → CSS)
| Data | CSS Selector |
|---|---|
| Title | `h1[data-pl]` |
| Reviews text | `a[class*="reviewer--reviews"]` |
| Rating stars | `div[class*="rating--wrap"] > div` (count length) |
| Sold count | `a[class*="reviewer--sliderItem"] span` (filter text includes "sold") |
| Available | `div[class*="quantity--info"] div span` |
| Images | `div[class*="slider--img"] img` (src attr) |
| Price | `span[class*="price-default--current"]` |
| Original price | `span[class*="price-default--original"]` |
| Discount | `span[class*="price--discount"]` |
| Spec rows | `div[class*="specification--prop"]` |
| Spec name | `div[class*="specification--title"] span` |
| Spec value | `div[class*="specification--desc"] span` |
| FAQ items | `div.ask-list ul li` |
| FAQ question | `p.ask-content span` |
| FAQ answer | `ul.answer-box li p` |
| Seller name | `a[data-pl="store-name"]` |
| Seller link | `a[data-pl="store-name"]` (href) |
| Seller feedback | `div[class*="store-info"] strong` (first) |
| Seller followers | `div[class*="store-info"] strong` (`.eq(1)`) |

### Reviews
- Separate API call: `https://feedback.aliexpress.com/pc/searchEvaluation.do?productId=${productId}&lang=en_US&country=US&page=1&pageSize=10&filter=all&sort=complex_default`
- Parse JSON response → `data.evaViewList`
- Fields: `buyerName`, `buyerCountry`, `buyerEval`, `evalDate`, `buyerTranslationFeedback || buyerFeedback`
- Images: handle both string and `{imgUrl}` formats
- Wrap in try/catch, default to empty array on error

### Parsing Notes
- ProductId from URL: `url.split("item/").pop()!.split(".")[0]`
- Price: use shared `parsePrice()`
- Reviews/sold/available: use `extractNumber()` (language-agnostic)
- Followers: use `parseFollowers()` (handles K/M)

---

## File 5: `lib/scrapers/amazon.ts`

**Source:** `scrape_amazon.py`

### Country Map (21 entries)
```
amazon.com→US, amazon.co.uk→GB, amazon.de→DE, amazon.fr→FR, amazon.es→ES,
amazon.it→IT, amazon.co.jp→JP, amazon.ca→CA, amazon.com.au→AU,
amazon.com.br→BR, amazon.com.mx→MX, amazon.in→IN, amazon.nl→NL,
amazon.sg→SG, amazon.se→SE, amazon.pl→PL, amazon.com.be→BE,
amazon.com.tr→TR, amazon.sa→SA, amazon.ae→AE, amazon.eg→EG
```

### URL Cleaning
- Regex `/(?:dp|gp\/product)\/([A-Z0-9]{10})/` → rebuild as `${domain}/dp/${asin}`
- Fallback: strip query params and hash

### ScrapeConfig — NO render_js
- `asp: true`, `country: detected`, `proxy_pool: "public_residential_pool"`
- `session: amazon-${crypto.randomUUID().slice(0, 8)}`

### CSS Selectors
| Data | CSS Selector | Notes |
|---|---|---|
| ASIN | `input#ASIN` | value attr; fallback regex from URL |
| Title | `span#productTitle` | trim |
| Brand | `a#bylineInfo` | remove "Visit the ", " Store", "Brand: " |
| Price | `[class*="a-price"] span[class*="a-offscreen"]` | first match, use parsePrice() |
| Original price | `[class*="basisPrice"] span[class*="a-offscreen"]` | |
| Discount | `[class*="savingsPercentage"]` | |
| Rating | `span[class*="reviewCountTextLinkedHistogram"]` | title attr, extract float |
| Review count | `#acrCustomerReviewText` | extractNumber() |
| Availability | `div#availability span` | trim |
| Categories | `#wayfinding-breadcrumbs_feature_div a` | all text, trim |
| Features | `#feature-bullets li span[class*="a-list-item"]` | all text, trim |
| Overview rows | `#productOverview_feature_div tr` | td:first span→label, td:last span→value |
| Spec rows | `table[class*="a-keyvalue"] tr` | th→name, td text joined→value |
| Reviews | `[data-hook="review"]` | |
| Review user | `[class*="profile-name"]` | |
| Review rating | `[class*="review-rating"]` | extract float |
| Review title | `[data-hook="review-title"]` | first text NOT containing "out of" |
| Review body | `[data-hook="review-body"]` | join all text, trim |
| Review date | `[data-hook="review-date"]` | |
| Review images | `img[data-hook="review-image"]` | src, upgrade `_SY\d+_` → `_SL1500_` |

### Product Images
- **From raw HTML** (not selectors): regex `/"hiRes":"(https:\/\/[^"]+)"/g` on `apiResult.result.content`
- Deduplicate with `[...new Set(matches)]`

### Currency Extraction
- Regex `/[^\d.,\s]+/` against price text

### Price Parsing
- Use shared `parsePrice()` — handles both `$1,234.56` and `1.234,56€`

---

## File 6: `lib/scrapers/mercadolibre.ts`

**Source:** `scrape_mercadolibre.py`

### Country Map (18 entries)
```
mercadolibre.com.ar→AR, mercadolibre.com.bo→BO, mercadolivre.com.br→BR,
mercadolibre.cl→CL, mercadolibre.com.co→CO, mercadolibre.co.cr→CR,
mercadolibre.com.do→DO, mercadolibre.com.ec→EC, mercadolibre.com.gt→GT,
mercadolibre.com.hn→HN, mercadolibre.com.mx→MX, mercadolibre.com.ni→NI,
mercadolibre.com.pa→PA, mercadolibre.com.py→PY, mercadolibre.com.pe→PE,
mercadolibre.com.sv→SV, mercadolibre.com.uy→UY, mercadolibre.com.ve→VE
```

### URL Cleaning
`url.split("#")[0].split("?")[0]`

### ScrapeConfig — WITH render_js
- `asp: true`, `country: detected`, `render_js: true`, `rendering_wait: 5000`
- `proxy_pool: "public_residential_pool"`
- `session: meli-${crypto.randomUUID().slice(0, 8)}`

### JSON-LD Parsing
- Parse all `script[type="application/ld+json"]`, find `@type === "Product"`
- Extract: `name`, `productID`/`sku`, `image`, `offers.priceCurrency`, `offers.price`

### CSS Selectors
| Data | CSS Selector | Notes |
|---|---|---|
| Title | `h1[class*="ui-pdp-title"]` | fallback to JSON-LD |
| Subtitle | `[class*="ui-pdp-subtitle"]` | regex `\+?([\d.]+)\s*vendidos?` for sold |
| Stock | `[class*="ui-pdp-buybox__quantity__available"]` | regex `\+?([\d.]+)` |
| Images | `figure[class*="ui-pdp-gallery__figure"] img` | try data-zoom first, then src |
| Images fallback | `[class*="ui-pdp-gallery"] img` | filter out `data:` and `.svg` |
| Rating | `[class*="ui-pdp-review__rating"]` | parseFloat |
| Review count | `[class*="ui-pdp-review__amount"]` | extract digits |
| Current price | `[class*="ui-pdp-price__second-line"] span[class*="andes-money-amount__fraction"]` | |
| Original price | `[class*="ui-pdp-price__original-value"] span[class*="andes-money-amount__fraction"]` | |
| Discount | `[class*="andes-money-amount__discount"]` | |
| Currency | `span[class*="andes-money-amount__currency-symbol"]` | |
| Spec rows | `table[class*="andes-table"] tr` | th→name, td→value |
| Description | `[class*="ui-pdp-description__content"]` | join text with newlines |
| Seller texts | `[class*="ui-pdp-seller__header__title"]` | filter out "Vendido por" |
| Seller sales | filter text containing "ventas" | regex `\+?([\d.]+)` |
| Review cards | `[class*="ui-review-capability-comments__comment"]` | |
| Review rating | `p` (first within card) | regex `(\d+)\s*de\s*5`, skip card if no match |
| Review content | `[class*="__content"]` | |
| Review images | `img` (within card) | get src |
| Review dates | `[class*="__date"]` | first=country, second=date |

### MercadoLibre Price Parsing (local function)
- MeLi uses dots as thousands separators: `"52.382"` = 52382
- Local `parseMeliPrice()`: `text.replace(/\./g, "").replace(",", ".")` then `parseFloat`
- Different from shared `parsePrice()` which handles currency prefixes

### Image Filtering
- Remove `data:` URIs and `.svg`
- Fallback to JSON-LD `image` if array empty

### Review Prioritization
- Skip cards where `p` text doesn't match `(\d+)\s*de\s*5`
- Skip reviews with no content AND no images
- Two arrays: `reviewsWithImages` + `reviewsTextOnly`
- Concatenate and slice to 10

---

## File 7: `lib/scrapers/etsy.ts`

**Source:** `scrape_etsy.py`

### URL Cleaning
- Strip `?` and `#` params
- Normalize locale: `url.replace(/etsy\.com\/\w{2}\/listing\//, "etsy.com/listing/")`

### ScrapeConfig — NO render_js, fixed country
- `asp: true`, `country: "US"`, `proxy_pool: "public_residential_pool"`
- `session: etsy-${crypto.randomUUID().slice(0, 8)}`

### JSON-LD Parsing (primary data source)
- Find `@type === "Product"` or `"ProductGroup"` → images, rating, reviewCount, brand, description, variants
- Find `@type === "BreadcrumbList"` → categories
- Images: handle both `{contentURL, thumbnail}` objects and plain strings
- Variants from `hasVariant[]`: name, `offers.price`, `offers.availability` (check for "InStock")

### CSS Selectors
| Data | CSS Selector | Notes |
|---|---|---|
| Title | `h1` | trim, fallback JSON-LD |
| Listing ID | regex `/\/listing\/(\d+)/` from URL | |
| Shop name | `a[href*="/shop/"]` | trim, fallback to JSON-LD brand |
| Shop link | `a[href*="/shop/"]` | href, strip query params |
| Price (HTML fallback) | `[class*="wt-text-title-larger"]` | regex for number + currency |
| Review cards | `[class*="review-card"]` | |
| Review rating | `input[name="rating"]` (within card) | value attr; skip if missing |
| Review user | `[class*="wt-text-link-no-underline"]` (within card) | |
| Review date | `[class*="wt-text-body-small"]` (within card) | match `\w+ \d+, \d{4}` |
| Review content | `[class*="wt-content-toggle"]` (within card) | join all text |
| Review images | `img[src*="etsystatic"]` (within card) | dedupe, exclude `/iusa/` paths |

### Review Notes
- Avatar images have `/iusa/` in URL path — filter these out
- Actual review photos are client-side rendered (won't appear in HTML for most products)
- Skip reviews with no content AND no images

---

## File 8: `app/api/scrape/route.ts`

Unified POST endpoint:
- Parse `{ url }` from request body
- Call `detectPlatform(url)` from utils
- Route to correct scraper function via switch
- Return `{ platform, data }` 
- Wrap in try/catch, return 500 with error message on failure
- Return 400 for missing URL or unsupported platform

---

## Implementation Order

1. `lib/scrapfly.ts` — no dependencies
2. `lib/utils.ts` — no dependencies
3. `lib/types.ts` — no dependencies
4. `lib/scrapers/aliexpress.ts` — depends on 1, 2, 3
5. `lib/scrapers/amazon.ts` — depends on 1, 2, 3
6. `lib/scrapers/mercadolibre.ts` — depends on 1, 2, 3
7. `lib/scrapers/etsy.ts` — depends on 1, 2, 3
8. `app/api/scrape/route.ts` — depends on 2, 4-7

Steps 4-7 can be implemented in parallel.

---

## Verification

Test each platform with curl:
```bash
# AliExpress
curl -X POST http://localhost:3000/api/scrape -H "Content-Type: application/json" \
  -d '{"url":"https://es.aliexpress.com/item/1005009361746399.html"}'

# Amazon
curl -X POST http://localhost:3000/api/scrape -H "Content-Type: application/json" \
  -d '{"url":"https://www.amazon.com/dp/B0CD1DZ6RD"}'

# MercadoLibre
curl -X POST http://localhost:3000/api/scrape -H "Content-Type: application/json" \
  -d '{"url":"https://www.mercadolibre.com.co/lamparas-tipo-spot-con-riel-de-1m-x-3-unid-de-10w-estructura-negro-luz-blanca/p/MCO42090990"}'

# Etsy
curl -X POST http://localhost:3000/api/scrape -H "Content-Type: application/json" \
  -d '{"url":"https://www.etsy.com/listing/1394647088/stacking-blocks-set-of-4"}'
```

Verify each response has: correct platform field, non-null info.name, non-null pricing.price, images array with URLs, and reviews array (may be empty for some products).

---

## Source Files Reference
- `C:/Users/JULIAN/Desktop/scrapfly-demo/scrape_product.py` — AliExpress Python source
- `C:/Users/JULIAN/Desktop/scrapfly-demo/scrape_reviews.py` — AliExpress reviews API
- `C:/Users/JULIAN/Desktop/scrapfly-demo/scrape_amazon.py` — Amazon Python source
- `C:/Users/JULIAN/Desktop/scrapfly-demo/scrape_mercadolibre.py` — MercadoLibre Python source
- `C:/Users/JULIAN/Desktop/scrapfly-demo/scrape_etsy.py` — Etsy Python source
- `C:/Users/JULIAN/Desktop/scrapfly-demo/NEXTJS_GUIDE.md` — Existing AliExpress Next.js reference
