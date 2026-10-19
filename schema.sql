-- SiteAudit MySQL schema.
--
-- The application creates missing tables and adds missing columns
-- automatically on first use (see database.ensure_schema), and never alters
-- or drops existing columns. This file documents the expected schema and can
-- be used to create the database from scratch:
--
--   mysql -u root -p -e "CREATE DATABASE IF NOT EXISTS siteaudit_db"
--   mysql -u root -p siteaudit_db < schema.sql
--
-- NOTE: the original tables were created before this file existed, so the
-- column types for the original columns below are inferred from the
-- application's queries.

CREATE TABLE IF NOT EXISTS scans (
    scan_id          INT AUTO_INCREMENT PRIMARY KEY,
    start_url        VARCHAR(2048) NOT NULL,
    page_limit       INT NOT NULL,
    pages_inspected  INT NOT NULL DEFAULT 0,
    issues_detected  INT NOT NULL DEFAULT 0,
    scan_time        DATETIME NOT NULL,
    -- added by this version:
    status           VARCHAR(20) NOT NULL DEFAULT 'completed',  -- running | completed | failed
    crawl_depth      INT NULL,                                  -- NULL = unlimited
    links_found      INT NOT NULL DEFAULT 0,
    links_checked    INT NOT NULL DEFAULT 0,
    links_skipped    INT NOT NULL DEFAULT 0,
    progress_phase   VARCHAR(30) NULL,                          -- crawling | checking_links | saving | done | failed
    progress_current INT NOT NULL DEFAULT 0,
    progress_total   INT NOT NULL DEFAULT 0,
    notes            TEXT NULL,
    finished_at      DATETIME NULL
);

CREATE TABLE IF NOT EXISTS pages (
    page_id        INT AUTO_INCREMENT PRIMARY KEY,
    scan_id        INT NOT NULL,
    url            VARCHAR(2048) NOT NULL,
    http_status    INT NULL,
    final_url      VARCHAR(2048) NULL,
    redirect_count INT NOT NULL DEFAULT 0,
    title          VARCHAR(255) NULL,
    error          TEXT NULL,
    depth          INT NULL,                                    -- added by this version
    INDEX idx_pages_scan (scan_id),
    FOREIGN KEY (scan_id) REFERENCES scans(scan_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS issues (
    issue_id    INT AUTO_INCREMENT PRIMARY KEY,
    scan_id     INT NOT NULL,
    source_url  VARCHAR(2048) NULL,
    target_url  VARCHAR(2048) NULL,
    issue_type  VARCHAR(100) NOT NULL,
    http_status INT NULL,
    severity    VARCHAR(20) NOT NULL,                           -- Critical | Medium | Low (legacy rows: High)
    status      VARCHAR(20) NOT NULL DEFAULT 'Open',            -- Open | Resolved
    category    VARCHAR(30) NULL,                               -- added by this version
    detail      TEXT NULL,                                      -- added by this version
    INDEX idx_issues_scan (scan_id),
    FOREIGN KEY (scan_id) REFERENCES scans(scan_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS rechecks (
    recheck_id     INT AUTO_INCREMENT PRIMARY KEY,
    issue_id       INT NOT NULL,
    http_status    INT NULL,
    final_url      VARCHAR(2048) NULL,
    redirect_count INT NOT NULL DEFAULT 0,
    issue_status   VARCHAR(20) NOT NULL,
    checked_at     DATETIME NOT NULL,                           -- stored as UTC
    error          TEXT NULL,
    outcome        TEXT NULL,                                   -- added by this version
    INDEX idx_rechecks_issue (issue_id),
    FOREIGN KEY (issue_id) REFERENCES issues(issue_id) ON DELETE CASCADE
);
