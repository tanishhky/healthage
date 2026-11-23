"""python -m healthage [--port 8765] [--no-browser] [--verbose]"""

import argparse
import sys

from . import __version__


def main(argv=None):
    ap = argparse.ArgumentParser(prog="healthage",
                                 description="Coachable Health Age on your own Google Health data, computed locally.")
    ap.add_argument("--port", type=int, default=8765, help="local port (default 8765)")
    ap.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    ap.add_argument("--verbose", action="store_true", help="log every request")
    ap.add_argument("--version", action="version", version=f"healthage {__version__}")
    args = ap.parse_args(argv)
    if sys.version_info < (3, 9):
        sys.exit("healthage needs Python 3.9 or newer.")
    from .server import serve
    serve(port=args.port, verbose=args.verbose, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
