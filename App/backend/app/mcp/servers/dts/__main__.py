"""CLI entry point for ``mcp_dts``."""

from __future__ import annotations

import argparse

from .server import SERVER_NAME, create_app, serve_stdio


def main() -> None:
    parser = argparse.ArgumentParser(prog=SERVER_NAME)
    parser.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    parser.add_argument("--host", default="0.0.0.0")  # nosec B104 # noqa: S104
    parser.add_argument("--port", type=int, default=8934)
    args = parser.parse_args()

    if not args.http:
        serve_stdio()
        return

    import uvicorn

    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
