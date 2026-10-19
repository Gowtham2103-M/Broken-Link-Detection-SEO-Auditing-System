"""Re-verify a single issue with fresh HTTP requests and record the outcome."""
from datetime import datetime, timezone

from analysis import analyze_html, extract_links, parse_html
from classification import (
    ISSUE_TYPES,
    LINK_ISSUE_TYPES,
    classify_link_result,
)
from crawler import fetch, normalize_url

TIMEOUT = 8


def _base_result(target_url, issue_type):
    return {
        "target_url": target_url,
        "issue_type": issue_type,
        "http_status": None,
        "final_url": "",
        "redirects": 0,
        "page_title": "",
        "issue_status": "Open",
        "outcome": "",
        "current_finding": None,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "error": "",
    }


def _describe(finding):
    return finding["issue_type"] + (f": {finding['detail']}" if finding["detail"] else "")


def recheck_issue(target_url, issue_type, source_url=None):
    """Re-test an issue with a brand-new request (no cache, no shared session).

    * Link issues (broken, redirect, access restricted, ...): the target URL is
      requested again and classified with the same rules the crawler uses. The
      issue is only ``Resolved`` when the link now classifies as healthy.
    * Page SEO issues: the page is fetched and re-analysed; the issue is
      ``Resolved`` when that finding no longer appears.
    * Non-HTTPS outbound link: the source page is fetched and checked for the
      plain-HTTP link.

    A recheck that cannot reach the server stays ``Open`` and reports why.
    """
    result = _base_result(target_url, issue_type)

    if issue_type not in ISSUE_TYPES:
        result["error"] = "Unknown issue type."
        result["outcome"] = "This issue type cannot be rechecked automatically."
        return result

    if issue_type == "Non-HTTPS Outbound Link":
        return _recheck_outbound_link(result, target_url, source_url)

    if issue_type in LINK_ISSUE_TYPES:
        return _recheck_link(result, target_url, issue_type)

    return _recheck_page(result, target_url, issue_type)


def _recheck_link(result, target_url, issue_type):
    response = fetch(target_url, timeout=TIMEOUT)
    result["http_status"] = response["status"]
    result["final_url"] = response["final_url"]
    result["redirects"] = response["redirects"]
    result["error"] = response["error"]

    finding = classify_link_result(
        response["status"], response["error_kind"],
        response["redirects"], response["error"],
    )

    if finding is None:
        result["issue_status"] = "Resolved"
        result["outcome"] = (
            f"Link is healthy now (HTTP {response['status']}, no redirects)."
        )
    elif finding["issue_type"] == issue_type:
        result["outcome"] = f"Still present. {finding['detail']}"
        result["current_finding"] = finding["issue_type"]
    else:
        result["outcome"] = (
            f"The problem changed but is not fixed: now '{finding['issue_type']}'. "
            f"{finding['detail']}"
        )
        result["current_finding"] = finding["issue_type"]

    return result


def _fetch_html(result, url):
    response = fetch(url, timeout=TIMEOUT, read_body=True)
    result["http_status"] = response["status"]
    result["final_url"] = response["final_url"]
    result["redirects"] = response["redirects"]
    result["error"] = response["error"]

    if response["status"] is None:
        result["outcome"] = f"Could not reach the page: {response['error']}"
        return None
    if response["status"] >= 400:
        result["outcome"] = f"The page returned HTTP {response['status']}, so it could not be analysed."
        return None
    if not response["text"]:
        result["outcome"] = "The response was not an HTML page."
        return None
    return response


def _recheck_page(result, url, issue_type):
    response = _fetch_html(result, url)
    if response is None:
        return result

    title, findings = analyze_html(url, response["text"], response["headers"])
    result["page_title"] = title

    if any(f["issue_type"] == issue_type for f in findings):
        match = next(f for f in findings if f["issue_type"] == issue_type)
        result["outcome"] = f"Still present. {match['detail']}"
        result["current_finding"] = issue_type
    else:
        result["issue_status"] = "Resolved"
        result["outcome"] = f"'{issue_type}' no longer appears on this page."
    return result


def _recheck_outbound_link(result, target_url, source_url):
    if not source_url:
        result["error"] = "The source page is required to recheck this issue."
        result["outcome"] = result["error"]
        return result

    response = _fetch_html(result, source_url)
    if response is None:
        return result

    links = {normalize_url(link) for link in
             extract_links(response["final_url"] or source_url, parse_html(response["text"]))}

    if normalize_url(target_url) in links:
        result["outcome"] = "The source page still links to this URL over plain HTTP."
        result["current_finding"] = "Non-HTTPS Outbound Link"
    else:
        result["issue_status"] = "Resolved"
        result["outcome"] = "The source page no longer contains this plain-HTTP link."
    return result


class RecheckSaveError(Exception):
    pass


def save_recheck(issue_id, result):
    """Store a recheck and update the issue state. Raises on any failure."""
    from database import ensure_schema, get_connection

    ensure_schema()
    connection = None
    cursor = None

    try:
        connection = get_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            "SELECT issue_id, target_url, issue_type FROM issues WHERE issue_id = %s",
            (issue_id,),
        )
        issue = cursor.fetchone()

        if not issue:
            raise RecheckSaveError(f"Issue ID {issue_id} was not found.")

        if (
            issue["target_url"] != result["target_url"]
            or issue["issue_type"] != result["issue_type"]
        ):
            raise RecheckSaveError(
                "URL or issue type does not match this database issue."
            )

        checked_at = datetime.fromisoformat(
            result["checked_at"]
        ).astimezone(timezone.utc).replace(tzinfo=None)

        cursor.execute(
            """
            INSERT INTO rechecks
            (issue_id, http_status, final_url, redirect_count,
             issue_status, checked_at, error, outcome)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                issue_id,
                result["http_status"],
                result["final_url"],
                result["redirects"],
                result["issue_status"],
                checked_at,
                (result["error"] or "")[:1000],
                (result.get("outcome") or "")[:1000],
            ),
        )

        # Only a confirmed fix resolves the issue; an inconclusive or failed
        # recheck leaves it Open.
        cursor.execute(
            "UPDATE issues SET status = %s WHERE issue_id = %s",
            ("Resolved" if result["issue_status"] == "Resolved" else "Open", issue_id),
        )

        connection.commit()

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
    try:
        issue_id = int(input("Enter the MySQL issue ID: "))
    except ValueError:
        print("Invalid issue ID.")
        raise SystemExit(1)

    target = input("Enter the URL to recheck: ").strip()

    print("\nSelect the issue type:")
    types = list(ISSUE_TYPES)
    for number, name in enumerate(types, start=1):
        print(f"{number}. {name}")

    choice = input(f"Enter choice (1-{len(types)}): ").strip()

    if not choice.isdigit() or not 1 <= int(choice) <= len(types):
        print("Invalid issue type.")
        raise SystemExit(1)

    issue_type = types[int(choice) - 1]
    source = None
    if issue_type == "Non-HTTPS Outbound Link":
        source = input("Enter the source page URL: ").strip()

    result = recheck_issue(target, issue_type, source)

    print("\n===== RECHECK RESULT =====")
    for key, value in result.items():
        print(f"{key}: {value}")

    try:
        save_recheck(issue_id, result)
        print("Recheck result saved to MySQL successfully.")
    except Exception as error:
        print("Could not save the recheck:", error)
