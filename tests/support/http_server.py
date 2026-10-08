"""Serveur HTTP local pour tester le téléchargeur sans réseau externe.

Scénarios injectables : support de Range, coupures en cours de flux (reproduit
`IncompleteRead` / `ChunkedEncodingError`), codes HTTP forcés (503, 404),
redirection 302 (comme data.gouv.fr), changement de version (ETag).
"""

from __future__ import annotations

import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeSource:
    """Un « fichier distant » servi à l'adresse `/stable` (302 → `/files/data.bin`)."""

    def __init__(
        self,
        content: bytes,
        *,
        support_range: bool = True,
        redirect: bool = True,
        etag: str | None = '"v1"',
        chunked_without_length: bool = False,
    ) -> None:
        self.content = content
        self.support_range = support_range
        self.redirect = redirect
        self.etag = etag
        self.chunked_without_length = chunked_without_length
        self.drop_after: deque[int | None] = deque()  # octets envoyés avant coupure, par GET « corps »
        self.forced_status: deque[int] = deque()  # statuts forcés, consommés par requête
        self.requests: list[dict] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # --- cycle de vie -----------------------------------------------------------
    def start(self) -> FakeSource:
        source = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # silence
                return

            def _record(self):
                source.requests.append(
                    {"method": self.command, "path": self.path, "range": self.headers.get("Range"),
                     "if_range": self.headers.get("If-Range")}
                )

            def do_HEAD(self):
                self._record()
                try:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(source.content)))
                    self.end_headers()
                except (ConnectionResetError, BrokenPipeError):
                    pass

            def do_GET(self):
                self._record()
                try:
                    if self.path == "/stable":
                        if source.redirect:
                            self.send_response(302)
                            self.send_header("Location", f"{source.base_url}/files/data.bin")
                            self.send_header("Content-Length", "0")
                            self.end_headers()
                            return
                        return self._serve_file()
                    if self.path == "/files/data.bin":
                        return self._serve_file()
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                except (ConnectionResetError, BrokenPipeError):
                    # Le client ferme volontairement la connexion dans les tests
                    # de reprise/coupure; ce n'est pas une erreur du serveur factice.
                    pass

            def _serve_file(self):
                if source.forced_status:
                    status = source.forced_status.popleft()
                    self.send_response(status)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return

                data = source.content
                total = len(data)
                start, end, partial = 0, total - 1, False
                range_header = self.headers.get("Range")
                if_range = self.headers.get("If-Range")
                honour_range = (
                    source.support_range
                    and range_header
                    and range_header.startswith("bytes=")
                    and (if_range is None or if_range == source.etag)
                )
                if honour_range:
                    spec = range_header[len("bytes="):]
                    first, _, last = spec.partition("-")
                    start = int(first)
                    end = int(last) if last else total - 1
                    end = min(end, total - 1)
                    if start >= total:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{total}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    partial = True

                body = data[start : end + 1]
                self.send_response(206 if partial else 200)
                if source.etag:
                    self.send_header("ETag", source.etag)
                if source.support_range:
                    self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Type", "application/octet-stream")
                if partial:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
                if source.chunked_without_length and not partial:
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    for i in range(0, len(body), 4096):
                        piece = body[i : i + 4096]
                        self.wfile.write(f"{len(piece):x}\r\n".encode() + piece + b"\r\n")
                    self.wfile.write(b"0\r\n\r\n")
                    return
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()

                # Coupure injectée uniquement pour les « vrais » téléchargements (> 1 octet).
                cut = source.drop_after.popleft() if (source.drop_after and len(body) > 1) else None
                if cut is not None:
                    self.wfile.write(body[:cut])
                    self.wfile.flush()
                    self.close_connection = True
                    self.connection.close()
                    return
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self._thread:
            self._thread.join(timeout=5)

    @property
    def base_url(self) -> str:
        assert self._server is not None
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def stable_url(self) -> str:
        return f"{self.base_url}/stable"

    def body_requests(self) -> list[dict]:
        """Requêtes GET sur le fichier lui-même, hors sonde `bytes=0-0`."""
        return [
            r for r in self.requests
            if r["method"] == "GET" and r["path"] == "/files/data.bin" and r["range"] != "bytes=0-0"
        ]
