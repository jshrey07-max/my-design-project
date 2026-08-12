#!/usr/bin/env python3
"""Scrape customer reviews for a Myntra product page and write them to CSV.

Usage:
    python myntra_reviews_scraper.py --url "https://www.myntra.com/35351208/buy" --out reviews.csv

Myntra does not publish an official reviews API, and the endpoint it uses
internally has changed over time. This script:
  1. Loads the product page and pulls the product's style id, either from
     the URL itself or from the `window.__myx` JSON blob embedded in the page.
  2. Tries a small list of known/likely review endpoints against that style
     id, using whichever one returns a valid review payload first.
  3. Paginates through all pages of reviews and writes them to a CSV file.

If Myntra changes its endpoint shape, pass --reviews-endpoint with a URL
template containing {style_id} and {page} placeholders to override the
built-in candidates.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

import requests

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

API_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
    "x-requested-with": "XMLHttpRequest",
}

# Candidate endpoint templates, tried in order until one returns usable data.
REVIEW_ENDPOINT_CANDIDATES = [
    "https://www.myntra.com/gateway/v2/reviews/{style_id}?page={page}&sort=RECENCY_DESC",
    "https://www.myntra.com/gateway/v2/product/{style_id}/reviews?page={page}",
    "https://www.myntra.com/review/{style_id}?page={page}",
]

STYLE_ID_RE = re.compile(r"/(\d+)/buy")
MYX_RE = re.compile(r"window\.__myx\s*=\s*(\{.*?\});\s*(?:</script>|\n\s*window\.)", re.DOTALL)


@dataclass
class Review:
    reviewer: str = ""
    rating: str = ""
    title: str = ""
    text: str = ""
    date: str = ""
    likes: str = ""
    verified_purchase: str = ""

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "Review":
        return cls(
            reviewer=str(
                raw.get("userNickName") or raw.get("userName") or raw.get("author") or ""
            ),
            rating=str(raw.get("rating") or raw.get("ratingValue") or ""),
            title=str(raw.get("title") or raw.get("reviewTitle") or ""),
            text=str(raw.get("reviewText") or raw.get("comment") or raw.get("text") or ""),
            date=str(raw.get("createdAt") or raw.get("date") or raw.get("reviewDate") or ""),
            likes=str(raw.get("likeCount") or raw.get("likes") or ""),
            verified_purchase=str(
                raw.get("verifiedPurchase") or raw.get("isVerifiedPurchase") or ""
            ),
        )


@dataclass
class ScraperStats:
    pages_fetched: int = 0
    reviews_found: int = 0
    errors: list[str] = field(default_factory=list)


def extract_style_id_from_url(url: str) -> str | None:
    match = STYLE_ID_RE.search(url)
    if match:
        return match.group(1)
    # Fall back to the last purely-numeric path segment, e.g.
    # .../roadster-men-navy-tshirt/12345678/buy or a bare numeric id.
    segments = [seg for seg in url.split("/") if seg.isdigit()]
    return segments[-1] if segments else None


def extract_style_id_from_page(html: str) -> str | None:
    match = MYX_RE.search(html)
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    pdp_data = data.get("pdpData") or {}
    style_id = pdp_data.get("id") or pdp_data.get("styleId")
    return str(style_id) if style_id else None


def fetch_product_page(session: requests.Session, url: str) -> str:
    response = session.get(url, headers=DEFAULT_HEADERS, timeout=20, allow_redirects=True)
    response.raise_for_status()
    return response.text


def resolve_style_id(session: requests.Session, url: str) -> str:
    style_id = extract_style_id_from_url(url)
    try:
        html = fetch_product_page(session, url)
        page_style_id = extract_style_id_from_page(html)
        if page_style_id:
            return page_style_id
    except requests.RequestException as exc:
        if not style_id:
            raise RuntimeError(f"Could not load product page and no id in URL: {exc}") from exc
    if not style_id:
        raise RuntimeError(f"Could not determine product style id from URL: {url}")
    return style_id


def _parse_review_payload(payload: Any) -> tuple[list[dict], bool] | None:
    """Return (raw_reviews, has_more) if payload looks like a review response."""
    if isinstance(payload, dict):
        for key in ("reviews", "reviewsList", "data", "results"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                has_more = bool(
                    payload.get("hasMoreReviews")
                    or payload.get("hasMore")
                    or payload.get("hasNext")
                )
                return candidate, has_more
    return None


def fetch_reviews_page(
    session: requests.Session,
    endpoint_template: str,
    style_id: str,
    page: int,
) -> tuple[list[dict], bool]:
    url = endpoint_template.format(style_id=style_id, page=page)
    response = session.get(url, headers=API_HEADERS, timeout=20)
    response.raise_for_status()
    payload = response.json()
    parsed = _parse_review_payload(payload)
    if parsed is None:
        raise ValueError("Response did not contain a recognizable reviews list")
    return parsed


def find_working_endpoint(
    session: requests.Session, style_id: str, candidates: list[str]
) -> tuple[str, list[dict], bool]:
    last_error: Exception | None = None
    for template in candidates:
        try:
            reviews, has_more = fetch_reviews_page(session, template, style_id, page=1)
            return template, reviews, has_more
        except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            continue
    raise RuntimeError(
        "None of the known review endpoints returned usable data. "
        "Myntra's API may have changed; pass --reviews-endpoint to override. "
        f"Last error: {last_error}"
    )


def iter_reviews(
    session: requests.Session,
    style_id: str,
    endpoint_template: str,
    first_page_reviews: list[dict],
    first_page_has_more: bool,
    max_pages: int | None,
    delay: float,
    stats: ScraperStats,
) -> Iterator[Review]:
    page = 1
    raw_reviews, has_more = first_page_reviews, first_page_has_more
    while True:
        stats.pages_fetched += 1
        for raw in raw_reviews:
            stats.reviews_found += 1
            yield Review.from_raw(raw)

        if not raw_reviews or not has_more:
            break
        if max_pages is not None and page >= max_pages:
            break

        page += 1
        time.sleep(delay)
        try:
            raw_reviews, has_more = fetch_reviews_page(session, endpoint_template, style_id, page)
        except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
            stats.errors.append(f"page {page}: {exc}")
            break


def write_reviews_csv(reviews: Iterator[Review], out_path: str) -> int:
    fieldnames = ["reviewer", "rating", "title", "text", "date", "likes", "verified_purchase"]
    count = 0
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for review in reviews:
            writer.writerow(review.__dict__)
            count += 1
    return count


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", required=True, help="Myntra product page URL")
    parser.add_argument("--out", default="reviews.csv", help="Path to write the CSV output (default: reviews.csv)")
    parser.add_argument("--max-pages", type=int, default=None, help="Maximum number of review pages to fetch (default: all)")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds to wait between page requests (default: 1.0)")
    parser.add_argument(
        "--reviews-endpoint",
        default=None,
        help="Override the review endpoint URL template, must contain {style_id} and {page}",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    session = requests.Session()
    stats = ScraperStats()

    try:
        style_id = resolve_style_id(session, args.url)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Resolved product style id: {style_id}")

    candidates = [args.reviews_endpoint] if args.reviews_endpoint else REVIEW_ENDPOINT_CANDIDATES
    try:
        endpoint_template, first_page_reviews, first_page_has_more = find_working_endpoint(
            session, style_id, candidates
        )
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Using review endpoint: {endpoint_template}")

    reviews_iter = iter_reviews(
        session,
        style_id,
        endpoint_template,
        first_page_reviews,
        first_page_has_more,
        args.max_pages,
        args.delay,
        stats,
    )
    written = write_reviews_csv(reviews_iter, args.out)

    print(f"Fetched {stats.pages_fetched} page(s), wrote {written} review(s) to {args.out}")
    for error in stats.errors:
        print(f"warning: {error}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
