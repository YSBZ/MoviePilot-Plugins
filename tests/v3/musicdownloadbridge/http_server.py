"""A small real HTTP server for the plugin's local integration tests."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlsplit


SONG = {
    "id": "song-001",
    "source": "netease",
    "name": "夜曲",
    "artist": "周杰伦",
    "album": "十一月的萧邦",
    "cover": "https://example.invalid/cover.jpg",
    "extra": "{\"media_id\":\"song-001\"}",
}


class MusicBridgeHTTPServer:
    """Serve the documented contract under the MoviePilot ``/music`` prefix."""

    def __init__(self) -> None:
        self.state = {
            "token": "local-test-token",
            "songs": [dict(SONG)],
            "requests": [],
            "downloads": {},
            "fail_search_status": None,
            "fail_post_status": None,
        }
        state = self.state

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_args: object) -> None:
                return

            def _json(self, status: int, payload: dict) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authorized(self) -> bool:
                parsed = urlsplit(self.path)
                state["requests"].append(
                    {
                        "method": self.command,
                        "path": parsed.path,
                        "query": parse_qs(parsed.query),
                        "authorization": self.headers.get("Authorization"),
                    }
                )
                if self.headers.get("Authorization") != f"Bearer {state['token']}":
                    self._json(401, {"error": "unauthorized"})
                    return False
                return True

            def do_GET(self) -> None:
                if not self._authorized():
                    return
                parsed = urlsplit(self.path)
                if parsed.path == "/music/api/music-bridge/search":
                    status = state["fail_search_status"]
                    if status:
                        state["fail_search_status"] = None
                        self._json(status, {"error": state["token"]})
                        return
                    self._json(200, {"songs": state["songs"]})
                    return
                prefix = "/music/api/music-bridge/downloads/"
                if parsed.path.startswith(prefix):
                    request_id = parsed.path.removeprefix(prefix)
                    result = state["downloads"].get(request_id)
                    if result is None:
                        self._json(404, {"error": "unknown request"})
                    else:
                        self._json(200, dict(result))
                    return
                self._json(404, {"error": "unknown route"})

            def do_POST(self) -> None:
                if not self._authorized():
                    return
                if urlsplit(self.path).path != "/music/api/music-bridge/downloads":
                    self._json(404, {"error": "unknown route"})
                    return
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length))
                state["requests"][-1]["json"] = payload
                status = state["fail_post_status"]
                if status:
                    self._json(status, {"error": "temporarily unavailable"})
                    return
                request_id = payload["request_id"]
                state["downloads"].setdefault(
                    request_id,
                    {
                        "request_id": request_id,
                            "status": "waiting",
                        "path": None,
                        "error": None,
                        "warning": None,
                    },
                )
                self._json(202, {"request_id": request_id, "status": "waiting"})

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}/music"

    def start(self) -> "MusicBridgeHTTPServer":
        self._thread.start()
        return self

    def set_status(
        self,
        request_id: str,
        status: str,
        *,
        path: str | None = None,
        error: str | None = None,
        warning: str | None = None,
    ) -> None:
        self.state["downloads"][request_id] = {
            "request_id": request_id,
            "status": status,
            "path": path,
            "error": error,
            "warning": warning,
        }

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)

    def __enter__(self) -> "MusicBridgeHTTPServer":
        return self.start()

    def __exit__(self, *_args: object) -> None:
        self.close()
