import csv
import io
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from audit_agent import analyze_issue, get_audit_recommendations
from classification import (
    effective_issue,
    normalize_severity,
    priority_key,
    summarize,
)
from crawler import normalize_url
from database import ensure_schema, get_connection
from recheck import RecheckSaveError, recheck_issue, save_recheck
from scan_service import (
    ScanBusyError,
    find_running_scan,
    mark_interrupted_scans,
    run_scan_blocking,
    start_background_scan,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run a single uvicorn worker: background scans live in this process.
    try:
        mark_interrupted_scans()
    except Exception as error:  # the DB may simply not be up yet
        print("Startup database check failed:", error)
    yield


app = FastAPI(
    title="SiteAudit API",
    description="Backend API for the Broken Link and SEO Crawler",
    version="2.0.0",
    lifespan=lifespan,
)

ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "SITEAUDIT_CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:4173,http://127.0.0.1:4173",
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ScanRequest(BaseModel):
    start_url: str
    page_limit: int = Field(default=200, ge=1, le=200)
    # Link hops allowed from the start page (0 = start page only).
    # Omit / null for unlimited depth.
    crawl_depth: int | None = Field(default=None, ge=0, le=25)
    # False (default): start the scan in the background and poll
    # /api/scans/{id}/progress. True: block until the scan finishes
    # (the original behaviour).
    wait: bool = False


def validate_url(url: str) -> str:
    url = url.strip()

    if url and "://" not in url:
        url = "https://" + url

    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(
            status_code=400,
            detail="Enter a valid website URL starting with http:// or https://",
        )

    return normalize_url(url)


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def _run(query: str, params: tuple, many: bool):
    connection = None
    cursor = None

    try:
        ensure_schema()
        connection = get_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(query, params)
        return cursor.fetchall() if many else cursor.fetchone()
    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()


def fetch_all(query: str, params: tuple = ()):
    return _run(query, params, many=True)


def fetch_one(query: str, params: tuple = ()):
    return _run(query, params, many=False)


def require_scan(scan_id: int):
    scan = fetch_one("SELECT * FROM scans WHERE scan_id = %s", (scan_id,))
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")
    return scan


ISSUE_COLUMNS = """
    issue_id, scan_id, source_url, target_url, issue_type,
    http_status, severity, status, category, detail
"""


def load_issues(scan_id: int):
    """All issues of a scan as *effective* issues, in priority order."""
    rows = fetch_all(
        f"SELECT {ISSUE_COLUMNS} FROM issues WHERE scan_id = %s", (scan_id,)
    )
    issues = [effective_issue(row) for row in rows]
    issues.sort(key=priority_key)
    return issues


def host_of(url: str) -> str:
    return urlparse(url or "").hostname or (url or "")


def public_issue(issue: dict) -> dict:
    return {
        "issue_id": issue["issue_id"],
        "scan_id": issue["scan_id"],
        "source_url": issue["source_url"],
        "target_url": issue["target_url"],
        "issue_type": issue["issue_type"],
        "http_status": issue["http_status"],
        "severity": issue["severity"],
        "severity_label": issue["severity_label"],
        "category": issue["category"],
        "category_label": issue["category_label"],
        "detail": issue.get("detail") or "",
        "status": issue["status"],
        "priority": issue["priority"],
        "penalty": issue["penalty"],
    }


def with_metrics(scan: dict, issues: list) -> dict:
    """Scan row plus the numbers the dashboard shows, from one source."""
    metrics = summarize(
        issues, scan.get("pages_inspected") or 0, scan.get("links_checked") or 0
    )
    return {
        **scan,
        "status": scan.get("status") or "completed",
        "domain": host_of(scan["start_url"]),
        "metrics": metrics,
        "health_score": metrics["health_score"],
    }


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health():
    try:
        fetch_all("SELECT 1 AS connected")
        return {"status": "ok", "database": "connected"}
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail=f"Database connection failed: {error}",
        )


# ---------------------------------------------------------------------------
# Scans
# ---------------------------------------------------------------------------
SCAN_LIST_COLUMNS = """
    scan_id, start_url, page_limit, crawl_depth, pages_inspected,
    issues_detected, scan_time, status, links_found, links_checked,
    links_skipped, progress_phase, progress_current, progress_total,
    notes, finished_at
"""


@app.get("/api/scans")
def list_scans():
    scans = fetch_all(
        f"SELECT {SCAN_LIST_COLUMNS} FROM scans ORDER BY scan_id DESC"
    )
    issue_rows = fetch_all(
        f"SELECT {ISSUE_COLUMNS} FROM issues"
    )

    issues_by_scan: dict = {}
    for row in issue_rows:
        issues_by_scan.setdefault(row["scan_id"], []).append(effective_issue(row))

    enriched = [with_metrics(scan, issues_by_scan.get(scan["scan_id"], [])) for scan in scans]

    # Score change versus the previous finished scan of the same site.
    previous_score: dict = {}
    for scan in sorted(enriched, key=lambda s: s["scan_id"]):
        scan["score_change"] = None
        if scan["status"] != "completed":
            continue
        site = urlparse(scan["start_url"]).netloc.lower().removeprefix("www.")
        if site in previous_score:
            scan["score_change"] = round(
                scan["health_score"] - previous_score[site], 1
            )
        previous_score[site] = scan["health_score"]

    return enriched


@app.post("/api/scans")
def create_scan(request: ScanRequest):
    start_url = validate_url(request.start_url)

    try:
        if request.wait:
            scan_id, outcome = run_scan_blocking(
                start_url, request.page_limit, request.crawl_depth
            )
            if outcome["status"] != "completed":
                raise HTTPException(
                    status_code=502,
                    detail=f"Scan failed: {outcome.get('error', 'unknown error')}",
                )
            return {
                "message": "Scan completed",
                "scan_id": scan_id,
                "status": "completed",
                "pages_inspected": outcome["pages_inspected"],
                "issues_detected": outcome["issues_detected"],
            }

        existing = find_running_scan(start_url)
        if existing:
            return {
                "message": "A scan of this website is already running",
                "scan_id": existing,
                "status": "running",
                "already_running": True,
                "pages_inspected": 0,
                "issues_detected": 0,
            }

        scan_id = start_background_scan(
            start_url, request.page_limit, request.crawl_depth
        )
        return {
            "message": "Scan started",
            "scan_id": scan_id,
            "status": "running",
            "pages_inspected": 0,
            "issues_detected": 0,
        }

    except ScanBusyError as error:
        raise HTTPException(status_code=429, detail=str(error))
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Scan failed: {error}",
        )


@app.get("/api/scans/{scan_id}")
def get_scan(scan_id: int):
    scan = require_scan(scan_id)
    return with_metrics(scan, load_issues(scan_id))


@app.get("/api/scans/{scan_id}/progress")
def get_progress(scan_id: int):
    scan = require_scan(scan_id)
    status = scan.get("status") or "completed"
    phase = scan.get("progress_phase") or ("done" if status == "completed" else status)
    current = scan.get("progress_current") or 0
    total = scan.get("progress_total") or 0

    if status == "completed":
        percent = 100
    elif phase in ("crawling", "checking_links") and total:
        percent = min(100, round(current * 100 / total))
    else:
        percent = 0

    return {
        "scan_id": scan_id,
        "status": status,
        "phase": phase,
        "current": current,
        "total": total,
        # While crawling, the total is the pages discovered so far (capped at
        # the page limit) and can still grow.
        "total_is_estimate": phase == "crawling" and status == "running",
        "percent": percent,
        "pages_crawled": scan.get("pages_inspected") or 0,
        "links_checked": scan.get("links_checked") or 0,
        "issues_detected": scan.get("issues_detected") or 0,
        "notes": scan.get("notes"),
    }



@app.get("/api/scans/{scan_id}/pages")
def get_pages(scan_id: int):
    require_scan(scan_id)

    return fetch_all(
        """
        SELECT page_id, scan_id, url, http_status,
               final_url, redirect_count, title, error, depth,
               priority, priority_reason,processed_priority,
               processed_order
        FROM pages
        WHERE scan_id = %s
        ORDER BY page_id
        """,
        (scan_id,),
    )



@app.get("/api/scans/{scan_id}/issues")
def get_issues(
    scan_id: int,
    status: str | None = Query(default=None),
    issue_type: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    category: str | None = Query(default=None),
):
    require_scan(scan_id)
    issues = load_issues(scan_id)

    if status:
        issues = [i for i in issues if str(i["status"]).lower() == status.lower()]
    if issue_type:
        issues = [i for i in issues if i["issue_type"] == issue_type]
    if severity:
        wanted = normalize_severity(severity)
        issues = [i for i in issues if i["severity"] == wanted]
    if category:
        issues = [i for i in issues if i["category"] == category]

    return [public_issue(i) for i in issues]


@app.get("/api/scans/{scan_id}/summary")
def get_summary(scan_id: int):
    scan = require_scan(scan_id)
    issues = load_issues(scan_id)

    breakdown: dict = {}
    for issue in issues:
        key = (issue["issue_type"], issue["status"])
        breakdown[key] = breakdown.get(key, 0) + 1

    enriched = with_metrics(scan, issues)

    return {
        "scan": enriched,
        "metrics": enriched["metrics"],
        "issue_breakdown": [
            {"issue_type": issue_type, "status": status, "total": total}
            for (issue_type, status), total in sorted(breakdown.items())
        ],
    }


@app.get("/api/scans/{scan_id}/recommendations")
def get_recommendations(scan_id: int):
    require_scan(scan_id)

    try:
        return get_audit_recommendations(scan_id)
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Could not generate recommendations: {error}",
        )


def _csv_cell(value):
    text = "" if value is None else str(value)
    # Stop spreadsheet apps from running a cell as a formula.
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


@app.get("/api/scans/{scan_id}/export")
def export_scan(scan_id: int, export_format: str = Query(default="csv", alias="format")):
    export_format = export_format.lower()
    if export_format not in ("csv", "json"):
        raise HTTPException(status_code=400, detail="format must be csv or json")

    scan = require_scan(scan_id)
    issues = load_issues(scan_id)
    filename = f"siteaudit-scan-{scan_id}.{export_format}"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}

    if export_format == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([
            "Issue ID", "Severity", "Category", "Issue", "HTTP Status",
            "Source URL", "Target URL", "Detail", "State",
        ])
        for issue in issues:
            writer.writerow([_csv_cell(v) for v in (
                f"ISS-{issue['issue_id']:03d}",
                issue["severity_label"],
                issue["category_label"],
                issue["issue_type"],
                issue["http_status"],
                issue["source_url"],
                issue["target_url"],
                issue.get("detail") or "",
                issue["status"],
            )])
        return Response(
            content=("\ufeff" + buffer.getvalue()).encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers=headers,
        )

    pages = fetch_all(
        """
        SELECT page_id, url, http_status, final_url, redirect_count, title, error, depth
        FROM pages WHERE scan_id = %s ORDER BY page_id
        """,
        (scan_id,),
    )
    payload = {
        "project": "SiteAudit",
        "scan": with_metrics(scan, issues),
        "issues": [public_issue(i) for i in issues],
        "pages": pages,
    }
    return JSONResponse(content=jsonable_encoder(payload), headers=headers)


# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------
def _get_issue_row(issue_id: int):
    issue = fetch_one(
        f"SELECT {ISSUE_COLUMNS} FROM issues WHERE issue_id = %s", (issue_id,)
    )
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    return issue


def _utc_iso(value):
    """rechecks.checked_at is stored as naive UTC."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    return value


def _load_rechecks(issue_id: int):
    rows = fetch_all(
        """
        SELECT recheck_id, issue_id, http_status,
               final_url, redirect_count, issue_status,
               checked_at, error, outcome
        FROM rechecks
        WHERE issue_id = %s
        ORDER BY checked_at DESC, recheck_id DESC
        """,
        (issue_id,),
    )
    for row in rows:
        row["checked_at"] = _utc_iso(row["checked_at"])
    return rows


@app.get("/api/issues/{issue_id}")
def get_issue(issue_id: int):
    """One issue with its agent explanation, cause, fix and recheck history."""
    row = _get_issue_row(issue_id)
    effective = effective_issue(row)

    return {
        **public_issue(effective),
        "agent": analyze_issue(row),
        "rechecks": _load_rechecks(issue_id),
    }


@app.post("/api/issues/{issue_id}/recheck")
def recheck(issue_id: int):
    issue = _get_issue_row(issue_id)

    result = recheck_issue(
        issue["target_url"],
        issue["issue_type"],
        issue["source_url"],
    )

    try:
        save_recheck(issue_id, result)
    except RecheckSaveError as error:
        raise HTTPException(status_code=409, detail=str(error))
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Recheck finished but could not be saved: {error}",
        )

    return {
        "issue_id": issue_id,
        **result,
        "status": "Resolved" if result["issue_status"] == "Resolved" else "Open",
    }


@app.get("/api/issues/{issue_id}/rechecks")
def get_rechecks(issue_id: int):
    _get_issue_row(issue_id)
    return _load_rechecks(issue_id)
