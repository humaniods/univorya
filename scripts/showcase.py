"""Open a creator's own project in a Chrome window on this machine.

A page served over HTTP cannot start a browser on the visitor's machine, so this is the maker's
own command: read the loopback preview address back out of the SQLite file, prove it really
points at this machine, and hand it to Chrome in app mode.

    python scripts/showcase.py my-project                  # open the stored address
    python scripts/showcase.py my-project --print           # only print it
    python scripts/showcase.py my-project --browser /usr/bin/chromium
"""
import argparse
import contextlib
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
LOCAL_HOSTS = {'127.0.0.1', 'localhost', '::1', '[::1]'}


def fail(message):
    print(message, file=sys.stderr)
    return 1


def preview_address(db_path, slug):
    """Return (title, stored preview_url) for a slug, or raise LookupError with the reason."""
    if not db_path.exists():
        raise LookupError(f'No Univorya database at {db_path}. Start the app once, or pass --data-dir.')
    with contextlib.closing(sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)) as con:
        try:
            row = con.execute('SELECT title, preview_url FROM projects WHERE slug=?', (slug,)).fetchone()
        except sqlite3.OperationalError as err:
            if 'no such column' in str(err).lower():
                raise LookupError(f'{db_path} predates local preview. Run python scripts/preview.py once to '
                                  'add the column, then set the address on the edit page.')
            raise LookupError(f'Cannot read {db_path}: {err}')
    if not row:
        raise LookupError(f'No project with the slug "{slug}". Your dashboard lists each project\'s address.')
    return row[0], (row[1] or '').strip()


def is_loopback(url):
    parsed = urlparse(url)
    return parsed.scheme in ('http', 'https') and (parsed.hostname or '').lower() in LOCAL_HOSTS


def listening(url, timeout=1.0):
    """True when something is actually accepting connections, so Chrome never opens an error page."""
    parsed = urlparse(url)
    host = parsed.hostname or '127.0.0.1'
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    try:
        targets = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    for family, _, _, _, address in targets:
        with socket.socket(family) as probe:
            probe.settimeout(timeout)
            if probe.connect_ex(address) == 0:
                return True
    return False


def browser_binary(explicit=None):
    for candidate in (explicit, os.environ.get('UNIVORYA_BROWSER'), shutil.which('google-chrome'),
                      shutil.which('google-chrome-stable'), shutil.which('chromium'),
                      shutil.which('chromium-browser')):
        if candidate and Path(candidate).exists():
            return str(candidate)
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('slug')
    parser.add_argument('--data-dir', default=os.environ.get('UNIVORYA_DATA_DIR', str(ROOT / 'data')),
                        help='where univorya.sqlite3 lives')
    parser.add_argument('--browser', help='path to a Chrome or Chromium binary')
    parser.add_argument('--print', action='store_true', dest='echo', help='print the address and stop')
    args = parser.parse_args(argv)

    try:
        title, url = preview_address(Path(args.data_dir) / 'univorya.sqlite3', args.slug)
    except LookupError as err:
        return fail(str(err))
    if not url:
        return fail(f'"{title}" has no local preview address yet. Edit the project and fill in '
                    '"Local preview address" with, for example, http://localhost:5173.')
    if not is_loopback(url):
        return fail(f'The stored address {url} does not point at this machine, so nothing is launched.')
    if args.echo:
        print(url)
        return 0
    if not listening(url):
        return fail(f'Nothing is listening at {url}. Start your dev server, then run this again.')
    binary = browser_binary(args.browser)
    if not binary:
        return fail(f'No Chrome or Chromium found. Pass --browser /path/to/chrome, or open {url} yourself.')
    profile = Path(tempfile.gettempdir()) / 'univorya-showcase'
    # --no-first-run keeps Chrome from opening its own welcome dialog over the maker's project.
    subprocess.Popen([binary, f'--app={url}', f'--user-data-dir={profile}',
                      '--no-first-run', '--no-default-browser-check'])
    print(f'Opening "{title}" at {url} in {binary} (separate profile: {profile}).')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
