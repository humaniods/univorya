"""Real HTTP smoke test for a running Univorya instance.

Set UNIVORYA_TEST_URL=http://127.0.0.1:8000. Creates and deletes a temporary test
project under an ephemeral test account; account remains for auditability.
Do not run against somebody else's production server without permission.
"""
import io
import os
import uuid
from urllib.parse import urljoin

import httpx
from PIL import Image

BASE = os.environ.get('UNIVORYA_TEST_URL', 'http://127.0.0.1:8000').rstrip('/')
identifier = uuid.uuid4().hex[:10]
with httpx.Client(base_url=BASE, follow_redirects=True, timeout=15) as c:
    health = c.get('/api/health')
    assert health.status_code == 200 and health.json()['status'] == 'ok'
    register = c.post('/api/auth/register', json={
        'name': 'Smoke Test Creator', 'username': 'smoke'+identifier,
        'email':'smoke+'+identifier+'@example.invalid', 'password':'Test-Password-84715'
    })
    assert register.status_code == 201, register.text
    assert c.get('/api/auth/me').json()['user']['username'] == 'smoke'+identifier
    sample = Image.new('RGB',(450,260),(92,50,200))
    img = io.BytesIO()
    sample.save(img,format='PNG')
    payload = {'title':'HTTP Smoke Creation '+identifier,
      'tagline':'A real network end-to-end regression test project.',
      'description':'A real integration check that uploads a PNG, publishes and discovers an app, updates details, and finally deletes the project.',
      'category':'Web App','stage':'Beta','tech':'Python, FastAPI','cover_style':'blue'}
    published = c.post('/api/projects',data=payload, files={'cover':('fixture.png',img.getvalue(),'image/png')})
    assert published.status_code == 201, published.text
    slug = published.json()['slug']
    try:
        detail = c.get('/api/projects/'+slug)
        assert detail.status_code == 200 and detail.json()['project']['title']==payload['title']
        cover = detail.json()['project']['cover_image']
        image_res = c.get(cover)
        assert image_res.status_code == 200 and image_res.headers['content-type'].startswith('image/webp')
        search = c.get('/api/projects',params={'q':identifier})
        assert search.status_code == 200 and any(p['slug']==slug for p in search.json()['projects'])
        updated = c.put('/api/projects/'+slug,data={**payload, 'title':'Updated HTTP Smoke '+identifier})
        assert updated.status_code == 200, updated.text
        assert c.get('/api/projects/'+slug).json()['project']['title']=='Updated HTTP Smoke '+identifier
        assert c.post('/api/projects/'+slug+'/like').json()['liked'] is True
        assert c.get('/p/'+slug).status_code == 200
        print('PASS: real HTTP signup → PNG upload → published detail → image → search → edit → like → public page')
    finally:
        remove=c.delete('/api/projects/'+slug)
        assert remove.status_code == 200, remove.text
        assert c.get('/api/projects/'+slug).status_code == 404
        print('PASS: owner cleanup/deletion')
