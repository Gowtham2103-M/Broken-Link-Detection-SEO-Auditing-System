from ensurepip import version
import json
import os
import time
from collections import defaultdict
import heapq
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import urldefrag, urlparse, urlunparse

import requests

from analysis import (
    analyze_html,
    extract_links,
    extract_navigation_links,
    parse_html,
)
from classification import (
    LONG_REDIRECT_CHAIN,
    classify_link_result,
    error_kind_from_exception,
    make_issue,
)

HEADERS = {"User-Agent": "SiteAuditCrawler/1.0"}
TIMEOUT = 10            # seconds, internal pages
LINK_TIMEOUT = 5        # seconds, link-status checks
MAX_PAGES = 200
MAX_LINK_CHECKS = 1000  # unique links checked per scan (internal links first)
LINK_WORKERS = 8
CRAWL_DELAY = 0.2       # polite delay between page fetches
MAX_SCAN_SECONDS = 900  # wall-clock budget for the page-crawl phase
LINK_PHASE_SECONDS = 300
MAX_BODY_BYTES = 2_000_000

NON_PAGE_EXTENSIONS = {
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp",
    ".zip", ".gz", ".tar", ".rar", ".7z", ".exe", ".dmg", ".mp3", ".mp4",
    ".avi", ".mov", ".wmv", ".css", ".js", ".json", ".xml", ".doc", ".docx",
    ".xls", ".xlsx", ".ppt", ".pptx", ".woff", ".woff2", ".ttf", ".eot",
}

# JSON export is optional. Set to True to enable it.
EXPORT_JSON = False


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------
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


def host_key(url):
    """Hostname used to decide whether a link is internal (www-insensitive)."""
    netloc = urlparse(url).netloc.lower().split("@")[-1]
    return netloc[4:] if netloc.startswith("www.") else netloc


def is_crawlable_page(url):
    path = urlparse(url).path.lower()
    return not any(path.endswith(ext) for ext in NON_PAGE_EXTENSIONS)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def _read_text(response):
    chunks, size = [], 0
    for chunk in response.iter_content(65536):
        chunks.append(chunk)
        size += len(chunk)
        if size >= MAX_BODY_BYTES:
            break

    content_type = response.headers.get("Content-Type", "").lower()
    encoding = "utf-8"
    if "charset=" in content_type:
        encoding = content_type.split("charset=")[-1].split(";")[0].strip() or "utf-8"

    try:
        return b"".join(chunks).decode(encoding, errors="replace")
    except LookupError:
        return b"".join(chunks).decode("utf-8", errors="replace")


def fetch(url, timeout=TIMEOUT, read_body=False, session=None):
    """Request ``url`` with a brand-new response (no cached result).

    Returns a dict with ``status`` (None on network failure), ``final_url``,
    ``redirects``, ``content_type``, ``headers``, ``text``, ``error`` and
    ``error_kind``.
    """
    result = {
        "status": None,
        "final_url": "",
        "redirects": 0,
        "content_type": "",
        "headers": {},
        "text": "",
        "error": "",
        "error_kind": None,
    }

    try:
        if session is not None:
            response = session.get(
                url, timeout=timeout, allow_redirects=True, stream=True
            )
        else:
            response = requests.get(
                url,
                timeout=timeout,
                allow_redirects=True,
                stream=True,
                headers={
                    **HEADERS,
                    "Cache-Control": "no-cache, no-store, max-age=0",
                    "Pragma": "no-cache",
                },
            )
    except requests.RequestException as error:
        result["error"] = str(error)
        result["error_kind"] = error_kind_from_exception(error)
        return result

    try:
        result["status"] = response.status_code
        result["final_url"] = response.url
        result["redirects"] = len(response.history)
        result["headers"] = dict(response.headers)
        result["content_type"] = response.headers.get("Content-Type", "").lower()

        is_html = (
            "text/html" in result["content_type"]
            or "application/xhtml" in result["content_type"]
        )
        if read_body and is_html:
            result["text"] = _read_text(response)
    except requests.RequestException as error:
        # The response arrived but the body could not be read.
        result["error"] = str(error)
    finally:
        response.close()

    return result


def fetch_link(url, session=None):
    """Check a link target, retrying once for timeouts and 502/503/504."""
    result = fetch(url, timeout=LINK_TIMEOUT, session=session)

    transient = result["error_kind"] == "timeout" or result["status"] in (502, 503, 504)
    if transient:
        time.sleep(0.5)
        retry = fetch(url, timeout=LINK_TIMEOUT, session=session)
        if retry["status"] is not None or retry["error_kind"] != "timeout":
            return retry

    return result


# ---------------------------------------------------------------------------
# Crawl
# ---------------------------------------------------------------------------

def calculate_page_priority(url, depth, navigation_links, inbound_counts):
    """Return queue priority and an evidence-based explanation.

    Lower queue-priority numbers are processed first.
    """
    is_navigation = url in navigation_links
    inbound_count = len(inbound_counts.get(url, set()))

    # Lower numbers mean higher crawl priority.
    priority = depth * 10

    reasons = [f"crawl depth: {depth}"]

    if is_navigation:
        priority -= 5
        reasons.append("linked from a detected navigation element")

    if inbound_count >= 5:
        priority -= 5
        reasons.append(
            f"linked from {inbound_count} distinct internal pages"
        )
    elif inbound_count >= 2:
        priority -= 3
        reasons.append(
            f"linked from {inbound_count} distinct internal pages"
        )
    elif inbound_count == 1:
        reasons.append("linked from 1 distinct internal page")
    else:
        reasons.append("no internal incoming-link evidence recorded")

    return priority, "; ".join(reasons)


def crawl_website_detailed(
    start_url,
    max_pages=200,
    max_depth=None,
    progress_callback=None,
    crawl_delay=None,
):
    """Crawl a site and return ``{"pages", "issues", "stats"}``.

    ``max_depth`` is the number of link hops allowed from the start page
    (0 = start page only, ``None`` = unlimited). ``progress_callback`` receives
    a dict with the real counters after each page and each checked link.
    """
    start_url = normalize_url(start_url.strip())

    if urlparse(start_url).scheme not in ("http", "https"):
        raise ValueError("Enter a valid HTTP or HTTPS URL.")

    max_pages = max(1, min(int(max_pages), MAX_PAGES))
    if max_depth is not None:
        max_depth = max(0, int(max_depth))
    delay = CRAWL_DELAY if crawl_delay is None else crawl_delay

    site = host_key(start_url)
    queue = []
    queued = {start_url}
    crawled = set()
    priority_versions = defaultdict(int)
    page_priorities = {}
    page_data = {}
    processed_count = 0
    link_sources = defaultdict(set)
    navigation_links = set()
    inbound_counts = defaultdict(set)
    link_results = {}
    page_issues = []
    non_https_seen = set()
    
    def update_page_priority(url, depth):
        """Recalculate priority and enqueue a fresh version if needed."""
        priority, reason = calculate_page_priority(
            url,
            depth,
            navigation_links,
            inbound_counts,
        )
        existing = page_priorities.get(url, {})

        page_priorities[url] = {
            **existing,
            "priority": priority,
            "reason": reason,
            "depth": depth,
        }

    # Don't enqueue pages that have already been crawled.
        if url in crawled:
            return

        priority_versions[url] += 1
        version = priority_versions[url]

        heapq.heappush(
            queue,
            (priority, depth, url, version),
        )

    started = time.monotonic()

    start_priority, start_reason = calculate_page_priority(
        start_url,
        0,
        navigation_links,
        inbound_counts,
    )
    page_priorities[start_url] = {
        "priority": start_priority,
        "reason": start_reason,
        "depth": 0,
    }
    priority_versions[start_url] = 1
    heapq.heappush(queue, (start_priority, 0, start_url, 1))

    stats = {
        "start_url": start_url,
        "max_pages": max_pages,
        "max_depth": max_depth,
        "depth_reached": 0,
        "pages_crawled": 0,
        "links_found": 0,
        "links_checked": 0,
        "links_skipped": 0,
        "time_limit_reached": False,
        "fatal_error": "",
    }

    def emit(phase, current_url="", links_total=0):
        if not progress_callback:
            return
        progress_callback({
            "phase": phase,
            "pages_crawled": len(page_data),
            "pages_target": min(max_pages, len(page_data) + len(queue)),
            "links_checked": stats["links_checked"],
            "links_total": links_total,
            "current_url": current_url,
        })

    with requests.Session() as session:
        session.headers.update(HEADERS)

        # ---- Phase 1: crawl internal pages (breadth-first, depth-limited) ----
        while queue and len(page_data) < max_pages:
            if time.monotonic() - started > MAX_SCAN_SECONDS:
                stats["time_limit_reached"] = True
                break

            priority, depth, url, version = heapq.heappop(queue)

            # Ignore outdated priority entries.
            if version != priority_versions[url]:
                continue

# Never crawl the same URL twice.
            if url in crawled:
                continue

            crawled.add(url)
            processed_count += 1
            page_priorities.setdefault(url, {})
            page_priorities[url]["processed_priority"] = priority
            page_priorities[url]["processed_order"] = len(page_data) + 1
            emit("crawling", url)

            result = fetch(url, timeout=TIMEOUT, read_body=True, session=session)
            status = result["status"]
            final_url = result["final_url"]

            # If the very first request lands on another host (http -> https,
            # bare domain -> www), treat that host as the site.
            if not page_data and final_url:
                site = host_key(final_url)

            # A redirect can land on a URL that is (or will be) crawled itself.
            duplicate = False
            if final_url:
                final_norm = normalize_url(final_url)
                if final_norm != url:
                    if final_norm in queued:
                        duplicate = True
                    else:
                        queued.add(final_norm)

            title = ""
            links = []

            if status is not None and status < 400 and result["text"] and not duplicate:
                soup = parse_html(result["text"])
                title, seo_issues = analyze_html(
                    url, result["text"], result["headers"], soup=soup
                )

                for issue in seo_issues:
                    issue.update(
                        source_url=url, target_url=url, http_status=status
                    )
                    page_issues.append(issue)
                navigation_links.update(
                normalize_url(link)
                for link in extract_navigation_links(final_url or url, soup)
                )

                for raw_link in extract_links(final_url or url, soup):
                    link = normalize_url(raw_link)
                    links.append(link)
                    link_sources[link].add(url)

                    if host_key(link) == site:
                        previous_count = len(inbound_counts[link])
                        inbound_counts[link].add(url)

                    if len(inbound_counts[link]) > previous_count:
                        if link in page_priorities:
                            update_page_priority(
                            link,
                            page_priorities[link]["depth"],
                        )

                    internal = host_key(link) == site

                    if (
                        not internal
                        and urlparse(link).scheme == "http"
                        and (url, link) not in non_https_seen
                    ):
                        non_https_seen.add((url, link))
                        issue = make_issue(
                            "Non-HTTPS Outbound Link",
                            "This page links to an external site over plain HTTP.",
                        )
                        issue.update(
                            source_url=url, target_url=link, http_status=None
                        )
                        page_issues.append(issue)

                    if (
                        internal
                        and link not in queued
                        and is_crawlable_page(link)
                        and (max_depth is None or depth < max_depth)
                    ):
                        
                        next_depth = depth + 1

                        if link not in queued and link not in crawled:
                            next_depth = depth + 1
                            queued.add(link)
                            update_page_priority(link, next_depth)

            page_data[url] = {
                "url": url,
                "status": status,
                "final_url": final_url,
                "redirects": result["redirects"],
                "title": title,
                "links": links,
                "error": result["error"],
                "error_kind": result["error_kind"],
                "depth": depth,
            }
            stats["depth_reached"] = max(stats["depth_reached"], depth)

            if len(page_data) == 1 and status is None:
                stats["fatal_error"] = (
                    result["error"] or "The start URL could not be reached."
                )
                break

            emit("crawling", url)
            if delay:
                time.sleep(delay)

        stats["pages_crawled"] = len(page_data)

        # ---- Phase 2: check every unique link target ------------------------
        all_targets = list(link_sources)
        stats["links_found"] = len(all_targets)

        if not stats["fatal_error"]:
            ordered = sorted(all_targets, key=lambda t: (host_key(t) != site, t))
            targets = ordered[:MAX_LINK_CHECKS]
            stats["links_skipped"] = len(all_targets) - len(targets)
            to_request = [t for t in targets if t not in page_data]
            deadline = time.monotonic() + LINK_PHASE_SECONDS

            for target in targets:
                if target in page_data:
                    link_results[target] = page_data[target]
                    stats["links_checked"] += 1

            emit("checking_links", links_total=len(targets))

            def check(target):
                if time.monotonic() > deadline:
                    return target, None
                return target, fetch_link(target, session=None)

            if to_request:
                with ThreadPoolExecutor(max_workers=LINK_WORKERS) as pool:
                    for target, result in pool.map(check, to_request):
                        if result is None:
                            stats["links_skipped"] += 1
                            stats["time_limit_reached"] = True
                            continue
                        link_results[target] = {
                            "status": result["status"],
                            "final_url": result["final_url"],
                            "redirects": result["redirects"],
                            "error": result["error"],
                            "error_kind": result["error_kind"],
                        }
                        stats["links_checked"] += 1
                        emit("checking_links", target, links_total=len(targets))

    # ---- Issue generation ---------------------------------------------------
    issues = []

    for target, result in link_results.items():
        classification = classify_link_result(
            result.get("status"),
            result.get("error_kind"),
            result.get("redirects", 0),
            result.get("error", ""),
        )
        if not classification:
            continue

        detail = classification["detail"]
        if classification["issue_type"] in ("Redirect", "Long Redirect Chain"):
            detail += f" Final URL: {result.get('final_url')}"

        for source in sorted(link_sources.get(target, ())):
            issues.append({
                **classification,
                "detail": detail,
                "source_url": source,
                "target_url": target,
                "http_status": result.get("status"),
            })

    # Pages nothing links to (normally just the start URL) still need a verdict.
    if not stats["fatal_error"]:
        for url, page in page_data.items():
            if url in link_sources:
                continue
            classification = classify_link_result(
                page["status"],
                page["error_kind"],
                page["redirects"] if page["redirects"] >= LONG_REDIRECT_CHAIN else 0,
                page["error"],
            )
            if classification:
                issues.append({
                    **classification,
                    "source_url": url,
                    "target_url": url,
                    "http_status": page["status"],
                })

    issues.extend(page_issues)

    return {
    "pages": list(page_data.values()),
    "issues": issues,
    "stats": stats,
    "page_priorities": page_priorities,
}


def crawl_website(start_url, max_pages=200, max_depth=None, progress_callback=None):
    """Backward-compatible wrapper returning ``(pages, issues)``."""
    result = crawl_website_detailed(
        start_url,
        max_pages=max_pages,
        max_depth=max_depth,
        progress_callback=progress_callback,
    )
    return result["pages"], result["issues"]


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def _scan_notes(stats):
    notes = []
    if stats.get("time_limit_reached"):
        notes.append("Scan stopped early because the time limit was reached.")
    if stats.get("links_skipped"):
        notes.append(
            f"{stats['links_skipped']} link(s) were found but not checked "
            f"(limit {MAX_LINK_CHECKS} unique links per scan)."
        )
    return " ".join(notes) or None



def save_to_mysql(
    start_url,
    page_limit,
    pages,
    issues,
    scan_time,
    scan_id=None,
    crawl_depth=None,
    stats=None,
    page_priorities=None,
):
    
    """Save a finished crawl. Updates ``scan_id`` if given, else inserts a scan."""
    from database import ensure_schema, get_connection

    ensure_schema()
    stats = stats or {}
    page_priorities = page_priorities or {}
    connection = None
    cursor = None

    try:
        connection = get_connection()
        connection.start_transaction()
        cursor = connection.cursor()

        values = (
            len(pages),
            len(issues),
            stats.get("links_found", 0),
            stats.get("links_checked", 0),
            stats.get("links_skipped", 0),
            _scan_notes(stats),
            datetime.now(),
        )

        if scan_id is None:
            cursor.execute(
                """
                INSERT INTO scans
                (start_url, page_limit, crawl_depth, scan_time, status,
                 pages_inspected, issues_detected, links_found,
                 links_checked, links_skipped, notes, finished_at)
                VALUES (%s, %s, %s, %s, 'completed', %s, %s, %s, %s, %s, %s, %s)
                """,
                (start_url, page_limit, crawl_depth,
                 scan_time.replace(tzinfo=None)) + values,
            )
            scan_id = cursor.lastrowid
        else:
            cursor.execute(
                """
                UPDATE scans
                SET status = 'completed', progress_phase = 'done',
                    pages_inspected = %s, issues_detected = %s,
                    links_found = %s, links_checked = %s, links_skipped = %s,
                    notes = %s, finished_at = %s
                WHERE scan_id = %s
                """,
                values + (scan_id,),
            )

        
    
        cursor.executemany(
    """
    INSERT INTO pages
    (scan_id, url, http_status, final_url, redirect_count,
     title, error, depth, priority, priority_reason,
     processed_priority, processed_order)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """,
    [
        (
            scan_id,
            page["url"],
            page["status"],
            page["final_url"],
            page["redirects"],
            (page["title"] or "")[:255],
            (page["error"] or "")[:1000],
            page.get("depth"),
            page_priorities.get(page["url"], {}).get("priority"),
            page_priorities.get(page["url"], {}).get("reason"),
            page_priorities.get(page["url"], {}).get("processed_priority"),
            page_priorities.get(page["url"], {}).get("processed_order"),
        )
        for page in pages
    ],
)



        cursor.executemany(
            """
            INSERT INTO issues
            (scan_id, source_url, target_url, issue_type, http_status,
             severity, status, category, detail)
            VALUES (%s, %s, %s, %s, %s, %s, 'Open', %s, %s)
            """,
            [
                (
                    scan_id,
                    issue["source_url"],
                    issue["target_url"],
                    issue["issue_type"],
                    issue["http_status"],
                    issue["severity"],
                    issue.get("category"),
                    (issue.get("detail") or "")[:1000],
                )
                for issue in issues
            ],
        )

        connection.commit()
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

        # Optional third prompt. A closed stdin (as when the Streamlit
        # dashboard pipes input) or a blank answer means "unlimited depth".
        try:
            depth_answer = input("Maximum crawl depth (blank = unlimited): ").strip()
        except EOFError:
            depth_answer = ""
        depth = int(depth_answer) if depth_answer else None

        scan_time = datetime.now()
        result = crawl_website_detailed(
            website,
            limit,
            max_depth=depth,
            progress_callback=lambda e: print(
                f"[{e['phase']}] pages {e['pages_crawled']}/{e['pages_target']}"
                f" links {e['links_checked']}/{e['links_total']} {e['current_url']}"
            ),
        )
        pages, issues, stats = result["pages"], result["issues"], result["stats"]

        if stats["fatal_error"]:
            raise RuntimeError(f"Could not reach {website}: {stats['fatal_error']}")

        scan_id = save_to_mysql(
            website, limit, pages, issues, scan_time,
            crawl_depth=depth, stats=stats,
        )

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
                "issues": issues,
            }

            with open("crawl_report.json", "w", encoding="utf-8") as file:
                json.dump(report, file, indent=4, ensure_ascii=False)

            print("JSON report exported.")

        print("\n===== CRAWL SUMMARY =====")
        print("Pages inspected:", len(pages))
        print("Links checked:", stats["links_checked"])
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
        raise SystemExit(1)
