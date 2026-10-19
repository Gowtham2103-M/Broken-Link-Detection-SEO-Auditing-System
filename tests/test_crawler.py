import unittest
import heapq
import crawler
from classification import summarize, effective_issue
from tests.local_site import Site


def by_type(issues, issue_type):
    return [i for i in issues if i["issue_type"] == issue_type]


class CrawlerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.site = Site().start().standard_routes()
        cls.result = crawler.crawl_website_detailed(
            cls.site.base + "/", max_pages=50, crawl_delay=0
        )

    @classmethod
    def tearDownClass(cls):
        cls.site.stop()

    def issues(self, issue_type):
        return by_type(self.result["issues"], issue_type)

    def test_404_is_confirmed_broken_link(self):
        found = self.issues("Broken Link")
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0]["target_url"].endswith("/missing"))
        self.assertEqual(found[0]["http_status"], 404)
        self.assertEqual(found[0]["severity"], "Critical")
        self.assertEqual(found[0]["category"], "broken_link")

    def test_403_is_access_restricted_not_broken(self):
        found = self.issues("Access Restricted")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["http_status"], 403)
        self.assertEqual(found[0]["category"], "access_restricted")
        self.assertEqual(found[0]["severity"], "Low")
        self.assertFalse(
            any(i["target_url"].endswith("/forbidden") for i in self.issues("Broken Link"))
        )

    def test_500_is_server_error(self):
        found = self.issues("Server Error")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["http_status"], 500)
        self.assertEqual(found[0]["category"], "server_error")

    def test_redirects_single_and_chain(self):
        chain = self.issues("Long Redirect Chain")
        single = self.issues("Redirect")
        self.assertEqual([i["target_url"][-10:] for i in chain], ["/redirect1"])
        self.assertEqual([i["target_url"][-14:] for i in single], ["/redirect-once"])
        self.assertEqual(single[0]["severity"], "Low")
        self.assertEqual(chain[0]["severity"], "Medium")

    def test_seo_findings(self):
        self.assertTrue(any(i["target_url"].endswith("/no-title")
                            for i in self.issues("Missing Page Title")))
        self.assertTrue(self.issues("Missing Image ALT Text"))
        self.assertTrue(self.issues("Non-HTTPS Outbound Link"))

    def test_duplicate_urls_are_crawled_once(self):
        urls = [p["url"] for p in self.result["pages"]]
        self.assertEqual(len(urls), len(set(urls)))
        self.assertEqual(sum(u.endswith("/about") for u in urls), 1)

    def test_non_page_files_are_checked_but_not_crawled(self):
        urls = [p["url"] for p in self.result["pages"]]
        self.assertFalse(any(u.endswith(".pdf") for u in urls))

    def test_stats_are_consistent(self):
        stats = self.result["stats"]
        self.assertEqual(stats["pages_crawled"], len(self.result["pages"]))
        self.assertEqual(stats["links_checked"], stats["links_found"] - stats["links_skipped"])
        self.assertEqual(stats["fatal_error"], "")

    def test_summary_matches_issues_and_formula(self):
        eff = [effective_issue(dict(i, status="Open")) for i in self.result["issues"]]
        s = summarize(eff, len(self.result["pages"]), self.result["stats"]["links_checked"])
        penalty = sum({"Critical": 8, "Medium": 3, "Low": 0}[i["severity"]] for i in eff)
        self.assertEqual(s["penalty_points"], penalty)
        expected = round(max(0, min(100, 100 - penalty)), 1)
        self.assertEqual(s["health_score"], expected)
        self.assertEqual(s["broken_links"], 1)
        self.assertEqual(s["server_errors"], 1)
        self.assertEqual(s["access_restricted"], 1)


class DepthAndLimitTests(unittest.TestCase):
    def setUp(self):
        self.site = Site().start().standard_routes()

    def tearDown(self):
        self.site.stop()

    def crawl(self, **kwargs):
        return crawler.crawl_website_detailed(self.site.base + "/", crawl_delay=0, **kwargs)

    def page_paths(self, result):
        return {p["url"].replace(self.site.base, "") for p in result["pages"]}

    def test_depth_zero_crawls_only_start_page(self):
        result = self.crawl(max_pages=50, max_depth=0)
        self.assertEqual(self.page_paths(result), {"/"})
        # links on the start page are still *checked*
        self.assertTrue(any(i["issue_type"] == "Broken Link" for i in result["issues"]))

    def test_depth_limit_is_enforced(self):
        shallow = self.page_paths(self.crawl(max_pages=50, max_depth=1))
        self.assertIn("/depth1", shallow)
        self.assertNotIn("/depth2", shallow)

        deeper = self.page_paths(self.crawl(max_pages=50, max_depth=2))
        self.assertIn("/depth2", deeper)
        self.assertNotIn("/depth3", deeper)

        unlimited = self.page_paths(self.crawl(max_pages=50, max_depth=None))
        self.assertIn("/depth4", unlimited)

    def test_max_pages_limit_is_enforced(self):
        result = self.crawl(max_pages=3)
        self.assertEqual(len(result["pages"]), 3)

    def test_progress_is_real_and_ordered(self):
        events = []
        self.crawl(max_pages=50, progress_callback=events.append)
        phases = [e["phase"] for e in events]
        self.assertIn("crawling", phases)
        self.assertIn("checking_links", phases)
        self.assertLess(phases.index("crawling"), phases.index("checking_links"))
        crawled = [e["pages_crawled"] for e in events if e["phase"] == "crawling"]
        self.assertEqual(crawled, sorted(crawled))
        self.assertEqual(max(crawled), len(self.page_paths(self.crawl(max_pages=50))))
        checked = [e["links_checked"] for e in events if e["phase"] == "checking_links"]
        self.assertEqual(checked, sorted(checked))

    def test_start_redirect_to_other_host_variant_is_followed(self):
        self.site.routes["/start"] = (301, {"Location": "/"}, "")
        result = crawler.crawl_website_detailed(self.site.base + "/start", max_pages=10, crawl_delay=0)
        self.assertTrue(result["pages"][0]["redirects"] >= 1)
        self.assertEqual(result["stats"]["fatal_error"], "")

    def test_unreachable_start_url_is_reported(self):
        result = crawler.crawl_website_detailed("http://127.0.0.1:1/", max_pages=5, crawl_delay=0)
        self.assertTrue(result["stats"]["fatal_error"])
        self.assertEqual(result["issues"], [])

    def test_invalid_scheme_rejected(self):
        with self.assertRaises(ValueError):
            crawler.crawl_website_detailed("ftp://example.com")


class PriorityQueueTests(unittest.TestCase):
    def test_updated_priority_is_used_and_stale_entry_is_ignored(self):
        queue = []
        priority_versions = {"page-a": 1, "page-b": 1}
        crawled = set()

        # Initially, A has priority 8 and B has priority 5.
        heapq.heappush(queue, (8, 1, "page-a", 1))
        heapq.heappush(queue, (5, 1, "page-b", 1))

        # A's priority improves to 2 before it is processed.
        priority_versions["page-a"] += 1
        heapq.heappush(queue, (2, 1, "page-a", 2))

        processed = []

        while queue:
            priority, depth, url, version = heapq.heappop(queue)

            if version != priority_versions[url]:
                continue

            if url in crawled:
                continue

            crawled.add(url)
            processed.append((url, priority))

        self.assertEqual(
            processed,
            [("page-a", 2), ("page-b", 5)],
        )
        self.assertEqual(len(crawled), 2)

if __name__ == "__main__":
    unittest.main()
