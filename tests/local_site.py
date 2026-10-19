"""A tiny in-process website used by the crawler and recheck tests."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = """<!DOCTYPE html><html><head>{head}</head><body>{body}</body></html>"""
GOOD_HEAD = (
    "<title>{title}</title>"
    '<meta name="description" content="A page">'
    '<link rel="canonical" href="http://example.test/">'
)


def page(title, body, head_extra=""):
    head = (GOOD_HEAD.format(title=title) if title else
            '<meta name="description" content="A page"><link rel="canonical" href="http://example.test/">')
    return PAGE.format(head=head + head_extra, body=body)


class Site:
    """Routes can be changed at runtime, which lets tests 'fix' a page."""

    def __init__(self):
        self.hits = []
        self.routes = {}
        self.port = None
        self._server = None

    def start(self):
        site = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                site.hits.append((self.path, dict(self.headers)))
                route = site.routes.get(self.path.split("#")[0])
                if route is None:
                    self.send_response(404)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(b"<html><body>not found</body></html>")
                    return
                status, headers, body = route
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                if "Content-Type" not in headers:
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                if isinstance(body, str):
                    body = body.encode()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self._server.shutdown()
        self._server.server_close()

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}"

    def standard_routes(self):
        ext = f"http://localhost:{self.port}/ext-ok"
        self.routes = {
            "/": (200, {}, page("Home", f"""
                <h1>Home</h1>
                <a href="/about">About</a>
                <a href="/about#team">About again</a>
                <a href="/missing">Missing</a>
                <a href="/forbidden">Forbidden</a>
                <a href="/server-error">Broken server</a>
                <a href="/redirect1">Chain</a>
                <a href="/redirect-once">One hop</a>
                <a href="/depth1">Deep</a>
                <a href="/no-title">No title</a>
                <a href="{ext}">External over http</a>
                <a href="mailto:me@example.test">mail</a>
                <a href="/file.pdf">pdf</a>
                <img src="/x.png">""")),
            "/about": (200, {}, page("About Us", '<h1>About</h1><a href="/">Home</a>')),
            "/no-title": (200, {}, page("", '<h1>No title</h1>')),
            "/forbidden": (403, {}, "<html><body>nope</body></html>"),
            "/server-error": (500, {}, "<html><body>boom</body></html>"),
            "/redirect1": (302, {"Location": "/redirect2"}, ""),
            "/redirect2": (302, {"Location": "/redirect3"}, ""),
            "/redirect3": (302, {"Location": "/about"}, ""),
            "/redirect-once": (301, {"Location": "/about"}, ""),
            "/depth1": (200, {}, page("Depth 1", '<h1>1</h1><a href="/depth2">2</a>')),
            "/depth2": (200, {}, page("Depth 2", '<h1>2</h1><a href="/depth3">3</a>')),
            "/depth3": (200, {}, page("Depth 3", '<h1>3</h1><a href="/depth4">4</a>')),
            "/depth4": (200, {}, page("Depth 4", '<h1>4</h1>')),
            "/file.pdf": (200, {"Content-Type": "application/pdf"}, b"%PDF-1.4"),
            "/ext-ok": (200, {}, page("External", "<h1>ext</h1>")),
            
            "/priority-home": (200, {}, page("Priority Home", """
                <nav>
                    <a href="/priority-a">Priority A</a>
                    <a href="/priority-b">Priority B</a>
                    <a href="/priority-about">About</a>
                    <a href="/priority-contact">Contact</a>
                </nav>
            """)),
            "/priority-a": (200, {}, page("Priority A", "<h1>Priority A</h1>")),
            "/priority-b": (200, {}, page("Priority B", "<h1>Priority B</h1>")),
            "/priority-about": (200, {}, page("Priority About", """
                <a href="/priority-a">Priority A</a>
            """)),
            "/priority-contact": (200, {}, page("Priority Contact", """
                <a href="/priority-a">Priority A</a>
            """)),

        }
        return self
if __name__ == "__main__":
    site = Site().start()
    site.standard_routes()

    print(f"Test website running at: {site.base}")
    print("Press Ctrl+C to stop the server.")

    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        site.stop()