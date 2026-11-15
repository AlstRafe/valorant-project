"""``python -m valtracker``: この PC だけで VAL Tracker を動かす（http://127.0.0.1:5050/）。
Home Page のサーバー無しで試す用。開けるのはこの PC だけなので、開いた人を持ち主として扱う（設定も含む）。"""

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
