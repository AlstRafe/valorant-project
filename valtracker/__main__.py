"""``python -m valtracker``: serve VAL Tracker on its own on this PC (http://127.0.0.1:5050/), to try it
without Home Page's server. Only this PC can open it, so whoever opens it is the owner (settings included)."""

from __future__ import annotations

import argparse

from valtracker import create_app


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m valtracker", description="VAL Tracker をこの PC だけで動かす")
    parser.add_argument("--port", type=int, default=5050)
    args = parser.parse_args()
    from waitress import serve

    print(f"VAL Tracker: http://127.0.0.1:{args.port}/ （止めるには Ctrl+C）", flush=True)
    serve(create_app(), host="127.0.0.1", port=args.port, threads=8)


if __name__ == "__main__":
    main()
