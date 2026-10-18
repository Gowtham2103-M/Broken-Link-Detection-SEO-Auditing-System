
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, urldefrag, urlunparse
from collections import deque, defaultdict
from datetime import datetime, timezone
from database import get_connection
import os
import json
import time

HEADERS = {"User-Agent": "SiteAuditCrawler/1.0"}
TIMEOUT = 10
MAX_PAGES = 200
MAX_LINK_CHECKS = 50

# JSON export is optional. Set to True to enable it.
EXPORT_JSON = False


def normalize_url(url):
    url, _ = urldefrag(url)
    p = urlparse(url)

    return urlunparse((
        p.scheme.lower(),
        p.netloc.lower(),
        p.path or "/",
        "",
        p.query,
        ""
    ))


def crawl_website(start_url, max_pages=200):
    start_url = normalize_url(start_url.strip())

    if urlparse(start_url).scheme not in ("http", "https"):
        raise ValueError("Enter a valid HTTP or HTTPS URL.")

    domain = urlparse(start_url).netloc.lower()
    queue = deque([start_url])
    visited = set()
    page_data = {}
    link_sources = defaultdict(set)
    link_results = {}
    issues = []

    with requests.Session() as session:
        session.headers.update(HEADERS)

        # Phase 1: Crawl internal pages.
        while queue and len(visited) < max_pages:
            url = normalize_url(queue.popleft())

            if url in visited:
                continue

            visited.add(url)
            print(f"Crawling page {len(visited)}/{max_pages}: {url}")

            try:
                response = session.get(
                    url, timeout=TIMEOUT, allow_redirects=True
                )

                content_type = response.headers.get(
                    "Content-Type", ""
                ).lower()

                title = ""
                links = []

                if "text/html" in content_type:
                    soup = BeautifulSoup(response.text, "html.parser")

                    if soup.title:
                        title = soup.title.get_text(strip=True)

                    for tag in soup.find_all("a", href=True):
                        link = normalize_url(
                            urljoin(response.url, tag["href"])
                        )

                        if urlparse(link).scheme not in ("http", "https"):
                            continue

                        links.append(link)
                        link_sources[link].add(url)

                        if urlparse(link).netloc.lower() == domain:
                            if link not in visited and link not in queue:
                                queue.append(link)

                page_data[url] = {
                    "url": url,
                    "status": response.status_code,
                    "final_url": response.url,
                    "redirects": len(response.history),
                    "title": title,
                    "links": links,
                    "error": ""
                }

                print(response.status_code, url)
                response.close()

            except requests.RequestException as error:
                page_data[url] = {
                    "url": url,
                    "status": None,
                    "final_url": "",
                    "redirects": 0,
                    "title": "",
                    "links": [],
                    "error": str(error)
                }
                print("Request failed:", url, error)

            time.sleep(0.2)

        # Phase 2: Check unique links.
        targets = list(link_sources.keys())[:MAX_LINK_CHECKS]
        print(f"\nChecking up to {len(targets)} unique links...")

        for index, target in enumerate(targets, start=1):
            if target in page_data:
                link_results[target] = page_data[target]
                print(f"Checked {index}/{len(targets)}")
                continue

            try:
                response = session.get(
                    target, timeout=5, allow_redirects=True
                )

                link_results[target] = {
                    "status": response.status_code,
                    "final_url": response.url,
                    "redirects": len(response.history),
                    "error": ""
                }
                response.close()

            except requests.RequestException as error:
                link_results[target] = {
                    "status": None,
                    "final_url": "",
                    "redirects": 0,
                    "error": str(error)
                }

            print(f"Checked {index}/{len(targets)}")
            time.sleep(0.1)

    # Detect broken links.
    for target, result in link_results.items():
        status = result.get("status")

        if status is None or status >= 400:
            for source in sorted(link_sources[target]):
                issues.append({
                    "source_url": source,
                    "target_url": target,
                    "issue_type": "Broken Link",
                    "http_status": status,
                    "severity": "High"
                })

    # Detect SEO and redirect issues.
    for url, page in page_data.items():
        status = page["status"]

        if status is not None and status < 400:
            if not page["title"] and page["final_url"]:
                issues.append({
                    "source_url": url,
                    "target_url": url,
                    "issue_type": "Missing Page Title",
                    "http_status": status,
                    "severity": "Medium"
                })

            if page["redirects"] >= 3:
                issues.append({
                    "source_url": url,
                    "target_url": url,
                    "issue_type": "Long Redirect Chain",
                    "http_status": status,
                    "severity": "Medium"
                })

    return list(page_data.values()), issues


def save_to_mysql(start_url, page_limit, pages, issues, scan_time):
    connection = None
    cursor = None

    try:
        connection = get_connection()
        connection.start_transaction()
        cursor = connection.cursor()

        # Insert scan summary.
        cursor.execute(
            """
            INSERT INTO scans
            (start_url, page_limit, pages_inspected,
             issues_detected, scan_time)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                start_url,
                page_limit,
                len(pages),
                len(issues),
                scan_time.replace(tzinfo=None)
            )
        )

        scan_id = cursor.lastrowid

        # Insert crawled pages.
        for page in pages:
            cursor.execute(
                """
                INSERT INTO pages
                (scan_id, url, http_status, final_url,
                 redirect_count, title, error)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    scan_id,
                    page["url"],
                    page["status"],
                    page["final_url"],
                    page["redirects"],
                    page["title"],
                    page["error"]
                )
            )

        # Insert detected issues.
        for issue in issues:
            cursor.execute(
                """
                INSERT INTO issues
                (scan_id, source_url, target_url, issue_type,
                 http_status, severity, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    scan_id,
                    issue["source_url"],
                    issue["target_url"],
                    issue["issue_type"],
                    issue["http_status"],
                    issue["severity"],
                    "Open"
                )
            )

        connection.commit()
        print("\nScan saved directly to MySQL successfully.")
        print("Scan ID:", scan_id)
        return scan_id

    except Exception:
        if connection and connection.is_connected():
            connection.rollback()
        raise

    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()


if __name__ == "__main__":
    website = input("Enter website URL: ").strip()

    try:
        limit = int(input("Maximum pages (1-200): "))
        limit = max(1, min(limit, MAX_PAGES))

        scan_time = datetime.now(timezone.utc)
        pages, issues = crawl_website(website, limit)

        # Save directly to MySQL.
        scan_id = save_to_mysql(
            website, limit, pages, issues, scan_time
        )

        # Optional JSON export.
        if EXPORT_JSON:
            report = {
                "project": "SiteAudit",
                "scan_id": scan_id,
                "scan_time": scan_time.isoformat(),
                "start_url": website,
                "page_limit": limit,
                "pages_inspected": len(pages),
                "issues_detected": len(issues),
                "pages": pages,
                "issues": issues
            }

            with open("crawl_report.json", "w", encoding="utf-8") as file:
                json.dump(report, file, indent=4, ensure_ascii=False)

            print("JSON report exported.")

        print("\n===== CRAWL SUMMARY =====")
        print("Pages inspected:", len(pages))
        print("Issues detected:", len(issues))
        print("MySQL scan ID:", scan_id)

        grouped = defaultdict(list)
        for issue in issues:
            grouped[issue["source_url"]].append(issue)

        for source, source_issues in grouped.items():
            print("\nSource page:", source)
            for issue in source_issues:
                print(" Issue:", issue["issue_type"])
                print(" Target:", issue["target_url"])
                print(" HTTP status:", issue["http_status"])
                print(" Severity:", issue["severity"])

    except ValueError as error:
        print("Invalid input:", error)
    except Exception as error:
        print("Scan or database save failed:", error)
