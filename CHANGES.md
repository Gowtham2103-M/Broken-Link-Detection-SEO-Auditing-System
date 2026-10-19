# SiteAudit: changes in this version

## Problems found in the original project

| # | Problem | Where |
|---|---------|-------|
| 1 | **Open did nothing visible.** The selected issue's details rendered *below the whole table*, off-screen, and explanations depended on one all-scan request whose failure was silently ignored. | `App.jsx` |
| 2 | Typo in the agent rules (`"Bn Link"`), so every real *Broken Link* got the generic fallback text and lowest priority. | `audit_agent.py` |
| 3 | Every HTTP >= 400 (including **403/401/429**) was stored as a High "Broken Link". | `crawler.py` |
| 4 | Crawl depth was shown in the UI but never sent or enforced. | `App.jsx`, `crawler.py` |
| 5 | No real progress: `POST /api/scans` blocked until done and the UI showed an animated bar with no data. | `main.py`, `App.jsx` |
| 6 | Only the first 50 unique links were ever checked, silently; "links checked" was never recorded. | `crawler.py` |
| 7 | Health score was computed in the browser with penalties 10/4/1 (spec: 10/3/1), per scan with N+1 requests; history "broken/warnings" came from regexes over issue names. | `App.jsx` |
| 8 | `save_recheck` swallowed every error (API still answered 200), ignored the source URL, and recorded no explanation of the outcome. | `recheck.py`, `main.py` |
| 9 | Redirect handling: bare domain -> `www`/https start redirects were treated as external, pages reached through redirects were crawled twice, and queue membership was O(n). | `crawler.py` |
| 10 | Scan timestamps mixed UTC (CLI) and local time (API). | `crawler.py`, `main.py` |
| 11 | `st.set_page_config` was called after other Streamlit calls (Streamlit raises). | `dashboard.py` |
| 12 | DB credentials hard-coded; start URLs without a scheme were rejected. | `database.py`, `main.py` |

## What changed

**Backend**
- `classification.py` (new): one place for severity penalties (Critical 10 / Medium 3 / Low 1), categories, status classification, the health-score formula and the dashboard summary. The crawler, API, recheck and agent all use it.
- `crawler.py`: breadth-first crawl with a real **depth limit** and **max-page limit**; duplicate/redirect de-duplication; concurrent link checking (first 1000 unique links, internal first, skipped count is recorded and shown); retries for timeouts and 502/503/504; progress callbacks; the command-line mode and optional JSON export are kept.
- `analysis.py` (new): on-page checks from the spec: missing/long title, meta description, H1, image ALT, noindex, canonical, URL format, non-HTTPS outbound links.
- **Status handling:** 404/410 and DNS/connection failures = *Broken Link*; 5xx = *Server Error*; 401/403/407/429 = *Access Restricted* (Info, **not** broken); timeouts/odd codes = *needs verification*; redirects = *Redirect* (Info) or *Long Redirect Chain* (Warning, 3+ hops); SSL failures = Critical.
- `scan_service.py` (new) and `main.py`: `POST /api/scans` now starts a background scan and returns immediately; `GET /api/scans/{id}/progress` returns the **real** counters the crawler wrote to MySQL (pages crawled, links checked, phase). Pass `"wait": true` for the old blocking behaviour. Scans left "running" by a server restart are marked failed.
- `recheck.py`: fresh, uncached request every time; resolves only when the finding really disappeared; stores a human-readable outcome; save errors are reported.
- `audit_agent.py`: explanation, cause, impact, recommended fix and example for every issue type (rule-based, not an LLM).
- `main.py`: new `GET /api/issues/{id}` (issue + explanation + recheck history), `GET /api/scans/{id}/export?format=csv|json`, enriched `GET /api/scans` (health score, score change vs previous scan of the same site, severity counts), metrics in `/summary`.
- **Historical data:** rows saved by the old crawler are *re-classified at read time* from their stored HTTP status (a stored 403 "High Broken Link" now shows as Access Restricted / Info). Nothing in the database is rewritten.

**Frontend** (React + Vite, same dependencies)
- Light, minimal, Public Sans; slim top nav (Scan, History); responsive desktop / tablet / mobile (table rows become cards on phones).
- Scan form with URL, crawl depth, max pages and Run Scan; real progress panel.
- Metric cards, health-score donut and severity breakdown, all from backend numbers.
- Issue table: severity tabs with counts, search, Open/Resolved/All filter, priority order, HTTP status, source URL, pagination. **Open expands the details directly under the row** with agent explanation, cause, fix and recheck history; Recheck runs a fresh request.
- History cards: domain, health score and change, pages crawled, critical / warnings / info, View Report, Re-scan Now.
- Exports: CSV (formula-injection safe) and JSON from the server.

## Health score

`max(0, min(100, 100 - total_penalty / max(pages_crawled, 10)))`, rounded to one decimal. Only issues that are still **Open** count, so a rechecked-and-fixed issue stops lowering the score.

Display labels: Critical = stored `Critical` (10 pts), Warning = `Medium` (3 pts), Info = `Low` (1 pt).

## Run it

Backend (Python 3.10+):
```
cd siteaudit_crawler
pip install -r requirements.txt
uvicorn main:app --reload --port 8000        # keep a single worker; scans run in-process
```
Frontend (Node 18+):
```
cd siteaudit_crawler/frontend
npm install
npm run dev                                  # http://localhost:5173
```
Optional demo site for testing: `python test_server.py` (http://127.0.0.1:8000 is also the API port, so change one of them, e.g. run the API on 8001 and set `VITE_API_BASE_URL=http://127.0.0.1:8001/api`).

Tests (no database or network required):
```
python -m unittest discover -s tests -t .
```

## Database and configuration

- MySQL with a database named `siteaudit_db`. Defaults are the original ones (`localhost`, `root`/`root`); override with `SITEAUDIT_DB_HOST`, `_PORT`, `_USER`, `_PASSWORD`, `_NAME` (see `.env.example`). **Do not keep root/root outside local development.**
- On first use the API creates missing tables and **adds** the new columns (`scans.status`, `crawl_depth`, `links_*`, `progress_*`, `notes`, `finished_at`; `pages.depth`; `issues.category`, `issues.detail`; `rechecks.outcome`). Existing columns and data are untouched. `schema.sql` documents the full schema.
- The MySQL user needs `CREATE`, `ALTER`, `SELECT`, `INSERT`, `UPDATE`.
- The legacy Streamlit `dashboard.py` still works with the same tables.
- `scan_time` is stored in the server's local time (matching how the API always stored it).
