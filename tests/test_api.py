"""End-to-end HTTP tests for account → image upload → published discovery."""
import importlib
import io
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image


def png_bytes():
    image = Image.new("RGB", (620, 360), (115, 86, 220))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def publish(**overrides):
    fields = {
        "title": "Nightowl Reading App",
        "tagline": "Read, annotate and discover stories together.",
        "description": "A community-powered reading application with live annotations, customizable bookmarks, and a peaceful reading experience.",
        "category": "Web App",
        "stage": "Beta",
        "tech": "Python, FastAPI, JavaScript",
        "tags": "reading, open source",
        "repo_url": "https://github.com/example/nightowl",
        "demo_url": "https://example.org/nightowl",
        "cover_style": "blue",
    }
    fields.update(overrides)
    return fields


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("UNIVORYA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UNIVORYA_SEED_DEMO", "0")
    from app import main
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def signup(c, name="Taylor Dev", username="taylordev", email="taylor@example.com"):
    return c.post("/api/auth/register", json={"name": name, "username": username, "email": email,
                                                "password": "Correct-Horse-59"})


def test_health_and_static(client):
    assert client.get('/api/health').json()['status'] == 'ok'
    assert client.get('/').status_code == 200
    assert 'Univorya' in client.get('/').text
    assert 'hero-grid' in client.get('/styles.css').text
    assert client.get('/api/projects').json()['projects'] == []


def test_signup_signin_logout_and_duplicate_registration(client):
    assert signup(client).status_code == 201
    assert client.get('/api/auth/me').json()['user']['username'] == 'taylordev'
    assert signup(client).status_code == 409
    assert client.post('/api/auth/logout').status_code == 200
    assert client.get('/api/auth/me').json()['user'] is None
    assert client.post('/api/auth/login', json={"email": "taylor@example.com", "password": "wrongwrong"}).status_code == 401
    assert client.post('/api/auth/login', json={"email": "taylordev", "password": "Correct-Horse-59"}).status_code == 200
    assert client.get('/api/auth/me').json()['user']['name'] == 'Taylor Dev'


def test_create_with_real_image_and_public_showcase(client):
    assert client.post('/api/projects', data=publish()).status_code == 401
    assert signup(client).status_code == 201
    res = client.post('/api/projects', data=publish(), files={"cover": ("sample.png", png_bytes(), "image/png")})
    assert res.status_code == 201, res.text
    slug = res.json()['slug']
    assert res.json()['url'] == f'/p/{slug}'
    public = client.get('/api/projects').json()
    assert public['pagination']['total'] == 1
    p = public['projects'][0]
    assert p['title'] == 'Nightowl Reading App'
    assert p['creator']['username'] == 'taylordev'
    assert p['stage'] == 'Beta'
    assert p['tech'] == ['Python', 'FastAPI', 'JavaScript']
    assert p['tags'] == ['reading', 'open source']
    assert p['cover_image'].startswith('/uploads/')
    img = client.get(p['cover_image'])
    assert img.status_code == 200
    assert img.headers['content-type'] == 'image/webp'
    assert Image.open(io.BytesIO(img.content)).format == 'WEBP'
    detail = client.get(f'/api/projects/{slug}').json()['project']
    assert detail['views'] >= 1
    assert detail['is_owner']
    assert client.get(f'/p/{slug}').status_code == 200


def test_search_filter_and_likes(client):
    assert signup(client).status_code == 201
    slug = client.post('/api/projects', data=publish()).json()['slug']
    assert client.get('/api/projects?q=nightowl').json()['pagination']['total'] == 1
    assert client.get('/api/projects?category=AI%20%26%20ML').json()['pagination']['total'] == 0
    assert client.get('/api/projects?stage=Beta').json()['pagination']['total'] == 1
    assert client.get('/api/projects?creator=taylordev').json()['pagination']['total'] == 1
    assert client.get('/api/projects?sort=invalid').status_code == 422
    assert client.post(f'/api/projects/{slug}/like').json() == {'liked': True, 'likes': 1}
    assert client.post(f'/api/projects/{slug}/like').json() == {'liked': False, 'likes': 0}
    client.post('/api/auth/logout')
    assert client.post(f'/api/projects/{slug}/like').status_code == 401
    assert client.get(f'/api/projects/{slug}').json()['project']['is_owner'] is False


def test_invalid_fields_and_upload_rejected(client):
    assert signup(client).status_code == 201
    assert client.post('/api/projects', data=publish(title='x')).status_code == 422
    assert client.post('/api/projects', data=publish(category='Hacking')).status_code == 422
    assert client.post('/api/projects', data=publish(demo_url='javascript:alert(1)')).status_code == 422
    assert client.post('/api/projects', data=publish(), files={'cover':('fake.png',b'not a real image','image/png')}).status_code == 422
    assert client.post('/api/projects', data=publish(), files={'cover':('huge.png',b'x'*(5*1024*1024+1),'image/png')}).status_code == 413
    assert client.get('/api/projects').json()['pagination']['total'] == 0


def test_owner_delete_and_cross_user_protection(client):
    assert signup(client).status_code == 201
    slug = client.post('/api/projects', data=publish()).json()['slug']
    client.post('/api/auth/logout')
    assert client.delete(f'/api/projects/{slug}').status_code == 401
    assert signup(client, 'Other User', 'otheruser', 'other@example.com').status_code == 201
    assert client.delete(f'/api/projects/{slug}').status_code == 403
    client.post('/api/auth/logout')
    assert client.post('/api/auth/login', json={'email':'taylor@example.com','password':'Correct-Horse-59'}).status_code == 200
    assert client.delete(f'/api/projects/{slug}').status_code == 200
    assert client.get(f'/api/projects/{slug}').status_code == 404


def test_csrf_origin_rejection(client):
    signup(client)
    assert client.post('/api/projects', data=publish(), headers={'Origin':'https://malicious.example'}).status_code == 403


def test_ideas_can_be_published_without_a_repository(client):
    signup(client)
    result = client.post('/api/projects', data=publish(category='Idea', stage='Idea', repo_url='', demo_url='', cover_style='pink'))
    assert result.status_code == 201, result.text
    p = client.get('/api/projects').json()['projects'][0]
    assert p['category'] == 'Idea'
    assert p['cover_image'] == ''
    assert p['repo_url'] == ''
