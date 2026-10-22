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
    shots=[]
    for n,(w,h,color) in enumerate([(900,600,(30,80,160)),(700,900,(160,60,90))]):
        buf=io.BytesIO();Image.new('RGB',(w,h),color).save(buf,format='PNG');shots.append((f'shot{n}.png',buf.getvalue(),'image/png'))
    payload = {'title':'HTTP Smoke Creation '+identifier,
      'tagline':'A real network end-to-end regression test project.',
      'description':'A real integration check that uploads a PNG, publishes and discovers an app, updates details, and finally deletes the project.',
      'category':'Web App','stage':'Beta','tech':'Python, FastAPI','cover_style':'blue'}
    published = c.post('/api/projects',data=payload, files=[
        ('cover',('fixture.png',img.getvalue(),'image/png')),
        *[('screenshots',shot) for shot in shots]])
    assert published.status_code == 201, published.text
    slug = published.json()['slug']
    after = {'media': []}
    try:
        detail = c.get('/api/projects/'+slug)
        assert detail.status_code == 200 and detail.json()['project']['title']==payload['title']
        project = detail.json()['project']
        kinds=[m['kind'] for m in project['media']]
        assert kinds==['cover','screenshot','screenshot'], kinds
        assert project['media_count']==3 and project['cover_image']==project['media'][0]['file']
        cover = detail.json()['project']['cover_image']
        image_res = c.get(cover)
        assert image_res.status_code == 200 and image_res.headers['content-type'].startswith('image/webp')
        shot_sizes = c.get(project['media'][1]['file'])
        assert shot_sizes.status_code == 200 and (shot_sizes.headers['content-length'] or '0') != '0'
        search = c.get('/api/projects',params={'q':identifier})
        assert search.status_code == 200 and any(p['slug']==slug for p in search.json()['projects'])
        maker = c.get('/api/makers/'+'smoke'+identifier)
        assert maker.status_code == 200 and maker.json()['maker']['project_count']==1, maker.text
        assert c.get('/api/makers/no-such-maker-'+identifier).status_code == 404
        # Dropping one screenshot on save must delete exactly that file.
        dropped = project['media'][2]
        kept=[m for m in project['media'] if m['kind']=='cover' or m['id']!=dropped['id']]
        updated = c.put('/api/projects/'+slug,
          data={**payload,'title':'Updated HTTP Smoke '+identifier,
                'keep_media':','.join(str(m['id']) for m in kept if m['kind']=='screenshot')})
        assert updated.status_code == 200, updated.text
        after = c.get('/api/projects/'+slug).json()['project']
        assert after['title']=='Updated HTTP Smoke '+identifier
        assert [m['kind'] for m in after['media']]==['cover','screenshot'], after['media']
        assert c.get(dropped['file']).status_code == 404, 'the dropped screenshot file must be gone'
        assert c.get('/api/projects/'+slug).json()['project']['title']=='Updated HTTP Smoke '+identifier
        assert c.post('/api/projects/'+slug+'/like').json()['liked'] is True
        assert c.get('/p/'+slug).status_code == 200
        assert c.get('/explore/', follow_redirects=False).status_code == 307
        assert c.get('/static/', follow_redirects=False).status_code == 404
        print('PASS: real HTTP signup → cover + screenshots → media rows → search → maker → edit drops one shot → like → public page')
    finally:
        remove=c.delete('/api/projects/'+slug)
        assert remove.status_code == 200, remove.text
        assert c.get('/api/projects/'+slug).status_code == 404
        leftover=[m['file'] for m in after['media']]
        for path in leftover:
            assert c.get(path).status_code == 404, path + ' survived deletion'
        print('PASS: owner cleanup removed the project and every uploaded file')
