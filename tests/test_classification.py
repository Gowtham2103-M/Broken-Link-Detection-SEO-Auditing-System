import unittest

import audit_agent
from analysis import analyze_html
from classification import (
    ISSUE_TYPES, PENALTIES, classify_link_result, effective_issue,
    health_score, normalize_severity, summarize,
)


class StatusClassification(unittest.TestCase):
    def kind(self, status, **kw):
        result = classify_link_result(status, **kw)
        return result and result["issue_type"]

    def test_status_mapping(self):
        self.assertIsNone(self.kind(200))
        self.assertEqual(self.kind(404), "Broken Link")
        self.assertEqual(self.kind(410), "Broken Link")
        for status in (401, 403, 407, 429):
            self.assertEqual(self.kind(status), "Access Restricted", status)
        self.assertEqual(self.kind(408), "Link Timeout")
        for status in (500, 502, 503):
            self.assertEqual(self.kind(status), "Server Error", status)
        self.assertEqual(self.kind(400), "Unverified Link")
        self.assertEqual(self.kind(200, redirects=1), "Redirect")
        self.assertEqual(self.kind(200, redirects=3), "Long Redirect Chain")

    def test_network_failures(self):
        self.assertEqual(self.kind(None, error_kind="ssl"), "SSL/HTTPS Certificate Error")
        self.assertEqual(self.kind(None, error_kind="timeout"), "Link Timeout")
        self.assertEqual(self.kind(None, error_kind="connection"), "Broken Link")
        self.assertEqual(self.kind(None, error_kind="redirect_loop"), "Broken Link")
        self.assertEqual(self.kind(None, error_kind="other"), "Unverified Link")

    def test_403_is_never_broken_category(self):
        self.assertEqual(classify_link_result(403)["category"], "access_restricted")


class ScoreTests(unittest.TestCase):
    
    def test_penalties_match_specification(self):
        self.assertEqual(PENALTIES, {
        "Critical": 8,
        "Medium": 3,
        "Low": 0,
        })

    def test_formula(self):
        self.assertEqual(health_score(0, 5), 100.0)
        self.assertEqual(health_score(16, 5), 84.0)
        self.assertEqual(health_score(25, 100), 75.0)
        self.assertEqual(health_score(100, 10), 0.0)
        self.assertEqual(health_score(0, 0), 100.0)


    def test_resolved_issues_do_not_count(self):
        issues = [effective_issue({"issue_type": "Broken Link", "http_status": 404,
                                   "severity": "Critical", "category": "broken_link", "status": s})
                  for s in ("Open", "Resolved")]
        result = summarize(issues, 8)
        self.assertEqual(result["penalty_points"], 8)
        self.assertEqual(result["resolved_issues"], 1)
        self.assertEqual(result["health_score"], 92.0)

    def test_legacy_severity_names(self):
        self.assertEqual(normalize_severity("High"), "Critical")
        self.assertEqual(normalize_severity("Warning"), "Medium")
        self.assertEqual(normalize_severity("whatever"), "Low")


class AnalysisTests(unittest.TestCase):
    def types(self, html, url="http://x.test/page", headers=None):
        return {i["issue_type"] for i in analyze_html(url, html, headers)[1]}

    GOOD = ('<html><head><title>Fine</title><meta name="description" content="d">'
            '<link rel="canonical" href="http://x.test/page"></head>'
            '<body><h1>Hi</h1><img src="a.png" alt=""></body></html>')

    def test_clean_page_has_no_findings(self):
        self.assertEqual(self.types(self.GOOD), set())

    def test_each_finding(self):
        self.assertIn("Missing Page Title", self.types("<html><body><h1>x</h1></body></html>"))
        long_title = "<title>" + "a" * 70 + "</title>"
        self.assertIn("Long Title Tag", self.types(self.GOOD.replace("<title>Fine</title>", long_title)))
        self.assertIn("Multiple H1 Tags", self.types(self.GOOD.replace("<h1>Hi</h1>", "<h1>a</h1><h1>b</h1>")))
        self.assertIn("Missing H1", self.types(self.GOOD.replace("<h1>Hi</h1>", "")))
        self.assertIn("Missing Image ALT Text", self.types(self.GOOD.replace(' alt=""', "")))
        self.assertIn("Noindex Tag", self.types(self.GOOD.replace("</head>", '<meta name="robots" content="noindex"></head>')))
        self.assertIn("Noindex Tag", self.types(self.GOOD, headers={"X-Robots-Tag": "noindex"}))
        self.assertIn("Missing Canonical Tag", self.types(self.GOOD.replace('<link rel="canonical" href="http://x.test/page">', "")))
        self.assertIn("Uppercase or Underscore in URL", self.types(self.GOOD, url="http://x.test/My_Page"))
        self.assertIn("Long URL", self.types(self.GOOD, url="http://x.test/" + "a" * 120))

    def test_decorative_empty_alt_is_not_flagged(self):
        self.assertNotIn("Missing Image ALT Text", self.types(self.GOOD))


class AgentTests(unittest.TestCase):
    def test_every_issue_type_has_cause_and_fix(self):
        for issue_type in ISSUE_TYPES:
            result = audit_agent.analyze_issue({
                "issue_id": 1, "issue_type": issue_type, "http_status": 404,
                "severity": "Medium", "category": ISSUE_TYPES[issue_type][1],
                "target_url": "http://x.test/", "source_url": "http://x.test/", "status": "Open"})
            for key in ("explanation", "cause", "impact", "recommended_action", "example_fix"):
                self.assertTrue(result[key], f"{issue_type}: {key}")


if __name__ == "__main__":
    unittest.main()
