"""
Lightweight threaded MJPEG HTTP server for Sentient vision nodes.

Usage:
    server = MJPEGServer(port=8090)
    server.start()

    # In your detection loop:
    server.update_frame(jpeg_bytes)

    # On shutdown:
    server.stop()
"""

import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
import logging

logger = logging.getLogger(__name__)


class MJPEGHandler(BaseHTTPRequestHandler):
    """Serves MJPEG stream and a simple status page."""

    server: "MJPEGServer"

    def log_message(self, format, *args):
        pass  # Suppress per-request logs

    def do_GET(self):
        if self.path == "/stream":
            self._stream()
        elif self.path == "/snapshot":
            self._snapshot()
        elif self.path == "/health":
            self._health()
        else:
            self._index()

    def _stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        try:
            while True:
                frame = self.server.current_frame
                if frame is not None:
                    self.wfile.write(b"--frame\r\n")
                    self.wfile.write(b"Content-Type: image/jpeg\r\n")
                    self.wfile.write(f"Content-Length: {len(frame)}\r\n".encode())
                    self.wfile.write(b"\r\n")
                    self.wfile.write(frame)
                    self.wfile.write(b"\r\n")
                    self.wfile.flush()
                time.sleep(1.0 / self.server.target_fps)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _snapshot(self):
        frame = self.server.current_frame
        if frame is None:
            self.send_error(503, "No frame available")
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(frame)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(frame)

    def _health(self):
        import json
        data = json.dumps({
            "status": "ok",
            "has_frame": self.server.current_frame is not None,
            "fps": self.server.target_fps,
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _index(self):
        html = b"""<html><body style="background:#000;margin:0">
        <img src="/stream" style="width:100%;height:auto">
        </body></html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)


class MJPEGServer(HTTPServer):
    """Threaded MJPEG server with frame update support."""

    def __init__(self, port=8090, target_fps=10):
        self.current_frame = None
        self.target_fps = target_fps
        self._thread = None
        super().__init__(("0.0.0.0", port), MJPEGHandler)
        logger.info(f"MJPEG server initialized on port {port}")

    def update_frame(self, jpeg_bytes: bytes):
        """Update the current frame (thread-safe, single reference swap)."""
        self.current_frame = jpeg_bytes

    def start(self):
        """Start serving in a daemon thread."""
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()
        logger.info(f"MJPEG server started on port {self.server_port}")

    def stop(self):
        """Stop the server."""
        self.shutdown()
        if self._thread:
            self._thread.join(timeout=3)
        logger.info("MJPEG server stopped")
