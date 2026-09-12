"""
Web Scraping - Books Dataset
CodeAlpha Internship - Task: Web Scraping

Scrapes book data (title, price, availability, rating, product URL,
category) from https://books.toscrape.com/ - a sandbox site that is
built and publicly offered specifically for scraping practice
("We love being scraped!").

Author: Harshal Pardeshi
"""

import argparse
import re
import sys
import time
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://books.toscrape.com/"
START_URL = urljoin(BASE_URL, "catalogue/page-1.html")
HOME_URL = BASE_URL  # used to read the category sidebar

REQUEST_TIMEOUT = 10        # seconds
REQUEST_DELAY = 0.5         # polite delay between requests, in seconds
MAX_RETRIES = 3

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; CodeAlphaBookScraper/1.0; "
        "+https://books.toscrape.com/ educational scraping project)"
    )
}

RATING_WORDS = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}


def fetch_page(url, retries=MAX_RETRIES):
    """
    Send an HTTP GET request to `url` and return a requests.Response.

    Handles connection errors, timeouts, and non-200 status codes with
    a small retry loop. Returns None if the page could not be fetched
    after all retries, and the caller is responsible for skipping it.
    """
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            return response
        except requests.exceptions.Timeout:
            print(f"[warning] Timeout fetching {url} (attempt {attempt}/{retries})")
        except requests.exceptions.ConnectionError:
            print(f"[warning] Connection error fetching {url} (attempt {attempt}/{retries})")
        except requests.exceptions.HTTPError as exc:
            print(f"[warning] HTTP error {exc} for {url} (attempt {attempt}/{retries})")
        except requests.exceptions.RequestException as exc:
            print(f"[warning] Request failed for {url}: {exc} (attempt {attempt}/{retries})")

        if attempt < retries:
            time.sleep(REQUEST_DELAY * attempt)  # simple backoff

    print(f"[error] Giving up on {url} after {retries} attempts.")
    return None


def parse_book(card, page_url):
    """
    Extract one book's fields from its <article class="product_pod"> tag.

    Returns a dict. Any field that cannot be found is set to None rather
    than fabricated, and a warning is printed so missing data is visible.
    """
    book = {
        "title": None,
        "price": None,
        "availability": None,
        "rating": None,
        "product_url": None,
    }

    try:
        title_tag = card.h3.a
        book["title"] = title_tag["title"].strip()
        book["product_url"] = urljoin(page_url, title_tag["href"])
    except (AttributeError, KeyError, TypeError):
        print("[warning] Could not extract title/URL for a book card.")

    price_tag = card.select_one("p.price_color")
    if price_tag:
        book["price"] = price_tag.get_text(strip=True)
    else:
        print(f"[warning] Missing price for: {book['title']}")

    availability_tag = card.select_one("p.instock.availability")
    if availability_tag:
        book["availability"] = availability_tag.get_text(strip=True)
    else:
        print(f"[warning] Missing availability for: {book['title']}")

    rating_tag = card.select_one("p.star-rating")
    if rating_tag:
        # class list looks like ["star-rating", "Three"] - the second
        # class name is the rating word used on this site.
        rating_classes = [c for c in rating_tag.get("class", []) if c != "star-rating"]
        book["rating"] = rating_classes[0] if rating_classes else None
    else:
        print(f"[warning] Missing rating for: {book['title']}")

    return book


def scrape_listing_page(url):
    """
    Fetch one catalogue listing page and parse every book card on it.

    Returns a tuple: (list_of_book_dicts, next_page_url_or_None).
    The next-page URL is read from the page's own "next" link rather
    than assumed, so this keeps working even if the site changes its
    page-count or URL scheme.
    """
    response = fetch_page(url)
    if response is None:
        return [], None

    soup = BeautifulSoup(response.text, "html.parser")
    cards = soup.select("article.product_pod")
    books = [parse_book(card, url) for card in cards]

    next_link = soup.select_one("li.next a")
    next_url = urljoin(url, next_link["href"]) if next_link else None

    return books, next_url


def scrape_all_pages(start_url, max_pages=None, delay=REQUEST_DELAY):
    """
    Walk the "next" pagination chain starting at `start_url`, collecting
    every book along the way.

    max_pages: cap the number of listing pages visited (useful for a
    quick test run). None means "follow pagination until it ends"
    (50 pages / ~1000 books on this site as of inspection).
    """
    all_books = []
    url = start_url
    page_number = 1

    while url:
        print(f"[info] Scraping listing page {page_number}: {url}")
        books, next_url = scrape_listing_page(url)
        all_books.extend(books)

        if max_pages is not None and page_number >= max_pages:
            break

        url = next_url
        page_number += 1
        if url:
            time.sleep(delay)  # be polite - don't hammer the server

    print(f"[info] Finished listing pages. Collected {len(all_books)} raw records.")
    return all_books


def get_category_map(delay=REQUEST_DELAY):
    """
    Build a {product_url: category_name} lookup.

    The listing pages used by scrape_all_pages() do not show a book's
    category - that only appears in the category-specific listing pages
    (sidebar links) and on each book's own detail page. Crawling the
    ~50 category listing pages is far cheaper than opening all ~1000
    individual book pages, so that's the approach used here.
    """
    response = fetch_page(HOME_URL)
    if response is None:
        print("[warning] Could not load the homepage to read category links; "
              "category will be left blank for all books.")
        return {}

    soup = BeautifulSoup(response.text, "html.parser")
    category_links = soup.select("div.side_categories ul li ul li a")

    category_map = {}
    for link in category_links:
        category_name = link.get_text(strip=True)
        category_url = urljoin(HOME_URL, link["href"])

        page_url = category_url
        while page_url:
            time.sleep(delay)
            books, next_url = scrape_listing_page(page_url)
            for book in books:
                if book["product_url"]:
                    category_map[book["product_url"]] = category_name
            page_url = next_url

    print(f"[info] Built category map for {len(category_map)} books "
          f"across {len(category_links)} categories.")
    return category_map


def clean_data(df):
    """
    Clean the raw scraped DataFrame:
      - strip stray whitespace from text columns
      - convert price to a numeric float column (price_gbp)
      - normalise availability to a simple "In stock" / "Out of stock" value
      - convert the star-rating word into a 1-5 integer
      - drop exact duplicate rows (same product URL)
    """
    df = df.copy()

    for col in ["title", "availability", "category"]:
        if col in df.columns:
            df[col] = df[col].astype("string").str.strip()

    # Price: "£51.77" -> 51.77 (float). If the pattern isn't found, leave NaN
    # rather than guessing a number.
    def extract_price(value):
        if not isinstance(value, str):
            return None
        match = re.search(r"[\d]+\.\d+|\d+", value)
        return float(match.group()) if match else None

    df["price_gbp"] = df["price"].apply(extract_price)

    # Availability: collapse to a clean Yes/No style label.
    def normalise_availability(value):
        if not isinstance(value, str):
            return None
        return "In stock" if "in stock" in value.lower() else "Out of stock"

    df["availability"] = df["availability"].apply(normalise_availability)

    df["rating"] = df["rating"].map(RATING_WORDS)

    before = len(df)
    df = df.drop_duplicates(subset=["product_url"]).reset_index(drop=True)
    removed = before - len(df)
    if removed:
        print(f"[info] Removed {removed} duplicate record(s).")

    return df


def save_data(df, filename):
    """Save the final DataFrame to CSV."""
    df.to_csv(filename, index=False)
    print(f"[info] Saved {len(df)} records to {filename}")


def print_validation_summary(df):
    """Print the checks requested for confirming the scrape worked."""
    print("\n===== DATASET PREVIEW =====")
    print(df.head())

    print("\n===== SHAPE =====")
    print(df.shape)

    print("\n===== INFO =====")
    df.info()

    print("\n===== MISSING VALUES =====")
    print(df.isnull().sum())

    print("\n===== DUPLICATE ROWS =====")
    print(df.duplicated().sum())

    print("\n===== VALIDATION SUMMARY =====")
    print(f"Records collected : {len(df)}")
    print(f"Columns           : {list(df.columns)}")
    if "category" in df.columns:
        print(f"Unique categories : {df['category'].nunique()}")
    if "price_gbp" in df.columns and df["price_gbp"].notna().any():
        print(f"Price range (GBP) : {df['price_gbp'].min()} - {df['price_gbp'].max()}")
    if "rating" in df.columns:
        print("Rating distribution:")
        print(df["rating"].value_counts(dropna=False).sort_index())


def print_mini_analysis(df):
    """Small analysis section to show the dataset is actually usable."""
    print("\n===== MINI ANALYSIS =====")
    if df["price_gbp"].notna().any():
        print(f"Average price : £{df['price_gbp'].mean():.2f}")
        print(f"Min price     : £{df['price_gbp'].min():.2f}")
        print(f"Max price     : £{df['price_gbp'].max():.2f}")

    print("\nBooks by rating:")
    print(df["rating"].value_counts(dropna=False).sort_index())

    if "category" in df.columns and df["category"].notna().any():
        print("\nBooks by category (top 10):")
        print(df["category"].value_counts(dropna=False).head(10))

    print("\nTop 5 highest-priced books:")
    top5 = df.sort_values("price_gbp", ascending=False).head(5)
    print(top5[["title", "price_gbp"]].to_string(index=False))


def parse_args():
    parser = argparse.ArgumentParser(description="Scrape book data from books.toscrape.com")
    parser.add_argument(
        "--max-pages", type=int, default=None,
        help="Limit how many listing pages to scrape (default: all, ~50 pages / 1000 books)",
    )
    parser.add_argument(
        "--no-category", action="store_true",
        help="Skip crawling category pages (faster, leaves category column blank)",
    )
    parser.add_argument(
        "--delay", type=float, default=REQUEST_DELAY,
        help=f"Delay in seconds between requests (default: {REQUEST_DELAY})",
    )
    parser.add_argument(
        "--output", type=str, default="books_dataset.csv",
        help="Output CSV filename (default: books_dataset.csv)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    print("[info] Starting scrape of books.toscrape.com ...")
    raw_books = scrape_all_pages(START_URL, max_pages=args.max_pages, delay=args.delay)

    if not raw_books:
        print("[error] No records were scraped. Check your internet connection "
              "and that books.toscrape.com is reachable, then try again.")
        sys.exit(1)

    df = pd.DataFrame(raw_books)

    if args.no_category:
        df["category"] = None
    else:
        category_map = get_category_map(delay=args.delay)
        df["category"] = df["product_url"].map(category_map)

    df = clean_data(df)

    print_validation_summary(df)
    print_mini_analysis(df)

    save_data(df, args.output)


if __name__ == "__main__":
    main()
