
from http.server import SimpleHTTPRequestHandler, HTTPServer
from functools import partial
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
TEST_DIR = BASE_DIR / "test_site"

if not (TEST_DIR / "index.html").exists():
    raise FileNotFoundError(
        f"index.html not found inside: {TEST_DIR}"
    )


class TestHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        redirects = {
            "/redirect1": "/redirect2",
            "/redirect2": "/redirect3",
            "/redirect3": "/about.html"
        }

        if self.path in redirects:
            self.send_response(302)
            self.send_header("Location", redirects[self.path])
            self.end_headers()
            return

        # Extra status codes for checking the crawler's classification.
        statuses = {
            "/forbidden": 403,      # access restricted, not a confirmed broken link
            "/server-error": 500,   # server error
        }

        if self.path in statuses:
            self.send_response(statuses[self.path])
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>Test status page</body></html>")
            return

        if self.path == "/redirect-once":
            self.send_response(301)
            self.send_header("Location", "/about.html")
            self.end_headers()
            return

        super().do_GET()


handler = partial(TestHandler, directory=str(TEST_DIR))

server = HTTPServer(("127.0.0.1", 8000), handler)

print("Test website: http://127.0.0.1:8000")
print("Press Ctrl+C to stop.")

server.serve_forever()
