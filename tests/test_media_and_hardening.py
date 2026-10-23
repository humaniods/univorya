"""Coverage for the upload pipeline, per-category persistence and the audit hardening fixes."""
import importlib
import io
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from PIL.TiffImagePlugin import IFDRational

STATIC_DIR = Path(__file__).resolve().parent.parent / 'static'


def png(width=600, height=400, color=(120, 90, 220)):
    buf = io.BytesIO()
    Image.new('RGB', (width, height), color).save(buf, 'PNG')
    buf.seek(0)
    return buf


def gif(frames=4, size=(480, 320)):
    buf = io.BytesIO()
    images = [Image.new('RGB', size, (i * 45 % 255, 90, 170)) for i in range(frames)]
    images[0].save(buf, 'GIF', save_all=True, append_images=images[1:], duration=120, loop=0)
    buf.seek(0)
    return buf


def fields(**kwargs):
    base = {'title': 'Uploaded Gallery Project', 'tagline': 'A project that carries several screenshots with it.',
            'description': 'Description long enough to satisfy the showcase rules and to keep the fixture honest.',
            'category': 'Web App', 'stage': 'Building', 'tags': 'a, b', 'tech': 'FastAPI', 'cover_style': 'violet'}
    base.update(kwargs)
    return base


@pytest.fixture
def c(tmp_path, monkeypatch):
    monkeypatch.setenv('UNIVORYA_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('UNIVORYA_SEED_DEMO', '0')
    from app import main
    importlib.reload(main)
    with TestClient(main.app) as client:
        client.data_dir = tmp_path
        yield client


def signup(client, username='gallery_owner'):
    return client.post('/api/auth/register', json={
        'name': 'Gallery Owner', 'username': username, 'email': username + '@example.com',
        'password': 'Correct-Horse-59'})


def publish(client, cover=True, shots=2, **overrides):
    files = []
    if cover:
        files.append(('cover', ('cover.png', png(), 'image/png')))
    for i in range(shots):
        files.append(('screenshots', (f'shot{i}.png', png(700, 500, (i * 60, 120, 200)), 'image/png')))
    return client.post('/api/projects', data=fields(**overrides),
                       files=files or None) if files else client.post('/api/projects', data=fields(**overrides))


def test_every_category_persists_project_and_uploads(c):
    assert signup(c).status_code == 201
    categories = c.get('/api/metadata').json()['categories']
    assert len(categories) == 8
    for category in categories:
        created = publish(c, category=category, stage='Beta', title='Showcase ' + category)
        assert created.status_code == 201, created.text
        project = c.get('/api/projects/' + created.json()['slug']).json()['project']
        assert project['category'] == category
        assert project['cover_image'].startswith('/uploads/')
        assert [m['kind'] for m in project['media']] == ['cover', 'screenshot', 'screenshot']
        assert project['media_count'] == 3
        assert project['media'][1]['width'] == 700 and project['media'][1]['height'] == 500
    listed = c.get('/api/projects?limit=60').json()['projects']
    assert len(listed) == 8
    assert all(p['media'] for p in listed), 'list endpoint must carry media for every card'


def test_each_stored_upload_reports_the_row_behind_it(c):
    assert signup(c).status_code == 201
    media = c.get('/api/projects/' + publish(c).json()['slug']).json()['project']['media']
    assert len(media) == 3
    for item in media:
        # The tile is only honest about an upload if it carries the timestamp of the row it came from.
        stored = datetime.fromisoformat(item['uploaded_at'])
        assert stored.tzinfo is not None and stored <= datetime.now(timezone.utc), item
    source = (STATIC_DIR / 'app.js').read_text()
    assert 'formatDate(item.uploaded_at)' in source, 'the creator must see when their own file was stored'


def test_metadata_advertises_the_limits_the_client_validates_against(c):
    meta = c.get('/api/metadata').json()
    assert meta['limits'] == {'max_file_mb': 5, 'max_total_mb': 25, 'max_media': 8,
                              'min_width': 200, 'min_height': 150}
    assert meta['cover_styles'] == ['violet', 'blue', 'orange', 'mint', 'pink', 'slate']


def test_tiny_images_are_rejected_instead_of_being_upscaled(c):
    signup(c)
    response = c.post('/api/projects', data=fields(), files={'cover': ('tiny.png', png(60, 40), 'image/png')})
    assert response.status_code == 422
    assert '200 × 150' in response.json()['detail']
    assert list((c.data_dir / 'uploads').iterdir()) == [], 'rejected upload must leave no file behind'


def test_a_re_encoded_image_loses_the_camera_and_location_metadata(c):
    """Phone exports carry owner and GPS data; the gallery must not publish it."""
    signup(c)
    exif = Image.new('RGB', (900, 600)).getexif()
    exif[271], exif[272], exif[305], exif[306] = 'SecretCameraModel', 'Owner Phone', 'SomePhone OS 9.9', '2026:10:22'
    exif[282] = IFDRational(50, 1)
    gps = exif.get_ifd(0x8825)
    gps[1], gps[3] = 'N', 'E'
    gps[2] = (IFDRational(30, 1), IFDRational(18, 1), IFDRational(0, 1))
    gps[4] = (IFDRational(75, 1), IFDRational(52, 1), IFDRational(0, 1))
    buf = io.BytesIO()
    Image.new('RGB', (900, 600), (20, 190, 140)).save(buf, 'JPEG', exif=exif)
    source = buf.getvalue()
    assert b'SecretCameraModel' in source, 'the fixture stopped carrying the tag it is meant to test'

    slug = c.post('/api/projects', data=fields(),
                  files={'cover': ('phone-export.jpg', source, 'image/jpeg')}).json()['slug']
    stored = c.get(c.get('/api/projects/' + slug).json()['project']['cover_image'])
    assert stored.status_code == 200 and stored.headers['content-type'] == 'image/webp'
    for marker in (b'SecretCameraModel', b'Owner Phone', b'SomePhone', b'2026:10:22'):
        assert marker not in stored.content, f'{marker} survived the re-encode'
    # Shrunk for the grid, never stretched beyond what the creator uploaded.
    image = Image.open(io.BytesIO(stored.content))
    assert image.size == (900, 600)


def test_animated_gif_keeps_its_frames(c):
    signup(c)
    created = c.post('/api/projects', data=fields(), files={'cover': ('anim.gif', gif(), 'image/gif')})
    assert created.status_code == 201
    project = c.get('/api/projects/' + created.json()['slug']).json()['project']
    assert project['media'][0]['animated'] is True
    stored = c.get(project['cover_image'])
    assert stored.headers['content-type'] == 'image/webp'
    image = Image.open(io.BytesIO(stored.content))
    assert image.format == 'WEBP' and image.n_frames > 1


def test_an_animation_cannot_ask_for_more_pixels_than_one_upload_may_decode(c):
    """Pillow only caps pixels per frame, so the frame budget has to be checked as a whole."""
    signup(c)
    response = c.post('/api/projects', data=fields(),
                      files={'cover': ('heavy.gif', gif(frames=40, size=(1200, 900)), 'image/gif')})
    assert response.status_code == 422
    assert 'too many pixels' in response.json()['detail']
    assert list((c.data_dir / 'uploads').iterdir()) == []


def test_screenshot_cap_and_mixed_rejection(c):
    signup(c)
    response = c.post('/api/projects', data=fields(),
                      files=[('screenshots', (f'{i}.png', png(), 'image/png')) for i in range(9)])
    assert response.status_code == 422 and '8 screenshots' in response.json()['detail']
    # One bad frame among good ones must not leave the good ones orphaned.
    response = c.post('/api/projects', data=fields(),
                      files=[('cover', ('ok.png', png(), 'image/png')),
                             ('screenshots', ('good.png', png(), 'image/png')),
                             ('screenshots', ('bad.png', io.BytesIO(b'not an image'), 'image/png'))])
    assert response.status_code == 422
    assert list((c.data_dir / 'uploads').iterdir()) == []


def test_editing_media_removes_only_what_the_editor_drops(c):
    signup(c)
    slug = publish(c).json()['slug']
    media = c.get('/api/projects/' + slug).json()['project']['media']
    shots = [m['id'] for m in media if m['kind'] == 'screenshot']
    before = {p.name for p in (c.data_dir / 'uploads').iterdir()}

    kept = shots[0]
    response = c.put('/api/projects/' + slug, data=fields(stage='Beta', keep_media=str(kept)),
                     files={'screenshots': ('new.png', png(500, 400), 'image/png')})
    assert response.status_code == 200
    after = c.get('/api/projects/' + slug).json()['project']['media']
    assert [m['id'] for m in after if m['kind'] == 'screenshot'] == [kept, max(shots) + 1]
    assert len(after) == 3 and after[0]['kind'] == 'cover'
    files = {p.name for p in (c.data_dir / 'uploads').iterdir()}
    assert len(before - files) == 1, 'exactly the dropped screenshot file should be gone'
    assert len(files) == 3

    # A text-only edit must never wipe the gallery.
    assert c.put('/api/projects/' + slug, data=fields(title='Renamed Without Touching Media')).status_code == 200
    assert c.get('/api/projects/' + slug).json()['project']['media_count'] == 3


def test_cover_replacement_leaves_no_orphan_files(c):
    signup(c)
    slug = publish(c, shots=0).json()['slug']
    old = c.get('/api/projects/' + slug).json()['project']['cover_image'].split('/')[-1]
    assert c.put('/api/projects/' + slug, data=fields(),
                 files={'cover': ('second.png', png(800, 600), 'image/png')}).status_code == 200
    files = {p.name for p in (c.data_dir / 'uploads').iterdir()}
    assert old not in files and len(files) == 1


def test_deleting_a_project_removes_every_uploaded_file(c):
    signup(c)
    slug = publish(c, shots=3).json()['slug']
    assert len(list((c.data_dir / 'uploads').iterdir())) == 4
    assert c.delete('/api/projects/' + slug).status_code == 200
    assert list((c.data_dir / 'uploads').iterdir()) == []
    assert c.get('/api/projects/' + slug).status_code == 404


def test_invalid_cover_style_is_rejected_on_both_verbs(c):
    signup(c)
    assert c.post('/api/projects', data=fields(cover_style='chartreuse')).status_code == 422
    slug = publish(c, shots=0).json()['slug']
    assert c.put('/api/projects/' + slug, data=fields(cover_style='chartreuse')).status_code == 422


def test_reserved_usernames_and_weak_passwords_are_blocked(c):
    for username in ('admin', 'univorya', 'explore', 'makers'):
        response = c.post('/api/auth/register', json={'name': 'Someone Else', 'username': username,
                                                      'email': username + '@example.com', 'password': 'Correct-Horse-59'})
        assert response.status_code == 422 and 'reserved' in response.json()['detail']
    for password in ('password', '12345678', 'aaaaaaaa', '    '):
        response = c.post('/api/auth/register', json={'name': 'Weak Wallet', 'username': 'weakwallet',
                                                      'email': 'weak@example.com', 'password': password})
        assert response.status_code == 422, password
    assert c.post('/api/auth/register', json={'name': 'Solid Person', 'username': 'solidperson',
                                             'email': 'solid@example.com', 'password': 'Correct-Horse-59'}).status_code == 201


def test_invisible_bidirectional_controls_never_reach_storage(c):
    signup(c)
    title = 'Innocent‮evil‬ Project'
    created = c.post('/api/projects', data=fields(title=title, tagline='A tagline with invisible controls'))
    assert created.status_code == 201, created.text
    stored = c.get('/api/projects/' + created.json()['slug']).json()['project']['title']
    assert '‮' not in stored and '‬' not in stored
    assert stored == 'Innocentevil Project'


def test_tag_dedupe_ignores_case(c):
    signup(c)
    slug = c.post('/api/projects', data=fields(tags='React, react, REACT, vue, vue')).json()['slug']
    assert c.get('/api/projects/' + slug).json()['project']['tags'] == ['React', 'vue']


def test_maker_profile_resolves_or_404s(c):
    signup(c, username='real_maker')
    slug = publish(c, shots=0).json()['slug']
    c.get('/api/projects/' + slug)
    maker = c.get('/api/makers/real_maker')
    assert maker.status_code == 200
    assert maker.json()['maker']['project_count'] == 1
    assert maker.json()['maker']['views'] >= 1
    assert 'password_hash' not in maker.text
    assert c.get('/api/makers/nobody_here').status_code == 404
    assert c.get('/api/makers/REAL_MAKER').status_code == 200


def test_upload_and_route_content_types(c):
    assert c.get('/app.js').headers['content-type'].startswith(('text/javascript', 'application/javascript'))
    assert c.get('/static/app.js').status_code == 404
    sneaky = c.get('/uploads/%2e%2e%2f%2e%2e%2fetc%2fpasswd')
    assert sneaky.status_code == 404
    assert 'root:' not in c.get('/uploads/../../etc/passwd').text
    assert c.get('/api/health').status_code == 200


def test_trailing_slash_pages_redirect_to_the_canonical_url(c):
    signup(c)
    slug = publish(c, shots=0).json()['slug']
    assert c.get('/api/projects/' + slug).status_code == 200
    response = c.get('/p/' + slug + '/', follow_redirects=False)
    assert response.status_code == 307
    assert response.headers['location'] == '/p/' + slug
    assert c.get('/p/' + slug + '/').status_code == 200


def test_a_double_slash_cannot_make_the_canonical_redirect_leave_our_origin(c):
    """/<path>/ redirects to /<path> — that answer must never start with //, or it is an open redirect."""
    for path in ('//evil.example/', '///evil.example/', '//p/slug/', '/explore/'):
        response = c.get('http://univorya.test' + path, follow_redirects=False)
        assert response.status_code == 307, (path, response.status_code)
        location = response.headers['location']
        assert not location.startswith('//'), (path, location)
        assert not location.lower().startswith(('http://', 'https://')), (path, location)
    assert c.get('http://univorya.test//evil.example/', follow_redirects=False).headers['location'] == '/evil.example'


def test_pagination_beyond_the_last_page_stays_sane(c):
    signup(c)
    for i in range(3):
        publish(c, shots=0, title='Paged Project Number ' + str(i))
    first = c.get('/api/projects?limit=2&page=1').json()['pagination']
    assert first == {'page': 1, 'limit': 2, 'total': 3, 'pages': 2}
    assert c.get('/api/projects?limit=2&page=2').json()['projects']
    assert c.get('/api/projects?limit=2&page=3').json()['projects'] == []
    assert c.get('/api/projects?page=0').status_code == 422
    assert c.get('/api/projects?limit=61').status_code == 422


def test_search_escapes_like_wildcards_and_survives_unicode(c):
    signup(c)
    publish(c, shots=0, title='Percent % Project', tags='日本語, Café')
    assert c.get('/api/projects', params={'q': '%'}).json()['pagination']['total'] == 1
    assert c.get('/api/projects', params={'q': '_'}).json()['pagination']['total'] == 0
    # Tags are stored as real unicode, so a Japanese or accented query finds the card.
    assert c.get('/api/projects', params={'q': '日本語'}).json()['pagination']['total'] == 1
    assert c.get('/api/projects', params={'q': 'Café'}).json()['pagination']['total'] == 1


def test_cache_policy_matches_how_often_a_url_can_change(c):
    """Un-hashed code and HTML revalidate; content-addressed files do not need to."""
    signup(c)
    slug = publish(c).json()['slug']
    cover = c.get('/api/projects/' + slug).json()['project']['cover_image']
    fonts = sorted(f.name for f in (STATIC_DIR / 'fonts').glob('*.woff2'))
    assert fonts, 'self-hosted fonts disappeared from static/fonts'
    cases = {
        '/': 'no-cache', '/app.js': 'no-cache', '/styles.css': 'no-cache', '/logo.svg': 'no-cache',
        '/explore': 'no-cache', '/dashboard': 'no-cache', '/p/' + slug: 'no-cache',
        '/api/projects': 'no-store', '/api/health': 'no-store', '/api/metadata': 'no-store',
        '/api/projects/' + slug: 'no-store', cover: 'immutable', '/fonts/' + fonts[0]: 'immutable',
    }
    for path, want in cases.items():
        response = c.get(path)
        assert response.status_code == 200, (path, response.status_code)
        value = response.headers.get('cache-control', '')
        assert want in value, (path, value)


def test_missing_assets_404_instead_of_answering_with_the_app_shell(c):
    """A 200 text/html for a typo'd asset URL gets cached by the browser as if it were the file."""
    for path in ('/does-not-exist.js', '/styles.missing.css', '/fonts/ghost.woff2',
                 '/images/ghost.png', '/uploads/ghost.webp', '/nope.html'):
        response = c.get(path)
        assert response.status_code == 404, (path, response.status_code, response.text[:80])
        assert 'id="app"' not in response.text, path
    # Extension-less paths are client routes, so the shell is right and the SPA renders its own 404.
    shell = c.get('/a-route-the-client-handles')
    assert shell.status_code == 200 and 'id="app"' in shell.text
    assert shell.headers['cache-control'] == 'no-cache'


def test_client_ships_the_fixes_the_audit_asked_for():
    root = __import__('pathlib').Path(__file__).resolve().parent.parent
    source = (root / 'static' / 'app.js').read_text()
    assert "$('.project-grid'," not in source, 'load-more must not pass a selector string as the search root'
    assert "$('#catalog-results .project-grid')" in source
    stylesheet = (root / 'static' / 'styles.css').read_text()
    assert '@keyframes' in stylesheet, 'the motion layer must exist'
    assert 'prefers-reduced-motion' in stylesheet
    assert "@import url('https://" not in stylesheet, 'fonts must stay on the same origin'
    assert 'else notFoundPage(path);' in source, 'unknown routes must render a real not-found page'
    assert 'else {readFiltersFromUrl();explore();}' not in source, 'the router must not swallow unknown routes'
    assert 'Nothing lives at this address' in source
    # script-src 'self' rejects inline handlers, so every button has to go through the delegated one.
    assert 'onclick=' not in source, 'a CSP-compliant client cannot rely on inline click handlers'
    assert "action==='retry-boot'" in source, 'the retry button on the backend-down screen must be wired up'
    assert 'const dec =' in source, 'path segments must be decoded without throwing on a bad %xx'
