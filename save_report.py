
import json
from datetime import datetime
from pathlib import Path
from database import get_connection

REPORT_PATH = Path(__file__).resolve().parent / "crawl_report.json"


def save_report():
    if not REPORT_PATH.exists():
        print("Error: crawl_report.json was not found.")
        return

    with REPORT_PATH.open("r", encoding="utf-8") as file:
        report = json.load(file)

    connection = None
    cursor = None

    try:
        connection = get_connection()
        cursor = connection.cursor()

        # 1. Save scan summary
        scan_time = datetime.fromisoformat(
            report["scan_time"].replace("Z", "+00:00")
        ).replace(tzinfo=None)

        cursor.execute(
            """
            INSERT INTO scans
            (start_url, page_limit, pages_inspected,
             issues_detected, scan_time)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                report["start_url"],
                report["page_limit"],
                report["pages_inspected"],
                report["issues_detected"],
                scan_time
            )
        )

        scan_id = cursor.lastrowid

        # 2. Save crawled pages
        for page in report.get("pages", []):
            cursor.execute(
                """
                INSERT INTO pages
                (scan_id, url, http_status, final_url,
                 redirect_count, title, error)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    scan_id,
                    page.get("url"),
                    page.get("status"),
                    page.get("final_url"),
                    page.get("redirects", 0),
                    page.get("title", ""),
                    page.get("error", "")
                )
            )

        # 3. Save detected issues
        for issue in report.get("issues", []):
            cursor.execute(
                """
                INSERT INTO issues
                (scan_id, source_url, target_url, issue_type,
                 http_status, severity, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    scan_id,
                    issue.get("source_url"),
                    issue.get("target_url"),
                    issue.get("issue_type"),
                    issue.get("http_status"),
                    issue.get("severity"),
                    "Open"
                )
            )

        # Save all inserts together
        connection.commit()

        print("\nReport saved to MySQL successfully!")
        print(f"Scan ID: {scan_id}")
        print(f"Pages saved: {len(report.get('pages', []))}")
        print(f"Issues saved: {len(report.get('issues', []))}")

    except Exception as error:
        if connection and connection.is_connected():
            connection.rollback()
        print("Failed to save report:", error)

    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()


if __name__ == "__main__":
    save_report()
