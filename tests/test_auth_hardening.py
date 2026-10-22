"""Brute-force defenses: per-account and per-client login caps, signup cost, session handling.

Each fixture reloads the app module, so the in-process attempt buckets start empty and
one test can never trip another test's throttle.
"""
import importlib
import io
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image


@pytest.fixture
def c(tmp_path, monkeypatch):
    monkeypatch.setenv('UNIVORYA_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('UNIVORYA_SEED_DEMO', '0')
    from app import main
    importlib.reload(main)
    with TestClient(main.app) as client:
        client.main = main
        client.data_dir = tmp_path
        yield client


def signup(client, username='throttle_user', password='Correct-Horse-59'):
    return client.post('/api/auth/register', json={
        'name': 'Throttle Tester', 'username': username,
        'email': username + '@example.com', 'password': password})


def png(width=640, height=440, color=(120, 90, 220)):
    buf = io.BytesIO()
    Image.new('RGB', (width, height), color).save(buf, 'PNG')
    return buf.getvalue()


def fields(**overrides):
    base = {'title': 'Throttled Gallery Project', 'tagline': 'A project that carries screenshots with it.',
            'description': 'Description long enough to satisfy the showcase rules and keep the fixture honest.',
            'category': 'Web App', 'stage': 'Building', 'cover_style': 'violet'}
    base.update(overrides)
    return base


def publish(client, shots=3, **overrides):
    files = [('cover', ('cover.png', png(), 'image/png'))]
    files += [('screenshots', (f's{i}.png', png(700, 500, (i * 60, 120, 200)), 'image/png'))
              for i in range(shots)]
    response = client.post('/api/projects', data=fields(**overrides), files=files)
    assert response.status_code == 201, response.text
    return response.json()['slug']


def test_repeated_wrong_passwords_lock_the_account_for_the_window(c):
    signup(c)
    for attempt in range(c.main.LOGIN_ACCOUNT_LIMIT):
        wrong = c.post('/api/auth/login', json={'email': 'throttle_user@example.com', 'password': 'nope-pass-1'})
        assert wrong.status_code == 401, (attempt, wrong.text)
    blocked = c.post('/api/auth/login', json={'email': 'throttle_user@example.com', 'password': 'Correct-Horse-59'})
    assert blocked.status_code == 429, blocked.text
    assert 'Too many attempts' in blocked.text


def test_cycling_through_accounts_hits_the_per_client_cap(c):
    """A leaked password list tries many accounts; the shared client bucket must catch it."""
    for index in range(c.main.LOGIN_CLIENT_LIMIT):
        probe = c.post('/api/auth/login', json={'email': f'ghost{index}@example.com', 'password': 'wrong-pass-1'})
        assert probe.status_code == 401, (index, probe.text)
    stuffed = c.post('/api/auth/login', json={'email': 'another@example.com', 'password': 'wrong-pass-1'})
    assert stuffed.status_code == 429, stuffed.text


def test_failed_logins_do_not_block_signing_up(c):
    for index in range(c.main.LOGIN_ACCOUNT_LIMIT):
        c.post('/api/auth/login', json={'email': f'ghost{index}@example.com', 'password': 'wrong-pass-1'})
    assert signup(c, 'fresh_from_throttle').status_code == 201


def test_registration_cost_is_capped_per_client(c):
    """Every signup pays for a scrypt hash, so anonymous signups need a ceiling of their own."""
    for index in range(c.main.SIGNUP_CLIENT_LIMIT):
        assert signup(c, f'signup_{index:02d}').status_code == 201, index
    blocked = signup(c, 'signup_over_cap')
    assert blocked.status_code == 429, blocked.text


def test_duplicate_registrations_count_against_the_cap(c):
    signup(c, 'taken_username')
    for index in range(c.main.SIGNUP_CLIENT_LIMIT - 1):
        clash = c.post('/api/auth/register', json={
            'name': 'Clash', 'username': 'taken_username',
            'email': f'clash{index}@example.com', 'password': 'Correct-Horse-59'})
        assert clash.status_code == 409, (index, clash.text)
    assert signup(c, 'signup_after_clashes').status_code == 429


def test_unknown_session_cookie_reads_as_anonymous_and_writes_as_unauthorized(c):
    signup(c)
    publish(c)
    c.post('/api/auth/logout')
    smuggled = TestClient(c.app, cookies={'univorya_session': 'deadbeef' * 8})
    anonymous = smuggled.get('/api/auth/me')
    assert anonymous.status_code == 200 and anonymous.json()['user'] is None
    assert smuggled.get('/api/projects').status_code == 200
    assert smuggled.post('/api/projects', data=fields()).status_code == 401
    assert smuggled.put('/api/projects/anything', data=fields()).status_code == 401
    assert smuggled.delete('/api/projects/anything').status_code == 401


def test_sign_in_asks_before_it_parses_the_body(c):
    """Validation errors are a schema map; an anonymous caller should not get one for free."""
    assert c.post('/api/projects', data={}).status_code == 401
    assert c.put('/api/projects/anything', data={}).status_code == 401
    assert c.post('/api/projects', json={'title': 1}).status_code == 401


def test_expired_sessions_stop_working(c):
    import hashlib
    signup(c)
    token = c.cookies.get('univorya_session')
    assert token
    with c.main.db() as con:
        con.execute('UPDATE sessions SET expires=? WHERE token_hash=?',
                    (int(time.time()) - 1, hashlib.sha256(token.encode()).hexdigest()))
    assert c.get('/api/auth/me').json()['user'] is None
    assert c.delete('/api/projects/whatever').status_code == 401


def test_logout_destroys_the_stored_token(c):
    signup(c)
    token = c.cookies.get('univorya_session')
    c.post('/api/auth/logout')
    assert c.cookies.get('univorya_session') is None
    with c.main.db() as con:
        assert con.execute('SELECT 1 FROM sessions').fetchone() is None
    assert TestClient(c.app, cookies={'univorya_session': token}).get('/api/auth/me').json()['user'] is None


def test_wrong_password_and_unknown_account_answer_identically(c):
    signup(c)
    known = c.post('/api/auth/login', json={'email': 'throttle_user@example.com', 'password': 'nope-pass-1'})
    unknown = c.post('/api/auth/login', json={'email': 'nobody@example.com', 'password': 'nope-pass-1'})
    assert known.status_code == unknown.status_code == 401
    assert known.json() == unknown.json()


def test_only_the_allowed_image_formats_are_accepted(c):
    signup(c)
    buf = io.BytesIO()
    Image.new('RGB', (640, 440), (90, 60, 200)).save(buf, 'GIF', save_all=True,
                                                     append_images=[Image.new('RGB', (640, 440))], duration=80, loop=0)
    animated_gif = buf.getvalue()
    buf.seek(0)
    Image.new('RGB', (640, 440), (90, 60, 200)).save(buf, 'WEBP', save_all=True,
                                                     append_images=[Image.new('RGB', (640, 440))], duration=80, loop=0)
    animated_webp = buf.getvalue()
    accepted = {
        'PNG': png(),
        'JPEG': (lambda b: (Image.new('RGB', (640, 440), (90, 60, 200)).save(b, 'JPEG'), b.getvalue())[1])(io.BytesIO()),
        'grayscale PNG': (lambda b: (Image.new('L', (640, 440), 180).save(b, 'PNG'), b.getvalue())[1])(io.BytesIO()),
        'RGBA PNG': (lambda b: (Image.new('RGBA', (640, 440), (90, 60, 200, 255)).save(b, 'PNG'), b.getvalue())[1])(io.BytesIO()),
        'palette PNG': (lambda b: (Image.new('RGB', (640, 440), (90, 60, 200)).convert('P').save(b, 'PNG'), b.getvalue())[1])(io.BytesIO()),
        'animated GIF': animated_gif,
        'animated WebP': animated_webp,
    }
    refused = {
        'SVG': b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        'BMP': (lambda b: (Image.new('RGB', (640, 440), (90, 60, 200)).save(b, 'BMP'), b.getvalue())[1])(io.BytesIO()),
        'TIFF': (lambda b: (Image.new('RGB', (640, 440), (90, 60, 200)).save(b, 'TIFF'), b.getvalue())[1])(io.BytesIO()),
        'ICO': (lambda b: (Image.new('RGB', (640, 440), (90, 60, 200)).save(b, 'ICO'), b.getvalue())[1])(io.BytesIO()),
        'garbage': b'\x00\x01\x02\x03not an image at all',
        'html': b'<html><body>hi</body></html>' * 40,
        'empty': b'',
    }
    for label, blob in accepted.items():
        response = c.post('/api/projects', data=fields(title='Format ' + label),
                          files=[('cover', ('cover.bin', blob, 'application/octet-stream'))])
        assert response.status_code == 201, (label, response.text)
    for label, blob in refused.items():
        response = c.post('/api/projects', data=fields(title='Bad ' + label),
                          files=[('cover', ('cover.bin', blob, 'application/octet-stream'))])
        assert response.status_code == 422, (label, response.text)


def test_multi_image_upload_roundtrips_and_edits(c):
    """The gallery is the product: cover plus screenshots must survive listing, editing and download."""
    signup(c)
    slug = publish(c, shots=4)
    listed = c.get('/api/projects', params={'limit': 60}).json()['projects'][0]
    assert [m['kind'] for m in listed['media']] == ['cover'] + ['screenshot'] * 4
    assert [m['file'] for m in listed['media']] == [m['file'] for m in c.get(f'/api/projects/{slug}').json()['project']['media']]
    for item in listed['media']:
        fetched = c.get(item['file'])
        assert fetched.status_code == 200, item['file']
        assert fetched.headers['content-type'] == 'image/webp'

    detail = c.get('/api/projects/' + slug).json()['project']
    keep = [m['id'] for m in detail['media'] if m['kind'] == 'screenshot'][1:3]
    edited = c.put('/api/projects/' + slug, data=fields(keep_media=','.join(map(str, keep))),
                   files=[('screenshots', ('new.png', png(660, 460, (10, 200, 120)), 'image/png'))])
    assert edited.status_code == 200, edited.text
    after = c.get('/api/projects/' + slug).json()['project']
    assert [m['kind'] for m in after['media']] == ['cover', 'screenshot', 'screenshot', 'screenshot']
    survivors = {m['id'] for m in after['media'] if m['kind'] == 'screenshot'}
    assert set(keep) <= survivors and len(survivors - set(keep)) == 1, survivors
    files = [m['file'] for m in after['media']]
    assert len(set(files)) == 4, files
    assert {f.name for f in (c.data_dir / 'uploads').glob('*')} == {f.rsplit('/', 1)[1] for f in files}

    text_only = c.put('/api/projects/' + slug, data=fields(title='Renamed Without Touching Media'))
    assert text_only.status_code == 200
    assert [m['id'] for m in c.get('/api/projects/' + slug).json()['project']['media']] == \
           [m['id'] for m in after['media']]

    cleared = c.put('/api/projects/' + slug, data=fields(title='Cover Only Now', keep_media=''))
    assert cleared.status_code == 200
    assert [m['kind'] for m in c.get('/api/projects/' + slug).json()['project']['media']] == ['cover']
    assert len(list((c.data_dir / 'uploads').glob('*'))) == 1


def test_keep_media_from_another_project_is_inert(c):
    signup(c, 'owner_one')
    victim = publish(c, shots=2)
    signup(c, 'owner_two')
    mine = publish(c, shots=1)
    foreign = c.get('/api/projects/' + victim).json()['project']['media'][2]['id']
    tampered = c.put('/api/projects/' + mine, data=fields(keep_media=str(foreign)))
    assert tampered.status_code == 200, tampered.text
    untouched = c.get('/api/projects/' + victim).json()['project']
    assert len(untouched['media']) == 3
    assert c.get(untouched['media'][2]['file']).status_code == 200
