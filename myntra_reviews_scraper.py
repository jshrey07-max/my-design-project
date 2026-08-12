#!/usr/bin/env python3
"""
Myntra Product Reviews Scraper
-------------------------------
Myntra blocks plain HTTP requests (no browser = bot-block page), and there's
no documented public API for reviews. So this script drives a REAL browser
with Playwright, visits the product page like a normal shopper would, and
captures whatever JSON responses the page itself loads for reviews. That's
more robust than hardcoding CSS class names, which break every time Myntra
tweaks their frontend.

SETUP (run once):
    pip install playwright
    playwright install chromium

USAGE:
    # Single product
    python myntra_reviews_scraper.py --url "https://www.myntra.com/.../36603057/buy" --out reviews.csv

    # Multiple products (one URL per line in a text file)
    python myntra_reviews_scraper.py --input urls.txt --outdir reviews_out

    # If the CSV comes out empty/wrong, run with --debug to see exactly which
    # network calls were captured, then send me that list and I'll fix the parser.
    python myntra_reviews_scraper.py --url "..." --out reviews.csv --debug

NOTE: This was written and syntax-checked here, but I could not run it against
the live myntra.com from my sandbox (no network access to that domain from
where I am). Test it on a product with a handful of reviews first, and if it
comes back empty, run with --debug and paste me the output — I'll patch the
parsing logic to match whatever Myntra is actually returning.
"""

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

REVIEW_URL_HINT = re.compile(r"review", re.I)
CLICK_LABELS = ["View All", "view all", "Load More", "Show More", "View More", "Reviews"]


def capture_review_payloads(url, headless=True, debug=False, max_scroll_rounds=8, max_load_more_clicks=20):
    """Open the product page in a real browser, scroll to reviews, click any
    'load more' controls, and capture every JSON response whose URL mentions
    'review'. Returns a list of (response_url, parsed_json)."""
    captured = []

    def handle_response(response):
        try:
            if not REVIEW_URL_HINT.search(response.url):
                return
            if response.status != 200:
                return
            ctype = response.headers.get("content-type", "")
            if "json" not in ctype:
                return
            body = response.json()
            captured.append((response.url, body))
            if debug:
                print(f"[capture] {response.status} {response.url}", file=sys.stderr)
        except Exception as e:
            if debug:
                print(f"[capture-error] {response.url}: {e}", file=sys.stderr)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 900},
            locale="en-IN",
        )
        page = context.new_page()
        page.on("response", handle_response)

        if debug:
            print(f"[nav] {url}", file=sys.stderr)
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)

        # Scroll down gradually — Myntra lazy-loads the reviews section.
        for i in range(max_scroll_rounds):
            page.mouse.wheel(0, 2200)
            page.wait_for_timeout(900)

        # Try clicking whatever opens/expands the full review list.
        for label in CLICK_LABELS:
            try:
                loc = page.get_by_text(re.compile(label, re.I)).first
                if loc.is_visible(timeout=1000):
                    loc.click(timeout=2000)
                    page.wait_for_timeout(1500)
                    if debug:
                        print(f"[click] matched '{label}'", file=sys.stderr)
            except Exception:
                pass

        # Repeatedly click "load more"-style buttons until they stop appearing
        # or we hit the click cap (avoids an infinite loop on a stuck page).
        for _ in range(max_load_more_clicks):
            clicked = False
            for label in ["Load More", "Show More", "View More"]:
                try:
                    btn = page.get_by_text(re.compile(label, re.I)).first
                    if btn.is_visible(timeout=800):
                        btn.click(timeout=1500)
                        page.wait_for_timeout(1400)
                        clicked = True
                except Exception:
                    pass
            if not clicked:
                break

        browser.close()

    return captured


def find_review_lists(obj, path="root"):
    """Walk an arbitrary JSON structure and yield (path, list_of_dicts) for
    any list whose items look like review records (have a rating- or
    review-text-shaped key). This avoids hardcoding one exact schema."""
    results = []

    def looks_like_review(d):
        if not isinstance(d, dict):
            return False
        keys = {k.lower() for k in d.keys()}
        has_text = any(k in keys for k in ("review", "reviewtext", "comment", "text", "title"))
        has_rating = any(k in keys for k in ("rating", "userrating", "stars", "score"))
        return has_text or has_rating

    def walk(node, cur_path):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{cur_path}.{k}")
        elif isinstance(node, list):
            if node and all(isinstance(x, dict) for x in node) and any(looks_like_review(x) for x in node):
                results.append((cur_path, node))
            else:
                for i, v in enumerate(node):
                    walk(v, f"{cur_path}[{i}]")

    walk(obj, path)
    return results


def flatten_review(rec):
    """Pull common fields out of a review dict regardless of exact key names/casing."""
    def get_any(d, *names):
        low = {k.lower(): v for k, v in d.items()}
        for n in names:
            if n.lower() in low:
                return low[n.lower()]
        return ""

    style = rec.get("style") if isinstance(rec.get("style"), dict) else {}
    return {
        "id": get_any(rec, "id", "reviewId"),
        "rating": get_any(rec, "userRating", "rating", "stars", "score"),
        "review_text": get_any(rec, "review", "reviewText", "comment", "text"),
        "reviewer_name": get_any(rec, "userName", "name", "author"),
        "size_bought": next(
            (a.get("value") for a in rec.get("styleAttribute", []) if isinstance(a, dict) and "size" in str(a.get("name", "")).lower()),
            "",
        ) if isinstance(rec.get("styleAttribute"), list) else "",
        "upvotes": get_any(rec, "upvotes", "helpfulCount", "likes"),
        "downvotes": get_any(rec, "downvotes", "unhelpfulCount", "dislikes"),
        "updated_at": get_any(rec, "updatedAt", "date", "createdAt", "reviewDate"),
        "style_id": style.get("id", "") if style else get_any(rec, "styleId", "productId"),
        "product_url": "",  # filled in by caller
    }


def scrape_product(url, headless=True, debug=False):
    payloads = capture_review_payloads(url, headless=headless, debug=debug)

    if debug:
        print(f"[summary] captured {len(payloads)} review-related JSON response(s)", file=sys.stderr)

    all_records = []
    seen_ids = set()
    for resp_url, body in payloads:
        for _, lst in find_review_lists(body):
            for rec in lst:
                flat = flatten_review(rec)
                flat["product_url"] = url
                key = flat["id"] or json.dumps(rec, sort_keys=True)[:80]
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                all_records.append(flat)

    return all_records, payloads


def write_csv(records, out_path):
    fields = ["id", "rating", "review_text", "reviewer_name", "size_bought",
              "upvotes", "downvotes", "updated_at", "style_id", "product_url"]
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in records:
            writer.writerow(r)


def main():
    ap = argparse.ArgumentParser(description="Scrape customer reviews from a Myntra product page.")
    ap.add_argument("--url", help="Single Myntra product URL (ends in /buy)")
    ap.add_argument("--input", help="Text file with one Myntra product URL per line")
    ap.add_argument("--out", default="reviews.csv", help="Output CSV path (single-URL mode)")
    ap.add_argument("--outdir", default="reviews_out", help="Output directory (multi-URL mode)")
    ap.add_argument("--headless", action="store_true", help="Run browser headless (default: headed, more reliable)")
    ap.add_argument("--debug", action="store_true", help="Print every captured network call and match")
    ap.add_argument("--raw-json", action="store_true", help="Also dump the raw captured JSON payloads for inspection")
    args = ap.parse_args()

    if not args.url and not args.input:
        ap.error("Provide --url for a single product or --input for a file of URLs")

    urls = [args.url] if args.url else [
        line.strip() for line in Path(args.input).read_text().splitlines() if line.strip()
    ]

    for i, url in enumerate(urls, 1):
        print(f"[{i}/{len(urls)}] Scraping: {url}")
        try:
            records, payloads = scrape_product(url, headless=args.headless, debug=args.debug)
        except PWTimeout:
            print(f"  -> TIMED OUT loading the page. Skipping.", file=sys.stderr)
            continue
        except Exception as e:
            print(f"  -> ERROR: {e}", file=sys.stderr)
            continue

        if len(urls) == 1:
            out_path = args.out
        else:
            style_guess = re.search(r"/(\d+)/buy", url)
            fname = f"{style_guess.group(1)}.csv" if style_guess else f"product_{i}.csv"
            out_path = Path(args.outdir) / fname

        write_csv(records, out_path)
        print(f"  -> {len(records)} review(s) written to {out_path}")

        if not records:
            print("  -> No reviews found. Re-run this URL with --debug to see what the page actually loaded.")

        if args.raw_json:
            raw_path = Path(str(out_path).rsplit(".", 1)[0] + "_raw.json")
            raw_path.write_text(json.dumps([b for _, b in payloads], indent=2))
            print(f"  -> Raw captured JSON saved to {raw_path}")

        time.sleep(2)  # be polite between products


if __name__ == "__main__":
    main()
