"""On-page SEO checks for a single fetched HTML document.

Pure functions only: give it a URL, the HTML text and the response headers and
it returns findings. No network access happens here.
"""
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from classification import make_issue

TITLE_MAX_LENGTH = 60
LONG_URL_LENGTH = 115


def _clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def parse_html(html):
    return BeautifulSoup(html or "", "html.parser")


def extract_title(soup):
    return _clean(soup.title.get_text()) if soup.title else ""


def _has_noindex(soup, headers):
    header_value = ""
    if headers:
        header_value = headers.get("X-Robots-Tag", "") or ""
    if "noindex" in header_value.lower():
        return True

    for meta in soup.find_all("meta", attrs={"name": re.compile(r"^(robots|googlebot)$", re.I)}):
        if "noindex" in (meta.get("content") or "").lower():
            return True
    return False


def analyze_html(url, html, headers=None, soup=None):
    """Return ``(title, issues)`` for one HTML page.

    Every issue carries ``issue_type``, ``severity``, ``category`` and
    ``detail``; the caller adds source/target/status fields.
    """
    soup = soup or parse_html(html)
    issues = []

    title = extract_title(soup)
    if not title:
        issues.append(make_issue(
            "Missing Page Title",
            "The page has no <title> element, or it is empty.",
        ))
    elif len(title) > TITLE_MAX_LENGTH:
        issues.append(make_issue(
            "Long Title Tag",
            f"The title is {len(title)} characters; search results usually "
            f"truncate titles beyond about {TITLE_MAX_LENGTH}.",
        ))

    description = soup.find(
        "meta", attrs={"name": re.compile(r"^description$", re.I)}
    )
    if description is None or not _clean(description.get("content")):
        issues.append(make_issue(
            "Missing Meta Description",
            "No <meta name=\"description\"> content was found.",
        ))

    h1_count = sum(1 for h1 in soup.find_all("h1") if _clean(h1.get_text()))
    if h1_count == 0:
        issues.append(make_issue("Missing H1", "The page has no non-empty <h1> heading."))
    elif h1_count > 1:
        issues.append(make_issue(
            "Multiple H1 Tags", f"The page contains {h1_count} <h1> headings."
        ))

    # alt="" is valid for decorative images, so only a *missing* attribute counts.
    missing_alt = [img for img in soup.find_all("img") if img.get("alt") is None]
    if missing_alt:
        issues.append(make_issue(
            "Missing Image ALT Text",
            f"{len(missing_alt)} image{'s' if len(missing_alt) != 1 else ''} "
            "without an alt attribute.",
        ))

    if _has_noindex(soup, headers):
        issues.append(make_issue(
            "Noindex Tag",
            "A robots meta tag or X-Robots-Tag header tells search engines "
            "not to index this page.",
        ))

    canonical = soup.find("link", rel="canonical")
    canonical_ok = False
    if canonical is not None:
        canonical_ok = bool(_clean(canonical.get("href")))
    if not canonical_ok:
        issues.append(make_issue(
            "Missing Canonical Tag",
            "No <link rel=\"canonical\"> with an href was found.",
        ))

    parsed = urlparse(url)
    path = parsed.path or ""
    if any(ch.isupper() for ch in path) or "_" in path:
        issues.append(make_issue(
            "Uppercase or Underscore in URL",
            "The URL path contains capital letters or underscores; hyphenated "
            "lowercase URLs are the usual convention.",
        ))
    if len(url) > LONG_URL_LENGTH:
        issues.append(make_issue(
            "Long URL", f"The URL is {len(url)} characters long."
        ))

    return title, issues


def extract_links(base_url, soup):
    """Return absolute http(s) link targets found in ``<a href>`` tags."""
    from urllib.parse import urljoin

    links = []
    for tag in soup.find_all("a", href=True):
        href = (tag["href"] or "").strip()
        if not href:
            continue
        absolute = urljoin(base_url, href)
        if urlparse(absolute).scheme in ("http", "https"):
            links.append(absolute)
    return links
