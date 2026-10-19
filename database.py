import os
import threading

import mysql.connector
from mysql.connector import Error

# Connection settings. The defaults are the values the project already used,
# so an unchanged setup keeps working. Set the SITEAUDIT_DB_* environment
# variables (see .env.example) to use different credentials without editing
# code.
DB_CONFIG = {
    "host": os.getenv("SITEAUDIT_DB_HOST", "localhost"),
    "user": os.getenv("SITEAUDIT_DB_USER", "root"),
    "password": os.getenv("SITEAUDIT_DB_PASSWORD", "root"),
    "database": os.getenv("SITEAUDIT_DB_NAME", "siteaudit_db"),
}
if os.getenv("SITEAUDIT_DB_PORT"):
    DB_CONFIG["port"] = int(os.environ["SITEAUDIT_DB_PORT"])


def get_connection():
    return mysql.connector.connect(**DB_CONFIG)


# ---------------------------------------------------------------------------
# Additive schema migration
# ---------------------------------------------------------------------------
# The original tables (scans, pages, issues, rechecks) are never altered or
# dropped. Missing tables are created, and the columns the new crawler,
# progress reporting and classification need are added if they are absent.
# See schema.sql for the full expected schema.

CREATE_TABLES = [
    """
    CREATE TABLE IF NOT EXISTS scans (
        scan_id INT AUTO_INCREMENT PRIMARY KEY,
        start_url VARCHAR(2048) NOT NULL,
        page_limit INT NOT NULL,
        pages_inspected INT NOT NULL DEFAULT 0,
        issues_detected INT NOT NULL DEFAULT 0,
        scan_time DATETIME NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pages (
        page_id INT AUTO_INCREMENT PRIMARY KEY,
        scan_id INT NOT NULL,
        url VARCHAR(2048) NOT NULL,
        http_status INT NULL,
        final_url VARCHAR(2048) NULL,
        redirect_count INT NOT NULL DEFAULT 0,
        title VARCHAR(255) NULL,
        error TEXT NULL,
        INDEX idx_pages_scan (scan_id),
        FOREIGN KEY (scan_id) REFERENCES scans(scan_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS issues (
        issue_id INT AUTO_INCREMENT PRIMARY KEY,
        scan_id INT NOT NULL,
        source_url VARCHAR(2048) NULL,
        target_url VARCHAR(2048) NULL,
        issue_type VARCHAR(100) NOT NULL,
        http_status INT NULL,
        severity VARCHAR(20) NOT NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'Open',
        INDEX idx_issues_scan (scan_id),
        FOREIGN KEY (scan_id) REFERENCES scans(scan_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rechecks (
        recheck_id INT AUTO_INCREMENT PRIMARY KEY,
        issue_id INT NOT NULL,
        http_status INT NULL,
        final_url VARCHAR(2048) NULL,
        redirect_count INT NOT NULL DEFAULT 0,
        issue_status VARCHAR(20) NOT NULL,
        checked_at DATETIME NOT NULL,
        error TEXT NULL,
        INDEX idx_rechecks_issue (issue_id),
        FOREIGN KEY (issue_id) REFERENCES issues(issue_id) ON DELETE CASCADE
    )
    """,
]

# table -> list of (column, definition)
ADDED_COLUMNS = {
    "scans": [
        ("status", "VARCHAR(20) NOT NULL DEFAULT 'completed'"),
        ("crawl_depth", "INT NULL"),
        ("links_found", "INT NOT NULL DEFAULT 0"),
        ("links_checked", "INT NOT NULL DEFAULT 0"),
        ("links_skipped", "INT NOT NULL DEFAULT 0"),
        ("progress_phase", "VARCHAR(30) NULL"),
        ("progress_current", "INT NOT NULL DEFAULT 0"),
        ("progress_total", "INT NOT NULL DEFAULT 0"),
        ("notes", "TEXT NULL"),
        ("finished_at", "DATETIME NULL"),
    ],
    "pages": [("depth", "INT NULL")],
    "issues": [("category", "VARCHAR(30) NULL"), ("detail", "TEXT NULL")],
    "rechecks": [("outcome", "TEXT NULL")],
}

_schema_lock = threading.Lock()
_schema_ready = False


def ensure_schema(force=False):
    """Create missing tables and add missing columns. Safe to call repeatedly."""
    global _schema_ready

    with _schema_lock:
        if _schema_ready and not force:
            return

        connection = get_connection()
        cursor = connection.cursor()
        try:
            for statement in CREATE_TABLES:
                cursor.execute(statement)

            for table, columns in ADDED_COLUMNS.items():
                cursor.execute(
                    """
                    SELECT COLUMN_NAME FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
                    """,
                    (table,),
                )
                existing = {row[0].lower() for row in cursor.fetchall()}

                for column, definition in columns:
                    if column.lower() not in existing:
                        cursor.execute(
                            f"ALTER TABLE `{table}` ADD COLUMN `{column}` {definition}"
                        )

            connection.commit()
            _schema_ready = True
        finally:
            cursor.close()
            if connection.is_connected():
                connection.close()


if __name__ == "__main__":
    try:
        connection = get_connection()

        if connection.is_connected():
            print("Connected to MySQL successfully!")

            cursor = connection.cursor()
            cursor.execute("SHOW TABLES")

            print("\nTables in", DB_CONFIG["database"] + ":")
            for table in cursor.fetchall():
                print("-", table[0])

            cursor.close()
            connection.close()

    except Error as error:
        print("MySQL connection failed:", error)
