"""End-to-end logic tests: routes -> scan service -> crawler -> database -> recheck.

Runs the real route functions against a local website and a SQLite stand-in
for MySQL (see tests/support.py).
"""
import time
import unittest
import json

from sklearn import metrics

from tests import support

DB_PATH = support.install()

import main  # noqa: E402
from tests.local_site import Site, page  # noqa: E402


def wait_for(scan_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        progress = main.get_progress(scan_id)
        if progress["status"] != "running":
            return progress
        time.sleep(0.1)
    raise AssertionError("scan did not finish")


def issues_of(scan_id, **filters):
    args = dict(status=None, issue_type=None, severity=None, category=None)
    args.update(filters)
    return main.get_issues(scan_id, **args)


class ApiFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.site = Site().start().standard_routes()
        import crawler
        crawler.CRAWL_DELAY = 0
        cls.scan_id = main.create_scan(
            main.ScanRequest(start_url=cls.site.base + "/", page_limit=50, crawl_depth=None)
        )["scan_id"]
        cls.final = wait_for(cls.scan_id)

    @classmethod
    def tearDownClass(cls):
        cls.site.stop()

    def test_scan_completed_with_real_counters(self):
        self.assertEqual(self.final["status"], "completed")
        self.assertEqual(self.final["percent"], 100)
        pages = main.get_pages(self.scan_id)
        self.assertEqual(self.final["pages_crawled"], len(pages))
        self.assertGreater(self.final["links_checked"], 0)

    def test_summary_issue_list_and_scan_list_agree(self):
        summary = main.get_summary(self.scan_id)
        issues = issues_of(self.scan_id)
        metrics = summary["metrics"]

        self.assertEqual(metrics["open_issues"], len(issues))
        self.assertEqual(sum(metrics["severity_counts"].values()), len(issues))
        self.assertEqual(metrics["pages_crawled"], len(main.get_pages(self.scan_id)))

        listed = next(s for s in main.list_scans() if s["scan_id"] == self.scan_id)
        self.assertEqual(listed["health_score"], metrics["health_score"])
        self.assertEqual(listed["metrics"], metrics)
        self.assertEqual(main.get_scan(self.scan_id)["health_score"], metrics["health_score"])

        penalty = sum({"Critical": 8, "Medium": 3, "Low": 0}[i["severity"]] for i in issues)
        expected = round(max(0, min(100, 100 - penalty)), 1)
        self.assertEqual(metrics["health_score"], expected)
    def test_priority_order_and_filters(self):
        issues = issues_of(self.scan_id)
        ranks = [{"Critical": 0, "Medium": 1, "Low": 2}[i["severity"]] for i in issues]
        self.assertEqual(ranks, sorted(ranks))

        critical = issues_of(self.scan_id, severity="critical")
        self.assertTrue(critical and all(i["severity"] == "Critical" for i in critical))
        broken = issues_of(self.scan_id, category="broken_link")
        self.assertEqual([i["http_status"] for i in broken], [404])
        restricted = issues_of(self.scan_id, category="access_restricted")
        self.assertEqual([i["http_status"] for i in restricted], [403])

    def test_issue_detail_has_explanation_cause_and_fix(self):
        broken = issues_of(self.scan_id, category="broken_link")[0]
        detail = main.get_issue(broken["issue_id"])
        agent = detail["agent"]
        for key in ("explanation", "cause", "recommended_action", "example_fix", "impact"):
            self.assertTrue(agent[key], key)
        self.assertEqual(detail["rechecks"], [])

        restricted = main.get_issue(issues_of(self.scan_id, category="access_restricted")[0]["issue_id"])
        self.assertIn("not confirmed", restricted["agent"]["explanation"])

    def test_csv_and_json_export_match_issue_list(self):
        issues = issues_of(self.scan_id)
        csv_response = main.export_scan(self.scan_id, export_format="csv")
        lines = csv_response.body.decode("utf-8-sig").strip().splitlines()
        self.assertEqual(len(lines), len(issues) + 1)
        self.assertIn("attachment", csv_response.headers["Content-Disposition"])

        json_response = main.export_scan(self.scan_id, export_format="json")
        json_payload = json.loads(json_response.body)
        self.assertEqual(len(json_payload["issues"]), len(issues))
        with self.assertRaises(main.HTTPException):
            main.export_scan(self.scan_id, export_format="xml")

    def test_unknown_ids_return_404(self):
        for call in (lambda: main.get_scan(99999), lambda: main.get_issue(99999),
                     lambda: main.recheck(99999), lambda: main.get_progress(99999)):
            with self.assertRaises(main.HTTPException) as ctx:
                call()
            self.assertEqual(ctx.exception.status_code, 404)

    def test_invalid_url_rejected(self):
        with self.assertRaises(main.HTTPException) as ctx:
            main.create_scan(main.ScanRequest(start_url="ftp://x", page_limit=5))
        self.assertEqual(ctx.exception.status_code, 400)


class RecheckFlowTests(unittest.TestCase):
    def setUp(self):
        self.site = Site().start().standard_routes()
        import crawler
        crawler.CRAWL_DELAY = 0
        result = main.create_scan(main.ScanRequest(
            start_url=self.site.base + "/", page_limit=50, crawl_depth=1, wait=True))
        self.scan_id = result["scan_id"]

    def tearDown(self):
        self.site.stop()

    def find(self, **filters):
        return issues_of(self.scan_id, **filters)[0]

    def test_wait_mode_and_depth_are_persisted(self):
        scan = main.get_scan(self.scan_id)
        self.assertEqual(scan["status"], "completed")
        self.assertEqual(scan["crawl_depth"], 1)
        paths = {p["url"].replace(self.site.base, "") for p in main.get_pages(self.scan_id)}
        self.assertNotIn("/depth2", paths)

    def test_recheck_uses_a_fresh_request_and_stays_open_while_broken(self):
        issue = self.find(category="broken_link")
        before = len([h for h in self.site.hits if h[0] == "/missing"])
        result = main.recheck(issue["issue_id"])
        after = [h for h in self.site.hits if h[0] == "/missing"]
        self.assertEqual(len(after), before + 1)
        self.assertIn("no-cache", after[-1][1].get("Cache-Control", ""))
        self.assertEqual(result["status"], "Open")
        self.assertEqual(result["http_status"], 404)
        self.assertEqual(issues_of(self.scan_id, category="broken_link")[0]["status"], "Open")

    def test_recheck_resolves_after_the_fix_and_updates_counts(self):
        issue = self.find(category="broken_link")
        score_before = main.get_summary(self.scan_id)["metrics"]["health_score"]

        self.site.routes["/missing"] = (200, {}, page("Fixed", "<h1>ok</h1>"))
        result = main.recheck(issue["issue_id"])

        self.assertEqual(result["status"], "Resolved")
        detail = main.get_issue(issue["issue_id"])
        self.assertEqual(detail["status"], "Resolved")
        self.assertEqual(len(detail["rechecks"]), 1)
        self.assertEqual(detail["rechecks"][0]["issue_status"], "Resolved")
        self.assertTrue(detail["rechecks"][0]["outcome"])

        metrics = main.get_summary(self.scan_id)["metrics"]
        self.assertEqual(metrics["broken_links"], 0)
        self.assertGreaterEqual(metrics["health_score"], score_before)
        open_only = issues_of(self.scan_id, status="Open")
        self.assertEqual(metrics["open_issues"], len(open_only))

    def test_recheck_403_stays_open_and_is_not_called_fixed(self):
        issue = self.find(category="access_restricted")
        result = main.recheck(issue["issue_id"])
        self.assertEqual(result["status"], "Open")
        self.assertEqual(result["http_status"], 403)

    def test_recheck_seo_issue(self):
        issue = self.find(issue_type="Missing Page Title")
        self.assertEqual(main.recheck(issue["issue_id"])["status"], "Open")
        self.site.routes["/no-title"] = (200, {}, page("Now has a title", "<h1>x</h1>"))
        self.assertEqual(main.recheck(issue["issue_id"])["status"], "Resolved")

    def test_recheck_unreachable_target_stays_open_with_reason(self):
        issue = self.find(category="broken_link")
        self.site.stop()
        result = main.recheck(issue["issue_id"])
        self.assertEqual(result["status"], "Open")
        self.assertTrue(result["error"])
        self.site = Site().start().standard_routes()  # so tearDown can stop it


class FailureAndLegacyTests(unittest.TestCase):
    def test_unreachable_site_marks_scan_failed_with_reason(self):
        scan_id = main.create_scan(main.ScanRequest(
            start_url="http://127.0.0.1:1/", page_limit=5))["scan_id"]
        progress = wait_for(scan_id)
        self.assertEqual(progress["status"], "failed")
        self.assertIn("Could not reach", progress["notes"])
        self.assertEqual(main.get_pages(scan_id), [])

    def test_legacy_rows_are_reclassified_in_history(self):
        import sqlite3
        raw = sqlite3.connect(DB_PATH)
        raw.execute("INSERT INTO scans (start_url, page_limit, pages_inspected, issues_detected, scan_time)"
                    " VALUES ('http://old.test/', 50, 4, 3, '2025-01-01 10:00:00')")
        scan_id = raw.execute("SELECT MAX(scan_id) FROM scans").fetchone()[0]
        for status, severity in ((404, "High"), (403, "High"), (500, "High")):
            raw.execute("INSERT INTO issues (scan_id, source_url, target_url, issue_type, http_status, severity, status)"
                        " VALUES (?, 'http://old.test/', ?, 'Broken Link', ?, ?, 'Open')",
                        (scan_id, f"http://old.test/{status}", status, severity))
        raw.commit(); raw.close()

        by_status = {i["http_status"]: i for i in issues_of(scan_id)}
        self.assertEqual(by_status[404]["issue_type"], "Broken Link")
        self.assertEqual(by_status[403]["issue_type"], "Access Restricted")
        self.assertEqual(by_status[403]["severity"], "Low")
        self.assertEqual(by_status[500]["category"], "server_error")
        metrics = main.get_summary(scan_id)["metrics"]
        self.assertEqual(metrics["broken_links"], 1)
        self.assertEqual(metrics["penalty_points"], 8 + 0 + 8)

    def test_score_change_compares_with_previous_scan_of_same_site(self):
        site = Site().start().standard_routes()
        try:
            first = main.create_scan(main.ScanRequest(start_url=site.base + "/", page_limit=20, wait=True))["scan_id"]
            site.routes["/missing"] = (200, {}, page("Fixed", "<h1>ok</h1>"))
            second = main.create_scan(main.ScanRequest(start_url=site.base + "/", page_limit=20, wait=True))["scan_id"]
            scans = {s["scan_id"]: s for s in main.list_scans()}
            self.assertIsNone(scans[first]["score_change"])
            self.assertEqual(scans[second]["score_change"],
                             round(scans[second]["health_score"] - scans[first]["health_score"], 1))
            self.assertGreater(scans[second]["score_change"], 0)
        finally:
            site.stop()


if __name__ == "__main__":
    unittest.main()
