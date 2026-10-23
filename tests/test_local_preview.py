"""A creator can keep a loopback preview address and open it on their own machine.

The address is private by design: it is stored on the project, returned only to its owner, and
never rendered for anyone else. scripts/showcase.py reads it back out of SQLite and launches a
Chrome app window, so the guard-rails that matter are 'loopback only' and 'owner only'.
"""
import importlib
import importlib.util
import socket
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / 'static'
spec = importlib.util.spec_from_file_location('showcase', ROOT / 'scripts' / 'showcase.py')
showcase = importlib.util.module_from_spec(spec)
spec.loader.exec_module(showcase)


def fields(**kwargs):
    base = {'title': 'Loopback Preview Project', 'tagline': 'A project that points at its own dev server.',
            'description': 'Description long enough to pass the showcase rules and keep this fixture honest.',
            'category': 'Web App', 'stage': 'Building', 'tags': 'local, dev', 'tech': 'Vite',
            'cover_style': 'violet'}
    base.update(kwargs)
    return base


@pytest.fixture
def c(tmp_path, monkeypatch):
    monkeypatch.setenv('UNIVORYA_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('UNIVORYA_SEED_DEMO', '0')
    from app import main
    importlib.reload(main)
    with TestClient(main.app) as client:
        yield client


def signup(client, username='alpha'):
    return client.post('/api/auth/register', json={
        'name': 'Local Previewer', 'username': username, 'email': username + '@example.com',
        'password': 'a-good-password-45321'})


def publish(client, **kwargs):
    created = client.post('/api/projects', data=fields(**kwargs))
    assert created.status_code == 201, created.text
    return created.json()['slug']


def test_the_owner_stores_and_reads_back_their_loopback_address(c):
    assert signup(c).status_code == 201
    slug = publish(c, preview_url='http://localhost:5173')
    detail = c.get('/api/projects/' + slug).json()['project']
    assert detail['preview_url'] == 'http://localhost:5173'
    assert detail['is_owner'] is True
    listed = c.get('/api/projects?limit=12').json()['projects']
    assert [item['preview_url'] for item in listed] == ['http://localhost:5173']


def test_the_address_is_invisible_to_everybody_else(c):
    assert signup(c).status_code == 201
    slug = publish(c, preview_url='http://127.0.0.1:3000/app')
    assert 'preview_url' in c.get('/api/projects/' + slug).json()['project']
    c.post('/api/auth/logout')
    anonymous = c.get('/api/projects/' + slug).json()['project']
    assert 'preview_url' not in anonymous
    assert not any('preview_url' in item for item in c.get('/api/projects?limit=12').json()['projects'])
    assert not any('preview_url' in item for item in c.get('/api/projects?creator=alpha').json()['projects'])
    signup(c, 'beta')
    other = c.get('/api/projects/' + slug).json()['project']
    assert 'preview_url' not in other and other['is_owner'] is False


@pytest.mark.parametrize('address', [
    'https://example.com', 'http://192.168.1.12:3000', 'http://10.0.0.5',
    'http://localhost.evil.com', 'javascript:alert(1)', 'file:///etc/passwd',
    'http://user:pass@localhost:8080', 'http://[2001:db8::1]:8000', 'not a url at all',
])
def test_only_a_loopback_address_is_accepted(c, address):
    assert signup(c).status_code == 201
    rejected = c.post('/api/projects', data=fields(preview_url=address))
    assert rejected.status_code == 422, rejected.text
    assert 'preview' in rejected.json()['detail'].lower()
    assert publish(c, preview_url='')  # omitting it entirely stays valid


def test_editing_round_trips_the_address_and_still_refuses_the_public_one(c):
    assert signup(c).status_code == 201
    slug = publish(c, preview_url='http://localhost:4173')
    assert c.put('/api/projects/' + slug, data=fields(preview_url='http://127.0.0.1:4173')).status_code == 200
    assert c.get('/api/projects/' + slug).json()['project']['preview_url'] == 'http://127.0.0.1:4173'
    assert c.put('/api/projects/' + slug, data=fields(preview_url='https://deployed.example')).status_code == 422
    assert c.get('/api/projects/' + slug).json()['project']['preview_url'] == 'http://127.0.0.1:4173'
    assert c.put('/api/projects/' + slug, data=fields(preview_url='')).status_code == 200
    assert c.get('/api/projects/' + slug).json()['project']['preview_url'] == ''


def test_a_database_from_before_the_column_is_migrated_not_replaced(tmp_path, monkeypatch):
    from app import main
    legacy = tmp_path / 'legacy.sqlite3'
    con = sqlite3.connect(legacy)
    con.execute("""CREATE TABLE projects (id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT NOT NULL UNIQUE,
        user_id INTEGER NOT NULL, title TEXT NOT NULL, tagline TEXT NOT NULL, description TEXT NOT NULL,
        category TEXT NOT NULL, stage TEXT NOT NULL, tags TEXT NOT NULL DEFAULT '[]',
        tech TEXT NOT NULL DEFAULT '[]', repo_url TEXT DEFAULT '', demo_url TEXT DEFAULT '',
        website_url TEXT DEFAULT '', license TEXT DEFAULT '', cover_image TEXT DEFAULT '',
        cover_style TEXT DEFAULT 'violet', is_demo INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL, views INTEGER NOT NULL DEFAULT 0)""")
    con.execute("""INSERT INTO projects(slug,user_id,title,tagline,description,category,stage,created_at,updated_at)
        VALUES('legacy-project',1,'Old Row','still here','description','Web App','Building','then','then')""")
    con.commit()
    con.close()

    monkeypatch.setattr(main, 'DB_PATH', legacy)
    main.init_db()
    main.init_db()  # booting twice must not add the column twice or fail

    con = sqlite3.connect(legacy)
    con.row_factory = sqlite3.Row
    assert 'preview_url' in {row['name'] for row in con.execute('PRAGMA table_info(projects)')}
    row = con.execute("SELECT title, preview_url FROM projects WHERE slug='legacy-project'").fetchone()
    con.close()
    assert (row['title'], row['preview_url']) == ('Old Row', '')


def load_script_db(tmp_path, slug='preview-me', address='http://localhost:5173'):
    """Write a tiny database shaped like the app's so the script is tested against real rows."""
    db = tmp_path / 'univorya.sqlite3'
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE projects (id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL, preview_url TEXT NOT NULL DEFAULT '')""")
    con.execute('INSERT INTO projects(slug,title,preview_url) VALUES(?,?,?)', (slug, 'Preview Me', address))
    con.commit()
    con.close()
    return db


def test_the_script_reads_the_stored_address_out_of_sqlite(tmp_path):
    db = load_script_db(tmp_path)
    assert showcase.preview_address(db, 'preview-me') == ('Preview Me', 'http://localhost:5173')
    with pytest.raises(LookupError, match='No project'):
        showcase.preview_address(db, 'someone-elses-slug')
    with pytest.raises(LookupError, match='No Univorya database'):
        showcase.preview_address(tmp_path / 'missing.sqlite3', 'preview-me')


def test_the_script_reports_a_database_that_predates_the_column(tmp_path):
    db = tmp_path / 'univorya.sqlite3'
    con = sqlite3.connect(db)
    con.execute('CREATE TABLE projects (slug TEXT, title TEXT)')
    con.execute("INSERT INTO projects VALUES('preview-me','Preview Me')")
    con.commit()
    con.close()
    with pytest.raises(LookupError, match='predates local preview'):
        showcase.preview_address(db, 'preview-me')


@pytest.mark.parametrize('address,allowed', [
    ('http://localhost:5173', True), ('http://127.0.0.1:8080/app', True), ('http://[::1]:3000', True),
    ('https://LOCALHOST', True), ('http://localhost@evil.com', False), ('http://example.com', False),
    ('http://192.168.0.9:3000', False), ('file:///etc/passwd', False), ('', False),
])
def test_the_script_only_treats_loopback_as_local(address, allowed):
    assert showcase.is_loopback(address) is allowed


def test_the_script_checks_that_the_dev_server_is_actually_up():
    probe = socket.socket()
    probe.bind(('127.0.0.1', 0))
    probe.listen(1)
    port = probe.getsockname()[1]
    assert showcase.listening(f'http://127.0.0.1:{port}') is True
    probe.close()
    assert showcase.listening('http://127.0.0.1:1') is False
    assert showcase.listening('http://no-such-host.invalid:80') is False


def capture_launch(monkeypatch, reachable=True):
    calls = []
    monkeypatch.setattr(showcase, 'listening', lambda url, timeout=1.0: reachable)
    monkeypatch.setattr(showcase, 'browser_binary', lambda explicit=None: '/usr/bin/google-chrome')
    monkeypatch.setattr(showcase.subprocess, 'Popen', lambda argv, **kw: calls.append(argv))
    return calls


def test_the_script_opens_the_address_in_its_own_chrome_window(tmp_path, monkeypatch, capsys):
    load_script_db(tmp_path)
    calls = capture_launch(monkeypatch)
    assert showcase.main(['preview-me', '--data-dir', str(tmp_path)]) == 0
    assert len(calls) == 1
    argv = calls[0]
    assert '--app=http://localhost:5173' in argv
    assert any(arg.startswith('--user-data-dir=') for arg in argv)
    assert 'Preview Me' in capsys.readouterr().out


def test_the_script_prints_instead_of_launching_when_asked(tmp_path, monkeypatch, capsys):
    load_script_db(tmp_path)
    calls = capture_launch(monkeypatch)
    assert showcase.main(['preview-me', '--data-dir', str(tmp_path), '--print']) == 0
    assert calls == []
    assert capsys.readouterr().out.strip() == 'http://localhost:5173'


def test_the_script_refuses_a_dead_server_and_a_public_address(tmp_path, monkeypatch, capsys):
    load_script_db(tmp_path)
    capture_launch(monkeypatch, reachable=False)
    assert showcase.main(['preview-me', '--data-dir', str(tmp_path)]) == 1
    assert 'Nothing is listening' in capsys.readouterr().err

    public = tmp_path / 'public'
    public.mkdir()
    load_script_db(public, slug='public-me', address='https://example.com')
    assert showcase.main(['public-me', '--data-dir', str(public)]) == 1
    assert 'does not point at this machine' in capsys.readouterr().err

    blank = tmp_path / 'blank'
    blank.mkdir()
    load_script_db(blank, slug='blank-me', address='')
    assert showcase.main(['blank-me', '--data-dir', str(blank)]) == 1
    assert 'no local preview address' in capsys.readouterr().err


def test_the_client_only_shows_the_field_and_the_button_to_the_owner():
    source = (STATIC_DIR / 'app.js').read_text()
    assert 'name="preview_url"' in source, 'the publish form must carry the address'
    assert 'p.is_owner&&p.preview_url' in source, 'the preview box is owner-only markup'
    assert 'preview_url:p.preview_url' in source, 'the edit page must prefill it'
    assert "action==='open-local-preview'" in source
    assert 'w.opener=null' in source, 'the opened window must not keep a handle on Univorya'
    styles = (STATIC_DIR / 'styles.css').read_text()
    assert '.local-preview-box' in styles
