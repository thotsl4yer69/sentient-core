"""sc-core entry point: ``python -m sc.core``.

Refuses to start (exit 78, EX_CONFIG) on a missing or world-readable token file,
a missing persona or MiniLM model, or an invalid setting.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

import uvicorn

from sc.api import create_app
from sc.auth import load_tokens
from sc.brain import Brain
from sc.config import load_settings
from sc.embed import MiniLMEncoder
from sc.llm import LLMClient
from sc.memory import MemoryStore

logger = logging.getLogger("sc.core")


def _logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )


async def serve(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sentient Core brain (sc-core)")
    parser.add_argument("--log-level", default="info")
    parser.add_argument("--voice", action="store_true", help="tell Cortana she can listen and speak")
    args = parser.parse_args(argv)
    _logging(args.log_level)

    settings = load_settings()
    tokens = load_tokens(settings.tokens_file)
    encoder = MiniLMEncoder(models_dir=str(settings.models_dir) if settings.models_dir else None)
    store = MemoryStore(settings.db_path, encoder)
    llm = LLMClient(settings.llm_url, settings.llm_model, connect_timeout_s=settings.llm_connect_timeout_s)
    brain = Brain(settings, store, llm, voice_enabled=args.voice)
    app = create_app(settings, brain, tokens)

    tls = {}
    if settings.tls_cert is not None:
        tls = {"ssl_certfile": str(settings.tls_cert), "ssl_keyfile": str(settings.tls_key)}
    health = await brain.health()
    logger.info("sc-core starting on %s:%d — users=%s memories=%d llm=%s",
                settings.bind_host, settings.bind_port, sorted(tokens.users),
                health["memory"]["memories"], health["llm"]["status"])
    if settings.lan_exposed:
        logger.warning("listening beyond loopback on %s (%s); every request needs a bearer token",
                       settings.bind_host, "TLS" if tls else "PLAIN HTTP, allow_plain_lan")

    config = uvicorn.Config(app, host=settings.bind_host, port=settings.bind_port, log_level="warning",
                            access_log=False, lifespan="off", timeout_graceful_shutdown=5, **tls)
    server = uvicorn.Server(config)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: setattr(server, "should_exit", True))
    try:
        await server.serve()
    finally:
        await llm.close()
        store.close()
        logger.info("sc-core stopped")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return asyncio.run(serve(argv))
    except (FileNotFoundError, PermissionError, ValueError) as exc:
        logging.getLogger("sc.core").error("refusing to start: %s", exc)
        print(f"sc-core: refusing to start: {exc}", file=sys.stderr)
        return 78  # EX_CONFIG: systemd won't hammer-restart a config error


if __name__ == "__main__":
    raise SystemExit(main())
