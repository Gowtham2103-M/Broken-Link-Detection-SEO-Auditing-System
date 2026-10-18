
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from database import get_connection

HEADERS = {"User-Agent": "SiteAuditCrawler/1.0"}
TIMEOUT = 5


def recheck_issue(target_url, issue_type):
    result = {
        "target_url": target_url,
        "issue_type": issue_type,
        "http_status": None,
        "final_url": "",
        "redirects": 0,
        "page_title": "",
        "issue_status": "Open",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "error": ""
    }

    try:
        response = requests.get(
            target_url,
            timeout=TIMEOUT,
            allow_redirects=True,
            headers=HEADERS
        )

        result["http_status"] = response.status_code
        result["final_url"] = response.url
        result["redirects"] = len(response.history)

        content_type = response.headers.get("Content-Type", "").lower()
        is_success = 200 <= response.status_code < 300

        if "text/html" in content_type:
            soup = BeautifulSoup(response.text, "html.parser")
            if soup.title:
                result["page_title"] = soup.title.get_text(strip=True)

        response.close()

        if issue_type == "Broken Link":
            if is_success:
                result["issue_status"] = "Resolved"

        elif issue_type == "Missing Page Title":
            if (
                is_success
                and "text/html" in content_type
                and result["page_title"]
            ):
                result["issue_status"] = "Resolved"

        elif issue_type == "Long Redirect Chain":
            if is_success and result["redirects"] < 3:
                result["issue_status"] = "Resolved"

        else:
            result["error"] = "Unknown issue type."

    except requests.RequestException as error:
        result["error"] = str(error)

    return result


def save_recheck(issue_id, result):
    connection = None
    cursor = None

    try:
        connection = get_connection()
        cursor = connection.cursor(dictionary=True)

        # Find the issue and verify its identity
        cursor.execute(
            """
            SELECT issue_id, target_url, issue_type
            FROM issues
            WHERE issue_id = %s
            """,
            (issue_id,)
        )
        issue = cursor.fetchone()

        if not issue:
            print(f"Issue ID {issue_id} was not found.")
            return

        if (
            issue["target_url"] != result["target_url"]
            or issue["issue_type"] != result["issue_type"]
        ):
            print("URL or issue type does not match this database issue.")
            return

        checked_at = datetime.fromisoformat(
            result["checked_at"]
        ).astimezone(timezone.utc).replace(tzinfo=None)

        # Save each recheck as a separate history record
        cursor.execute(
            """
            INSERT INTO rechecks
            (issue_id, http_status, final_url, redirect_count,
             issue_status, checked_at, error)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                issue_id,
                result["http_status"],
                result["final_url"],
                result["redirects"],
                result["issue_status"],
                checked_at,
                result["error"]
            )
        )

        # Only change the issue to Resolved when confirmed.
        # Keep it Open if the recheck is inconclusive.
        if result["issue_status"] == "Resolved":
            cursor.execute(
                """
                UPDATE issues
                SET status = 'Resolved'
                WHERE issue_id = %s
                """,
                (issue_id,)
            )
        else:
            cursor.execute(
                """
                UPDATE issues
                SET status = 'Open'
                WHERE issue_id = %s
                """,
                (issue_id,)
            )

        connection.commit()
        print("Recheck result saved to MySQL successfully.")

    except Exception as error:
        if connection and connection.is_connected():
            connection.rollback()
        print("Database error:", error)

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
    print("1. Broken Link")
    print("2. Missing Page Title")
    print("3. Long Redirect Chain")

    choice = input("Enter choice (1-3): ").strip()

    issue_types = {
        "1": "Broken Link",
        "2": "Missing Page Title",
        "3": "Long Redirect Chain"
    }

    if choice not in issue_types:
        print("Invalid issue type.")
        raise SystemExit(1)

    issue_type = issue_types[choice]

    result = recheck_issue(target, issue_type)

    print("\n===== RECHECK RESULT =====")
    for key, value in result.items():
        print(f"{key}: {value}")

    save_recheck(issue_id, result)
