import json
import os
import time
import random
import logging
import socket
import asyncio
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# ========== CONFIGURATION ==========
CACHE_FILE = "user_tweets_cache.json"
TARGET_TOTAL_TWEETS = 4000
RUN_DELAY_SECONDS = 60 * 10  # 10 minutes
MAX_RETRIES = 5
BASE_BACKOFF = 5
PLAYWRIGHT_TIMEOUT = 15000  # ms
# ====================================

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s - %(message)s')
log = logging.getLogger("scraper")

def wait_for_internet(host="8.8.8.8", port=53, timeout=3):
    while True:
        try:
            socket.setdefaulttimeout(timeout)
            socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect((host, port))
            return
        except socket.error:
            log.warning("No internet. Waiting...")
            time.sleep(5)

def save_cache(all_tweets):
    with open(CACHE_FILE, "w") as f:
        json.dump(all_tweets, f, indent=2)

def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r") as f:
            data = json.load(f)
            return {tweet["id"] for tweet in data}, data
    return set(), []

async def retry_async_with_backoff(func, *args, **kwargs):
    for attempt in range(MAX_RETRIES):
        try:
            wait_for_internet()
            return await func(*args, **kwargs)
        except Exception as e:
            wait = BASE_BACKOFF * (2 ** attempt) + random.uniform(0, 2)
            log.warning(f"Async error: {e}. Retrying in {wait:.2f}s... (Attempt {attempt+1}/{MAX_RETRIES})")
            await asyncio.sleep(wait)
    raise Exception(f"Async function failed after {MAX_RETRIES} retries.")

async def scrape_with_playwright(username, scraped_ids, all_data, limit, delay):
    log.info(f"Scraping with Playwright: @{username}")
    new_tweets = []
    count = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        url = f"https://twitter.com/{username}"
        await page.goto(url, timeout=PLAYWRIGHT_TIMEOUT)

        last_height = None
        while count < limit:
            tweets = await page.query_selector_all('article')
            for tweet in tweets[count:]:
                try:
                    tweet_url_el = await tweet.query_selector('a[href*="/status/"]')
                    if not tweet_url_el:
                        continue
                    href = await tweet_url_el.get_attribute('href')
                    tweet_id = href.split("/")[-1]

                    if tweet_id in scraped_ids:
                        continue

                    content_el = await tweet.query_selector('div[lang]')
                    text = await content_el.inner_text() if content_el else ""

                    date_el = await tweet.query_selector('time')
                    date = await date_el.get_attribute('datetime') if date_el else ""

                    tweet_data = {
                        "id": tweet_id,
                        "username": username,
                        "text": text,
                        "date": date,
                    }

                    new_tweets.append(tweet_data)
                    scraped_ids.add(tweet_id)
                    count += 1

                    log.info(f"Scraped tweet {tweet_id} from @{username}")

                    await asyncio.sleep(delay + random.uniform(0, 1))

                    if count >= limit:
                        break
                except Exception as e:
                    log.warning(f"Error parsing tweet: {e}")

            current_height = await page.evaluate('document.body.scrollHeight')
            if last_height == current_height:
                break
            last_height = current_height
            await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
            await asyncio.sleep(3)  # wait for loading

        await browser.close()

    if new_tweets:
        all_data.extend(new_tweets)
        save_cache(all_data)
        log.info(f"Saved {len(new_tweets)} new tweets for @{username}")
    else:
        log.info(f"No new tweets found for @{username}")

    return count

async def scrape_user_tweets(username, limit=50, delay=10):
    scraped_ids, all_data = load_cache()
    return await retry_async_with_backoff(scrape_with_playwright, username, scraped_ids, all_data, limit, delay)

def run_scraping_cycle(usernames, limit_per_user=50, delay_per_tweet=10):
    total_collected = 0
    scraped_ids, _ = load_cache()

    for username in usernames:
        current_total = total_collected + len(scraped_ids)
        if current_total >= TARGET_TOTAL_TWEETS:
            break

        tweets_needed = TARGET_TOTAL_TWEETS - current_total
        limit = min(limit_per_user, tweets_needed)

        collected = asyncio.run(scrape_user_tweets(username, limit=limit, delay=delay_per_tweet))
        total_collected += collected

    log.info(f"Cycle complete. Total new tweets collected this round: {total_collected}.")
    return total_collected

def main():
    usernames = [
        "coindesk", "Cointelegraph", "vitalikbuterin", "crypto", "binance"
    ]

    total_scraped = 0
    while total_scraped < TARGET_TOTAL_TWEETS:
        scraped_this_cycle = run_scraping_cycle(usernames, limit_per_user=50, delay_per_tweet=10)
        total_scraped += scraped_this_cycle

        if total_scraped >= TARGET_TOTAL_TWEETS:
            log.info("Target tweet count reached. Exiting.")
            break

        log.info(f"Waiting {RUN_DELAY_SECONDS} seconds before next cycle...")
        time.sleep(RUN_DELAY_SECONDS)

if __name__ == "__main__":
    main()
