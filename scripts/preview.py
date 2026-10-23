"""Run Univorya locally and open it in the machine's own browser.

A page served over HTTP cannot open a visitor's browser, so this is the maker's
own command: boot Uvicorn, wait until the app really answers, then hand the URL
to the desktop browser — or point an in-app browser at the printed address.

    python scripts/preview.py                 # data/ on 127.0.0.1:8123, opens a browser
    python scripts/preview.py --temp          # throwaway database, nothing touches data/
    python scripts/preview.py --no-browser    # server only; prints the URL
"""
import argparse
import os
import socket
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path

import httpx
import uvicorn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def port_busy(host, port, timeout=1.0):
    with socket.socket() as probe:
        probe.settimeout(timeout)
        return probe.connect_ex((host, port)) == 0


def wait_until_healthy(url, timeout=25.0, fetch=None, pause=0.25):
    """Poll /api/health so the browser is never handed a server that is still booting."""
    deadline = time.monotonic() + timeout
    check = fetch or (lambda target: httpx.get(target, timeout=2).status_code == 200)
    while time.monotonic() < deadline:
        try:
            if check(url.rstrip('/') + '/api/health'):
                return True
        except Exception:
            pass
        time.sleep(pause)
    return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8123)
    parser.add_argument('--data-dir', default=str(ROOT / 'data'),
                        help='where univorya.sqlite3 and uploads/ live')
    parser.add_argument('--temp', action='store_true',
                        help='use a throwaway data directory instead of the real one')
    parser.add_argument('--no-browser', action='store_true', help='start the server only')
    args = parser.parse_args(argv)

    data_dir = tempfile.mkdtemp(prefix='univorya-preview-') if args.temp else args.data_dir
    os.environ['UNIVORYA_DATA_DIR'] = str(data_dir)
    url = f'http://{args.host}:{args.port}'

    if port_busy(args.host, args.port):
        # Something is already answering there — reuse it rather than failing to bind.
        if wait_until_healthy(url, timeout=3.0):
            print(f'Univorya is already running at {url} (data dir: {data_dir})')
            if not args.no_browser:
                webbrowser.open(url)
            return 0
        print(f'Port {args.port} is taken by something that is not Univorya; pass --port.', file=sys.stderr)
        return 1

    from app import main as app_main  # imported after UNIVORYA_DATA_DIR is set

    server = uvicorn.Server(uvicorn.Config(app_main.app, host=args.host, port=args.port, log_level='warning'))
    threading.Thread(target=server.run, daemon=True).start()
    if not wait_until_healthy(url):
        print('The server did not answer /api/health in time.', file=sys.stderr)
        return 1

    print(f'Univorya is live at {url}  (database: {Path(data_dir) / "univorya.sqlite3"})')
    print('Publish through /submit, then watch /dashboard and your maker profile.')
    print('Press Ctrl-C to stop.')
    if not args.no_browser:
        webbrowser.open(url)
    try:
        while server.should_exit is False:
            time.sleep(0.5)
    except KeyboardInterrupt:
        server.should_exit = True
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
