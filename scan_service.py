"""Runs scans in background threads and records real progress in MySQL.

The scan row is created up front with status ``running``. While the crawler
works, its progress callback writes the real counters (pages crawled, links
checked) to that row, and ``GET /api/scans/{id}/progress`` simply reads them.
When the crawl ends the results are saved in one transaction and the row
becomes ``completed`` (or ``failed`` with a reason).
"""
import threading
import time
from datetime import datetime

from crawler import crawl_website_detailed, save_to_mysql
from database import ensure_schema, get_connection

MAX_CONCURRENT_SCANS = 3
PROGRESS_INTERVAL = 0.4  # seconds between progress writes

_lock = threading.Lock()
_active = {}  # scan_id -> Thread


class ScanBusyError(Exception):
    """Too many scans are already running."""


def _execute(query, params=()):
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(query, params)
        connection.commit()
        return cursor.lastrowid
    finally:
        cursor.close()
        if connection.is_connected():
            connection.close()


def find_running_scan(start_url):
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT scan_id FROM scans WHERE start_url = %s AND status = 'running' LIMIT 1",
            (start_url,),
        )
        row = cursor.fetchone()
        return row[0] if row else None
    finally:
        cursor.close()
        if connection.is_connected():
            connection.close()


def create_scan_record(start_url, page_limit, crawl_depth):
    ensure_schema()
    return _execute(
        """
        INSERT INTO scans
        (start_url, page_limit, crawl_depth, pages_inspected, issues_detected,
         scan_time, status, progress_phase)
        VALUES (%s, %s, %s, 0, 0, %s, 'running', 'starting')
        """,
        (start_url, page_limit, crawl_depth, datetime.now()),
    )


def mark_failed(scan_id, message):
    _execute(
        """
        UPDATE scans
        SET status = 'failed', progress_phase = 'failed',
            notes = %s, finished_at = %s
        WHERE scan_id = %s
        """,
        (str(message)[:1000], datetime.now(), scan_id),
    )


def mark_interrupted_scans():
    """Scans left 'running' by a previous server process can never finish."""
    ensure_schema()
    _execute(
        """
        UPDATE scans
        SET status = 'failed', progress_phase = 'failed',
            notes = 'Interrupted: the server stopped while this scan was running.',
            finished_at = %s
        WHERE status = 'running'
        """,
        (datetime.now(),),
    )


class _ProgressWriter:
    def __init__(self, scan_id):
        self.scan_id = scan_id
        self.connection = get_connection()
        self.connection.autocommit = True
        self.cursor = self.connection.cursor()
        self.last_write = 0.0
        self.last_phase = None

    def __call__(self, event):
        now = time.monotonic()
        if event["phase"] == self.last_phase and now - self.last_write < PROGRESS_INTERVAL:
            return
        self.last_write, self.last_phase = now, event["phase"]

        if event["phase"] == "checking_links":
            current, total = event["links_checked"], event["links_total"]
        else:
            current, total = event["pages_crawled"], event["pages_target"]

        try:
            self.cursor.execute(
                """
                UPDATE scans
                SET progress_phase = %s, progress_current = %s,
                    progress_total = %s, pages_inspected = %s,
                    links_checked = %s
                WHERE scan_id = %s
                """,
                (event["phase"], current, total, event["pages_crawled"],
                 event["links_checked"], self.scan_id),
            )
        except Exception:
            pass  # progress is informational; never let it abort the scan

    def close(self):
        try:
            self.cursor.close()
            self.connection.close()
        except Exception:
            pass


def run_scan(scan_id, start_url, page_limit, crawl_depth):
    """Run and save one scan. Returns a summary dict; never raises."""
    writer = None
    try:
        writer = _ProgressWriter(scan_id)
        result = crawl_website_detailed(
            start_url,
            max_pages=page_limit,
            max_depth=crawl_depth,
            progress_callback=writer,
        )
        stats = result["stats"]

        if stats["fatal_error"]:
            message = f"Could not reach {start_url}: {stats['fatal_error']}"
            mark_failed(scan_id, message)
            return {"status": "failed", "error": message}

        _execute(
            "UPDATE scans SET progress_phase = 'saving' WHERE scan_id = %s",
            (scan_id,),
        )
        
        save_to_mysql(
        start_url,
        page_limit,
        result["pages"],
        result["issues"],
        datetime.now(),
        scan_id=scan_id,
        crawl_depth=crawl_depth,
        stats=stats,
        page_priorities=result.get("page_priorities", {}),
        )

        return {
            "status": "completed",
            "pages_inspected": len(result["pages"]),
            "issues_detected": len(result["issues"]),
            "page_priorities": result.get("page_priorities", {}),
        }
    except Exception as error:
        try:
            mark_failed(scan_id, f"Scan failed: {error}")
        except Exception:
            pass
        return {"status": "failed", "error": str(error)}
    finally:
        if writer:
            writer.close()
        with _lock:
            _active.pop(scan_id, None)


def start_background_scan(start_url, page_limit, crawl_depth):
    with _lock:
        running = sum(1 for t in _active.values() if t.is_alive())
        if running >= MAX_CONCURRENT_SCANS:
            raise ScanBusyError(
                f"{running} scans are already running. Wait for one to finish."
            )

    scan_id = create_scan_record(start_url, page_limit, crawl_depth)
    thread = threading.Thread(
        target=run_scan,
        args=(scan_id, start_url, page_limit, crawl_depth),
        name=f"scan-{scan_id}",
        daemon=True,
    )
    with _lock:
        _active[scan_id] = thread
    thread.start()
    return scan_id


def run_scan_blocking(start_url, page_limit, crawl_depth):
    """Legacy behaviour: create, run and finish the scan in the caller's thread."""
    scan_id = create_scan_record(start_url, page_limit, crawl_depth)
    outcome = run_scan(scan_id, start_url, page_limit, crawl_depth)
    return scan_id, outcome
