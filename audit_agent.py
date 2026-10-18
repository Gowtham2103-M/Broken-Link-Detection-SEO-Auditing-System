
from database import get_connection


def analyze_issue(issue):
    issue_type = issue["issue_type"]
    status = issue["http_status"]
    target = issue["target_url"]

    recommendations = {
        "Bn Link": {
            "priority": 1,
            "impact": "Visitors may reach a missing or inaccessible page. "
                      "Broken internal links can also affect site navigation.",
            "action": "Check whether the destination URL is correct. "
                      "Restore the missing page, update the link, or remove "
                      "the link if the destination is no longer needed.",
            "fix_example": "Open the source page and replace the broken "
                           "href with the correct destination URL."
        },
        "Long Redirect Chain": {
            "priority": 2,
            "impact": "Multiple redirects can increase page-loading time "
                      "and make crawling less efficient.",
            "action": "Inspect the redirect destinations and point links "
                      "directly to the final destination where appropriate.",
            "fix_example": "Change a link from /old-page to /final-page "
                           "if the intermediate redirects are unnecessary."
        },
        "Missing Page Title": {
            "priority": 2,
            "impact": "A missing HTML title can make the page harder to "
                      "understand in browser tabs and search results.",
            "action": "Add a unique, descriptive title inside the HTML head.",
            "fix_example": "<title>Products | Example Website</title>"
        }
    }

    details = recommendations.get(issue_type, {
        "priority": 3,
        "impact": "The detected issue may affect website quality.",
        "action": "Inspect the page and verify the finding before making changes.",
        "fix_example": "Review the relevant URL and its server response."
    })

    if status is None:
        details = details.copy()
        details["impact"] += (
            " The crawler could not obtain an HTTP status, so the cause "
            "needs verification."
        )

    return {
        "issue_id": issue["issue_id"],
        "issue_type": issue_type,
        "target_url": target,
        "http_status": status,
        "status": issue["status"],
        "priority": details["priority"],
        "priority_label": {
            1: "High",
            2: "Medium",
            3: "Low"
        }[details["priority"]],
        "impact": details["impact"],
        "recommended_action": details["action"],
        "example_fix": details["fix_example"]
    }


def get_audit_recommendations(scan_id):
    connection = None
    cursor = None

    try:
        connection = get_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT issue_id, issue_type, target_url,
                   http_status, severity, status
            FROM issues
            WHERE scan_id = %s
            """,
            (scan_id,)
        )

        issues = cursor.fetchall()

        analyses = [analyze_issue(issue) for issue in issues]

        # Prioritize open issues first, then by recommendation priority.
        analyses.sort(
            key=lambda item: (
                item["status"] != "Open",
                item["priority"]
            )
        )

        return analyses

    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()


if __name__ == "__main__":
    try:
        scan_id = int(input("Enter scan ID to analyze: "))
        results = get_audit_recommendations(scan_id)

        if not results:
            print("No issues found for this scan.")

        for item in results:
            print("\n" + "=" * 50)
            print(f"Issue #{item['issue_id']}: {item['issue_type']}")
            print(f"Priority: {item['priority_label']}")
            print(f"Status: {item['status']}")
            print(f"Target: {item['target_url']}")
            print(f"HTTP status: {item['http_status']}")
            print(f"Impact: {item['impact']}")
            print(f"Recommended action: {item['recommended_action']}")
            print(f"Example fix: {item['example_fix']}")

    except ValueError:
        print("Please enter a valid numeric scan ID.")
    except Exception as error:
        print("Audit analysis failed:", error)
