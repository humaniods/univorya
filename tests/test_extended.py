"""Regression coverage for project editing, validation, security, and discovery."""
import importlib
import io
import os

import pytest
from fastapi.testclient import TestClient
from PIL import Image


def cover(color=(111, 44, 225)):
    image = Image.new('RGB', (300, 160), color)
    file = io.BytesIO()
    image.save(file, 'PNG')
    return file.getvalue()


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
        'name': 'Project Author', 'username': username, 'email': username+'@example.com', 'password': 'a-good-password-45321'
    })


def project(**kwargs):
    fields = {'title': 'Creative Real Project', 'tagline': 'An independent project showcase for everyone.',
              'description': 'A genuine developer project with a practical implementation and a public showcase, created for regression testing.',
              'category': 'Web App', 'stage': 'Building', 'tags':'web, open source', 'tech':'FastAPI, JavaScript',
              'cover_style':'violet', 'repo_url':'https://github.com/example/example', 'demo_url':'', 'website_url':'', 'license':'MIT'}
    fields.update(kwargs)
    return fields


def test_branding_and_security_headers(c):
    page = c.get('/')
    assert 'Univorya' in page.text
    assert c.get('/logo.svg').status_code == 200
    assert 'univorya' in c.get('/app.js').text.lower()
    assert 'script-src' in page.headers['content-security-policy']
    assert page.headers['x-frame-options'] == 'DENY'
    assert c.get('/api/auth/me').headers['cache-control'] == 'no-store'
    assert c.get('/api/health').json()['service'] == 'univorya'


def test_idea_without_code_and_search_and_pagination(c):
    assert signup(c).status_code == 201
    for i in range(3):
        r = c.post('/api/projects', data=project(title='My Idea '+str(i), category='Idea', stage='Idea', repo_url=''))
        assert r.status_code == 201, r.text
    all_projects = c.get('/api/projects?limit=2&page=1').json()
    assert all_projects['pagination']['pages'] == 2
    assert len(all_projects['projects']) == 2
    assert len(c.get('/api/projects?limit=2&page=2').json()['projects']) == 1
    assert c.get('/api/projects?category=Idea&stage=Idea').json()['pagination']['total'] == 3
    assert c.get('/api/projects?q=My%20Idea').json()['pagination']['total'] == 3
    assert c.get('/api/projects?limit=61').status_code == 422
    assert c.get('/api/projects?stage=imaginary').status_code == 422


def test_edit_preserves_link_and_updates_public_profile(c):
    assert signup(c).status_code == 201
    slug = c.post('/api/projects', data=project(), files={'cover':('c.png',cover(),'image/png')}).json()['slug']
    before = c.get('/api/projects/'+slug).json()['project']
    assert before['cover_image'].startswith('/uploads/')
    changed = c.put('/api/projects/'+slug, data=project(title='Creative Project Updated', category='AI & ML',
                              stage='Launched', tagline='A complete public AI product for makers worldwide.',
                              tech='Python, AI', repo_url=''))
    assert changed.status_code == 200, changed.text
    assert changed.json()['slug'] == slug
    after = c.get('/api/projects/'+slug).json()['project']
    assert after['title'] == 'Creative Project Updated'
    assert after['category'] == 'AI & ML'
    assert after['stage'] == 'Launched'
    assert after['tech'] == ['Python','AI']
    assert after['repo_url'] == ''
    assert after['cover_image'] == before['cover_image']
    assert after['updated_at'] >= before['updated_at']
    assert c.get('/api/projects?creator=alpha').json()['pagination']['total'] == 1


def test_edit_image_replacement_and_cleanup(c):
    assert signup(c).status_code == 201
    slug = c.post('/api/projects', data=project(), files={'cover':('old.png',cover(),'image/png')}).json()['slug']
    old = c.get('/api/projects/'+slug).json()['project']['cover_image']
    r = c.put('/api/projects/'+slug, data=project(), files={'cover':('new.png',cover((1,188,94)),'image/png')})
    assert r.status_code == 200, r.text
    new = c.get('/api/projects/'+slug).json()['project']['cover_image']
    assert new != old
    assert c.get(new).status_code == 200
    assert c.get(old).status_code == 404


def test_only_owner_can_edit(c):
    assert signup(c).status_code == 201
    slug = c.post('/api/projects', data=project()).json()['slug']
    assert c.post('/api/auth/logout').status_code == 200
    assert c.put('/api/projects/'+slug, data=project()).status_code == 401
    assert signup(c,'beta').status_code == 201
    assert c.put('/api/projects/'+slug,data=project(title='I should not edit')).status_code == 403
    assert c.put('/api/projects/missing',data=project()).status_code == 404
    assert c.get('/api/projects/'+slug).json()['project']['title'] == 'Creative Real Project'


def test_edit_validation_rejects_bad_inputs_unchanged(c):
    signup(c)
    slug = c.post('/api/projects', data=project()).json()['slug']
    assert c.put('/api/projects/'+slug, data=project(title='X')).status_code == 422
    assert c.put('/api/projects/'+slug, data=project(website_url='javascript:evil()')).status_code == 422
    assert c.put('/api/projects/'+slug, data=project(cover_style='invalid')).status_code == 422
    assert c.get('/api/projects/'+slug).json()['project']['title'] == 'Creative Real Project'


def test_upload_and_link_security(c):
    signup(c)
    assert c.post('/api/projects',data=project(),files={'cover':('evil.svg',b'<svg onload="alert(1)" />','image/svg+xml')}).status_code == 422
    assert c.post('/api/projects',data=project(repo_url='https://user:pw@example.com')).status_code == 422
    assert c.post('/api/projects',data=project(),headers={'Origin':'https://evil.example'}).status_code == 403
    assert c.get('/api/projects').json()['pagination']['total'] == 0


def test_duplicate_username_and_cookie_hardening(c):
    r=signup(c)
    assert r.status_code == 201
    cookie = r.headers['set-cookie']
    assert 'univorya_session=' in cookie
    assert 'httponly' in cookie.lower()
    assert 'samesite=lax' in cookie.lower()
    assert signup(c).status_code == 409
    assert c.post('/api/auth/logout').status_code == 200
    assert c.get('/api/auth/me').json()['user'] is None
