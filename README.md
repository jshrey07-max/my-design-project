# Myntra Reviews Scraper

## What this actually is
Myntra blocks plain HTTP requests (confirmed — a direct fetch to a real product
page returned a bot-block page, not the product). So this drives a real
Chromium browser with Playwright, opens the product page like a shopper would,
scrolls to the reviews section, clicks through "load more," and captures
whatever JSON the page itself loads for reviews — rather than scraping
hardcoded HTML class names, which Myntra can (and does) change at any time.

## What's verified vs. not
- ✅ **The parsing logic** (finding review records inside arbitrary JSON, and
  pulling out rating/text/reviewer/size/votes/date) is unit-tested against a
  realistic Myntra review payload shape — this works correctly.
- ✅ **Syntax and CSV output** are checked and correct.
- ⚠️ **The live browser run against myntra.com is NOT verified from my side** —
  I don't have network access to myntra.com from this environment. You'll be
  the first real test against the live site.

## Setup (one-time)
```bash
pip install playwright
playwright install chromium
```

## Usage
```bash
# One product
python myntra_reviews_scraper.py --url "https://www.myntra.com/.../36603057/buy" --out reviews.csv

# Several products (one URL per line in urls.txt)
python myntra_reviews_scraper.py --input urls.txt --outdir reviews_out
```

Run headed first (default) — real browser window, easier to bypass bot
detection than headless, and you can watch what happens:
```bash
python myntra_reviews_scraper.py --url "..." --out reviews.csv --debug
```

## If it comes back with 0 reviews
This is the likely failure point, so here's exactly what to do:
1. Re-run with `--debug`. It prints every network response captured and
   every button-click it attempted.
2. Add `--raw-json` to also dump whatever JSON it *did* capture, even if the
   parser didn't recognize it as reviews.
3. Send me the `--debug` output (or the raw JSON file). I can patch the
   `find_review_lists` / `flatten_review` logic to match Myntra's actual
   response shape in minutes — the capture mechanism doesn't need to change,
   just the field-name mapping.

## Realistic expectations
- This is fine for pulling reviews on a handful of products at a time.
- If Myntra shows a CAPTCHA or "Access Denied" page instead of the product,
  that's anti-bot detection kicking in — running headed (not `--headless`),
  slowing down between products, or eventually adding a residential proxy
  are the usual fixes. I didn't build proxy support in since I don't know
  your setup, but it's a small addition if you hit that wall.
- For scraping at real scale (hundreds of products, daily runs), a paid
  service like Apify's Myntra Reviews Scraper is genuinely more reliable
  than any self-hosted script, since they maintain proxy rotation for you.
