"""The local preview launcher must only hand a browser a server that already answers."""
import importlib.util
import os
import socket
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('preview', ROOT / 'scripts' / 'preview.py')
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


def test_health_poll_waits_for_the_server_instead_of_guessing():
    answers = iter([False, False, True])
    seen = []

    def fetch(url):
        seen.append(url)
        return next(answers)

    assert preview.wait_until_healthy('http://127.0.0.1:5999', timeout=5, fetch=fetch, pause=0.01) is True
    assert seen == ['http://127.0.0.1:5999/api/health'] * 3


def test_health_poll_gives_up_instead_of_hanging():
    def fetch(url):
        raise ConnectionError('nothing is listening')

    assert preview.wait_until_healthy('http://127.0.0.1:5999/', timeout=0.3, fetch=fetch, pause=0.01) is False


def test_a_busy_port_is_recognised_before_trying_to_bind():
    with socket.socket() as held:
        held.bind(('127.0.0.1', 0))
        held.listen(1)
        port = held.getsockname()[1]
        assert preview.port_busy('127.0.0.1', port) is True
    assert preview.port_busy('127.0.0.1', port) is False


def test_an_already_running_preview_is_reused_not_restarted(monkeypatch):
    monkeypatch.setattr(preview, 'port_busy', lambda host, port, timeout=1.0: True)
    monkeypatch.setattr(preview, 'wait_until_healthy', lambda *a, **k: True)
    opened = []
    monkeypatch.setattr(preview.webbrowser, 'open', opened.append)
    assert preview.main(['--port', '5998']) == 0
    assert opened == ['http://127.0.0.1:5998'], 'the browser should be pointed at the running instance'


def test_no_browser_and_temp_data_dir_are_honoured(tmp_path, monkeypatch):
    # Registered so the assignment main() makes cannot leak into the rest of the suite.
    monkeypatch.setenv('UNIVORYA_DATA_DIR', str(tmp_path / 'elsewhere'))
    monkeypatch.setattr(preview, 'port_busy', lambda host, port, timeout=1.0: True)
    monkeypatch.setattr(preview, 'wait_until_healthy', lambda *a, **k: True)
    monkeypatch.setattr(preview.tempfile, 'mkdtemp', lambda **kw: str(tmp_path))
    opened = []
    monkeypatch.setattr(preview.webbrowser, 'open', opened.append)
    assert preview.main(['--temp', '--port', '5998', '--no-browser']) == 0
    assert opened == []
    assert os.environ['UNIVORYA_DATA_DIR'] == str(tmp_path), 'a temp preview must not aim at data/'
