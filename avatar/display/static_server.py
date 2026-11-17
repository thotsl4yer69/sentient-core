#!/usr/bin/env python3
"""Serve the hologram renderer on loopback. No directory listing outside that tree."""

from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--dir", required=True)
    args = parser.parse_args()
    handler = partial(SimpleHTTPRequestHandler, directory=args.dir)
    server = ThreadingHTTPServer((args.bind, args.port), handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
