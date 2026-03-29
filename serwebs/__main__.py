from __future__ import annotations

import argparse

import uvicorn

from serwebs.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description="SerWebs — Web Serial Terminal Manager")
    parser.add_argument("-c", "--config", default=None, help="Path to config.toml")
    parser.add_argument("--host", default=None, help="Bind host (overrides config)")
    parser.add_argument("--port", type=int, default=None, help="Bind port (overrides config)")
    args = parser.parse_args()

    cfg = load_config(args.config)
    host = args.host or cfg.server.host
    port = args.port or cfg.server.port

    uvicorn.run(
        "serwebs.app:create_app",
        factory=True,
        host=host,
        port=port,
        log_level=cfg.logging.level.lower(),
    )


if __name__ == "__main__":
    main()
