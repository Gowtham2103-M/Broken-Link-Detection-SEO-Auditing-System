"""Issue classification, severity penalties and health-score calculation.

This module is intentionally free of database and network code so that the
crawler, the recheck logic, the API and the tests all apply exactly the same
rules.

Severity model (from the SiteAudit specification document):

    Critical  10 penalty points per issue
    Medium     3 penalty points per issue   (shown as "Warning")
    Low        1 penalty point  per issue   (shown as "Info")

Health score:

    max(0, min(100, 100 - total_penalty_points / max(total_pages_crawled, 10)))
"""

# --------------------------------------------------------------------------
# Severity
# --------------------------------------------------------------------------
SEVERITY_CRITICAL = "Critical"
SEVERITY_MEDIUM = "Medium"
SEVERITY_LOW = "Low"

PENALTIES = {
    SEVERITY_CRITICAL: 8,
    SEVERITY_MEDIUM: 3,
    SEVERITY_LOW: 0,
}

SEVERITY_RANK = {
    SEVERITY_CRITICAL: 0,
    SEVERITY_MEDIUM: 1,
    SEVERITY_LOW: 2,
}

# Label shown in the UI for each stored severity.
SEVERITY_LABEL = {
    SEVERITY_CRITICAL: "Critical",
    SEVERITY_MEDIUM: "Warning",
    SEVERITY_LOW: "Info",
}

_SEVERITY_ALIASES = {
    "critical": SEVERITY_CRITICAL,
    "high": SEVERITY_CRITICAL,  # legacy value written by the original crawler
    "medium": SEVERITY_MEDIUM,
    "warning": SEVERITY_MEDIUM,
    "low": SEVERITY_LOW,
    "info": SEVERITY_LOW,
}


def normalize_severity(value):
    """Map any stored/legacy severity string to Critical / Medium / Low."""
    return _SEVERITY_ALIASES.get(str(value or "").strip().lower(), SEVERITY_LOW)


# --------------------------------------------------------------------------
# Categories
# --------------------------------------------------------------------------
CAT_BROKEN = "broken_link"          # confirmed dead link (404/410, DNS, refused, SSL)
CAT_SERVER = "server_error"         # target answered with 5xx
CAT_ACCESS = "access_restricted"    # 401/403/407/429: blocked, NOT confirmed broken
CAT_UNVERIFIED = "unverified"       # timeouts / odd responses that need a manual check
CAT_REDIRECT = "redirect"
CAT_SEO = "seo"

CATEGORY_LABEL = {
    CAT_BROKEN: "Broken link",
    CAT_SERVER: "Server error",
    CAT_ACCESS: "Access restricted",
    CAT_UNVERIFIED: "Needs verification",
    CAT_REDIRECT: "Redirect",
    CAT_SEO: "SEO",
}

# Lower number = shown earlier in the priority-ordered table.
CATEGORY_RANK = {
    CAT_BROKEN: 0,
    CAT_SERVER: 1,
    CAT_UNVERIFIED: 2,
    CAT_ACCESS: 3,
    CAT_REDIRECT: 4,
    CAT_SEO: 5,
}

# Statuses that mean "the server refused an automated request" rather than
# "the page does not exist".
ACCESS_RESTRICTED_STATUSES = {401, 403, 407, 429}
CONFIRMED_BROKEN_STATUSES = {404, 410}

LONG_REDIRECT_CHAIN = 3  # redirects >= this are a chain, not a single hop

# --------------------------------------------------------------------------
# Issue-type catalogue: type -> (severity, category)
# --------------------------------------------------------------------------

ISSUE_TYPES = {
    # Link / response problems
    "Broken Link": (SEVERITY_CRITICAL, CAT_BROKEN),
    "SSL/HTTPS Certificate Error": (SEVERITY_CRITICAL, CAT_BROKEN),
    "Server Error": (SEVERITY_CRITICAL, CAT_SERVER),
    "Access Restricted": (SEVERITY_LOW, CAT_ACCESS),
    "Link Timeout": (SEVERITY_MEDIUM, CAT_UNVERIFIED),
    "Unverified Link": (SEVERITY_MEDIUM, CAT_UNVERIFIED),
    "Redirect": (SEVERITY_LOW, CAT_REDIRECT),
    "Long Redirect Chain": (SEVERITY_MEDIUM, CAT_REDIRECT),

    # On-page SEO
    "Noindex Tag": (SEVERITY_CRITICAL, CAT_SEO),
    "Missing Canonical Tag": (SEVERITY_LOW, CAT_SEO),
    "Missing Page Title": (SEVERITY_MEDIUM, CAT_SEO),
    "Long Title Tag": (SEVERITY_LOW, CAT_SEO),
    "Missing Meta Description": (SEVERITY_LOW, CAT_SEO),
    "Missing Image ALT Text": (SEVERITY_MEDIUM, CAT_SEO),
    "Missing H1": (SEVERITY_MEDIUM, CAT_SEO),
    "Multiple H1 Tags": (SEVERITY_LOW, CAT_SEO),
    "Non-HTTPS Outbound Link": (SEVERITY_LOW, CAT_SEO),
    "Uppercase or Underscore in URL": (SEVERITY_LOW, CAT_SEO),
    "Long URL": (SEVERITY_LOW, CAT_SEO),
}


# Issue types produced by checking a link target's HTTP response.
LINK_ISSUE_TYPES = {
    "Broken Link",
    "SSL/HTTPS Certificate Error",
    "Server Error",
    "Access Restricted",
    "Link Timeout",
    "Unverified Link",
    "Redirect",
    "Long Redirect Chain",
}


def make_issue(issue_type, detail="", **extra):
    """Build an issue dict with severity/category taken from the catalogue."""
    severity, category = ISSUE_TYPES[issue_type]
    issue = {
        "issue_type": issue_type,
        "severity": severity,
        "category": category,
        "detail": detail or "",
    }
    issue.update(extra)
    return issue


# --------------------------------------------------------------------------
# Link-response classification
# --------------------------------------------------------------------------
def classify_link_result(status, error_kind=None, redirects=0, error_message=""):
    """Classify the result of requesting a link target.

    Returns ``None`` when the link is healthy, otherwise a dict with
    ``issue_type``, ``severity``, ``category`` and ``detail``.

    ``error_kind`` is one of ``ssl``, ``timeout``, ``connection``,
    ``redirect_loop``, ``other`` (or ``None`` when a response was received).
    """
    if status is None:
        message = (error_message or "").strip()
        if error_kind == "ssl":
            return make_issue(
                "SSL/HTTPS Certificate Error",
                f"TLS/SSL handshake failed. {message}".strip(),
            )
        if error_kind == "timeout":
            return make_issue(
                "Link Timeout",
                "The server did not respond before the timeout. "
                "It may be slow, temporarily down, or blocking the crawler.",
            )
        if error_kind == "redirect_loop":
            return make_issue(
                "Broken Link", "Redirect loop: too many redirects."
            )
        if error_kind == "connection":
            return make_issue(
                "Broken Link",
                "Could not connect to the server (DNS lookup failed or "
                f"connection refused). {message}".strip(),
            )
        return make_issue(
            "Unverified Link",
            f"The request failed for an unclassified reason. {message}".strip(),
        )

    if status in CONFIRMED_BROKEN_STATUSES:
        return make_issue("Broken Link", f"The server returned HTTP {status}.")

    if status in ACCESS_RESTRICTED_STATUSES:
        return make_issue(
            "Access Restricted",
            f"The server returned HTTP {status}. The page may exist but refuses "
            "automated requests, so it is not counted as a confirmed broken link.",
        )

    if status == 408:
        return make_issue("Link Timeout", "The server returned HTTP 408.")

    if 500 <= status <= 599:
        return make_issue("Server Error", f"The server returned HTTP {status}.")

    if 400 <= status <= 499 or 300 <= status <= 399 or status >= 600:
        return make_issue(
            "Unverified Link",
            f"The server returned HTTP {status}, which needs a manual check.",
        )

    # 1xx / 2xx: reachable. Look at the redirect chain.
    if redirects >= LONG_REDIRECT_CHAIN:
        return make_issue(
            "Long Redirect Chain",
            f"The link passes through {redirects} redirects before the final page.",
        )
    if redirects >= 1:
        return make_issue(
            "Redirect",
            f"The link redirects {redirects} time{'s' if redirects != 1 else ''} "
            "before the final page.",
        )

    return None


def error_kind_from_exception(error):
    """Return the ``error_kind`` string for a ``requests`` exception."""
    import requests  # local import keeps this module importable anywhere

    if isinstance(error, requests.exceptions.SSLError):
        return "ssl"
    if isinstance(error, requests.exceptions.TooManyRedirects):
        return "redirect_loop"
    if isinstance(error, requests.exceptions.Timeout):
        return "timeout"
    if isinstance(error, requests.exceptions.ConnectionError):
        return "connection"
    return "other"


# --------------------------------------------------------------------------
# Legacy / stored-row normalisation
# --------------------------------------------------------------------------
def effective_issue(row):
    """Return the issue as it should be displayed and scored.

    Rows written by the new crawler carry ``category`` and a normalised
    severity. Rows written by the original crawler have neither, and the old
    crawler labelled every HTTP >= 400 (including 403) as a "High" broken link.
    Those rows are reclassified from their stored HTTP status so historical
    reports follow the same rules as new scans. The database is not modified.
    """
    issue_type = row.get("issue_type") or "Other finding"
    status = row.get("http_status")
    category = row.get("category")
    severity = normalize_severity(row.get("severity"))

    if not category:
        if issue_type == "Broken Link":
            reclassified = classify_link_result(
                status, error_kind="connection" if status is None else None
            )
            if reclassified is not None:
                issue_type = reclassified["issue_type"]
                severity = reclassified["severity"]
                category = reclassified["category"]
            else:
                category = CAT_BROKEN
        elif issue_type == "Long Redirect Chain":
            category = CAT_REDIRECT
        elif issue_type in ISSUE_TYPES:
            category = ISSUE_TYPES[issue_type][1]
        else:
            category = CAT_SEO

    result = dict(row)
    result["issue_type"] = issue_type
    result["severity"] = severity
    result["severity_label"] = SEVERITY_LABEL[severity]
    result["category"] = category
    result["category_label"] = CATEGORY_LABEL.get(category, category)
    result["penalty"] = PENALTIES[severity]
    result["priority"] = priority_key(result)[0] * 10 + priority_key(result)[1]
    return result


def priority_key(issue):
    """Sort key: severity first, then category, then status, then type."""
    status = issue.get("http_status")
    return (
        SEVERITY_RANK.get(normalize_severity(issue.get("severity")), 9),
        CATEGORY_RANK.get(issue.get("category"), 9),
        status if isinstance(status, int) else 999,
        str(issue.get("issue_type") or ""),
        str(issue.get("target_url") or ""),
    )


# --------------------------------------------------------------------------
# Health score and summary metrics
# --------------------------------------------------------------------------
def health_score(total_penalty, pages_crawled=None):
    """Calculate health score from severity-weighted open issue penalties."""
    score = 100 - float(total_penalty or 0)
    return round(max(0.0, min(100.0, score)), 1)


def summarize(issues, pages_crawled, links_checked=0):
    """Aggregate *effective* issues into the numbers shown on the dashboard.

    Only issues whose state is not ``Resolved`` count towards penalties,
    severity counts and metrics, so rechecked-and-fixed issues stop
    lowering the score.
    """
    open_issues = [
        i for i in issues if str(i.get("status") or "Open").lower() != "resolved"
    ]
    severity_counts = {
        SEVERITY_CRITICAL: 0,
        SEVERITY_MEDIUM: 0,
        SEVERITY_LOW: 0,
    }
    category_counts = {key: 0 for key in CATEGORY_RANK}
    penalty = 0

    for issue in open_issues:
        severity_counts[issue["severity"]] += 1
        category_counts[issue["category"]] = (
            category_counts.get(issue["category"], 0) + 1
        )
        penalty += PENALTIES[issue["severity"]]

    return {
        "pages_crawled": int(pages_crawled or 0),
        "links_checked": int(links_checked or 0),
        "broken_links": category_counts[CAT_BROKEN],
        "server_errors": category_counts[CAT_SERVER],
        "access_restricted": category_counts[CAT_ACCESS],
        "unverified_links": category_counts[CAT_UNVERIFIED],
        "redirects": category_counts[CAT_REDIRECT],
        "seo_issues": category_counts[CAT_SEO],
        "open_issues": len(open_issues),
        "resolved_issues": len(issues) - len(open_issues),
        "severity_counts": {
            "critical": severity_counts[SEVERITY_CRITICAL],
            "warning": severity_counts[SEVERITY_MEDIUM],
            "info": severity_counts[SEVERITY_LOW],
        },
        "penalty_points": penalty,
        "health_score": health_score(penalty, pages_crawled),
    }
