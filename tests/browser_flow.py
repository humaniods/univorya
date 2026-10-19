"""Visual smoke test despite sandbox Chrome navigation policy.

This loads actual CSS/JS inside Chromium, forwards the app's fetch requests to
FastAPI TestClient, and patches only history routing for the about:blank origin.
Run: python tests/browser_flow.py
"""
import base64
import importlib
import io
import json
import os
import shutil
import tempfile
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DOCS = ROOT / 'docs'
DOCS.mkdir(exist_ok=True)


def preview():
    with tempfile.TemporaryDirectory() as temp:
        os.environ['UNIVORYA_DATA_DIR'] = temp
        os.environ['UNIVORYA_SEED_DEMO'] = '1'
        from app import main
        importlib.reload(main)
        with TestClient(main.app) as client:
            def serve(request):
                method = request.get('method', 'GET')
                url = request['url']
                if method == 'GET':
                    res = client.get(url)
                else:
                    files = None
                    body = request.get('body')
                    if request.get('form'):
                        data = request['form']['fields']
                        files = {x['name']:(x['filename'],base64.b64decode(x['data']),x['type'])
                                 for x in request['form']['files']}
                        res = client.request(method,url,data=data,files=files)
                    elif body:
                        res = client.request(method,url,content=body,headers={'content-type':request.get('contentType','application/json')})
                    else:
                        res = client.request(method,url)
                response_text=res.text
                # Browser sandbox blocks HTTP images too. Expose genuine stored image bytes
                # as inlined data URLs only in this test transport.
                if method=='GET' and url.startswith('/api/projects') and res.status_code==200:
                    payload=res.json()
                    cards=payload.get('projects',[])
                    if 'project' in payload:cards.append(payload['project'])
                    for card in cards:
                        if card.get('cover_image'):
                            blob=client.get(card['cover_image'])
                            assert blob.status_code==200
                            card['cover_image']='data:image/webp;base64,'+base64.b64encode(blob.content).decode()
                    response_text=json.dumps(payload)
                return {'status':res.status_code,'data':response_text,'type':res.headers.get('content-type','application/json')}

            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=shutil.which('chromium') or p.chromium.executable_path,headless=True,args=['--no-sandbox'])
                page = browser.new_page(viewport={'width':1440,'height':900},device_scale_factor=1)
                errors=[]
                page.on('pageerror',lambda err:errors.append(str(err)))
                page.expose_function('__backend',serve)
                page.set_content('''<!doctype html><html><head><base href="https://univorya.local/"></head><body><div class="site-glow glow-a"></div><div class="site-glow glow-b"></div><div id="app"></div><div id="toast-container"></div></body></html>''')
                css=(ROOT/'static/styles.css').read_text().split('\n',1)[1]
                page.add_style_tag(content=css)
                page.evaluate('''() => {
                  window.__testPath = '/';
                  window.fetch = async (url,opts={}) => {
                    const payload={url,method:opts.method||'GET',contentType:opts.headers?.['Content-Type']};
                    if(opts.body instanceof FormData){
                      const fields={},files=[];
                      for (const [name,value] of opts.body.entries()) {
                        if(value instanceof File){
                          const buffer=Array.from(new Uint8Array(await value.arrayBuffer()));
                          const b64=btoa(buffer.map(n=>String.fromCharCode(n)).join(''));
                          files.push({name,filename:value.name,type:value.type,data:b64});
                        } else {fields[name]=value;}
                      }
                      payload.form={fields,files};
                    }else if(opts.body){payload.body=opts.body;}
                    const response=await window.__backend(payload);
                    return new Response(response.data,{status:response.status,headers:{'content-type':response.type}});
                  };
                }''')
                logo='data:image/svg+xml;base64,'+base64.b64encode((ROOT/'static/logo.svg').read_bytes()).decode()
                script=(ROOT/'static/app.js').read_text().replace('/logo.svg',logo).replace('location.pathname', 'window.__testPath').replace("history.pushState({},'',path)","window.__testPath=path").replace("new URL(a.href,location.origin)","new URL(a.href,'https://univorya.local')")
                page.add_script_tag(content=script)
                page.locator('.project-card').first.wait_for(timeout=10000)
                print('Desktop homepage:',page.locator('h1').inner_text().replace('\n',' '),'| demo cards:',page.locator('.project-card').count())
                page.screenshot(path=str(DOCS/'home-desktop.png'),full_page=True)
                page.set_viewport_size({'width':390,'height':844})
                page.evaluate('home()')
                page.locator('.project-card').first.wait_for(timeout=10000)
                page.screenshot(path=str(DOCS/'home-mobile.png'),full_page=True)
                print('Mobile viewport:',page.evaluate('window.innerWidth'),'document scroll width:',page.evaluate('document.documentElement.scrollWidth'))
                assert page.evaluate('document.documentElement.scrollWidth') <= 410
                page.set_viewport_size({'width':1440,'height':900})

                page.locator('[data-link][href="/submit"]').first.click()
                assert page.locator('.gate-card').count() == 1
                page.locator('.gate-card [href="/join"]').click()
                page.locator('#full-name').fill('Morgan Builder')
                page.locator('#user-name').fill('morganbuilds')
                page.locator('#email').fill('morgan@test.example')
                page.locator('#password').fill('strong-user-password')
                page.locator('#auth-form button[type=submit]').click()
                page.locator('#project-form').wait_for(timeout=10000)
                print('Signup → submission form: passed')

                # Generate a richly rendered cover fixture so the actual image-upload showcase is visible.
                image=Image.new('RGB',(1400,850))
                pixels=image.load()
                for y in range(850):
                    for x in range(1400):
                        dx=x/1400;dy=y/850
                        light=max(0,1-(((dx-.72)**2 + (dy-.35)**2)**.5)*1.6)
                        pixels[x,y]=(int(32+76*light+26*dx),int(24+25*light+35*dy),int(70+95*light))
                d=ImageDraw.Draw(image,'RGBA')
                for i in range(6):
                    offset=i*58
                    d.arc((100+offset,70+offset,1300-offset,920-offset),35,330,fill=(180,174,255,35),width=3)
                d.rounded_rectangle((182,155,1218,692),radius=36,fill=(27,26,58,195),outline=(198,168,255,105),width=3)
                d.rounded_rectangle((212,187,1188,250),radius=14,fill=(255,255,255,18))
                for i,c in enumerate([(255,153,183,220),(255,216,156,220),(152,255,212,220)]):
                    d.ellipse((241+i*29,212,253+i*29,224),fill=c)
                d.rounded_rectangle((251,304,343,396),radius=27,fill=(167,137,255,180))
                try:
                    bold=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',67)
                    sub=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',28)
                except OSError:
                    bold=ImageFont.load_default();sub=bold
                d.text((372,303),'AURORA',font=bold,fill=(244,238,255,255))
                d.text((255,464),'A calmer way to build better habits.',font=sub,fill=(196,180,233,255))
                for i in range(7):
                    d.rounded_rectangle((255+i*118,550,341+i*118,610),radius=16,fill=(141,113,226,140 if i%2 else 210))
                out=io.BytesIO();image.save(out,format='PNG')
                page.locator('#cover').set_input_files({'name':'morgan-cover.png','mimeType':'image/png','buffer':out.getvalue()})
                page.locator('#title').fill('Aurora Habit Tracker')
                page.locator('#tagline').fill('A calmer way to track your everyday routines.')
                page.locator('#description').fill('Aurora is an independent habit-tracking web application that celebrates small everyday progress, thoughtful routines and reflection with a clean, distraction-free interface.')
                page.locator('#category').select_option('Web App')
                page.locator('#stage').select_option('Beta')
                page.locator('#tech').fill('Python, FastAPI, JavaScript')
                page.locator('#tags').fill('wellness, productivity')
                page.locator('#repo_url').fill('https://github.com/example/aurora')
                page.screenshot(path=str(DOCS/'submission-form.png'),full_page=True)
                page.locator('#project-form button[type=submit]').click()
                page.locator('.detail-header h1').wait_for(timeout=10000)
                assert 'Aurora Habit Tracker' in page.locator('.detail-header h1').inner_text()
                assert page.locator('.detail-art img').count() == 1
                page.locator('.detail-art img').evaluate('(img)=>img.decode()')
                assert page.locator('.detail-art img').evaluate('(img)=>img.naturalWidth') > 0
                assert page.locator('.detail-header .detail-author').inner_text().strip().startswith('M')
                page.screenshot(path=str(DOCS/'published-project.png'),full_page=True)
                print('Published image-backed project: passed')

                page.locator('.nav-links [href="/explore"]').click()
                page.locator('.project-card').first.wait_for(timeout=10000)
                page.locator('#search-input').fill('Aurora Habit')
                page.wait_for_timeout(550)
                assert page.locator('.project-card').count() == 1
                assert page.locator('.project-title').inner_text() == 'Aurora Habit Tracker'
                print('Public search discovery: passed')
                page.locator('.project-title').click()
                page.locator('[href^="/edit/"]').click()
                page.locator('#project-form').wait_for(timeout=10000)
                assert page.locator('#title').input_value()=='Aurora Habit Tracker'
                page.locator('#title').fill('Aurora Habit Studio')
                page.locator('#stage').select_option('Launched')
                page.locator('#project-form button[type=submit]').click()
                page.locator('.detail-header h1').wait_for(timeout=10000)
                assert 'Aurora Habit Studio' in page.locator('.detail-header h1').inner_text()
                assert 'Launched' in page.locator('.detail-header .stage').inner_text()
                print('Owner edit and published update: passed')
                if errors:print('Browser errors:',errors)
                assert not errors, errors
                browser.close()
    print('Visual and browser-interaction smoke test: PASSED')


if __name__=='__main__':
    preview()
