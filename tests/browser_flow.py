"""End-to-end browser smoke test against a real Univorya server in Chromium.

Starts uvicorn on a throwaway data directory, drives Chromium through signup,
multi-image upload, publish, discovery, editing, the mobile drawer and the
animation layer, and writes the reference screenshots into docs/.

Run: python tests/browser_flow.py            (headless, for CI)
     UNIVORYA_HEADED=1 python tests/browser_flow.py   (watch it in a real window)
"""
import io
import os
import shutil
import socket
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DOCS = ROOT / 'docs'
DOCS.mkdir(exist_ok=True)
DATA = tempfile.mkdtemp(prefix='univorya-browser-')
os.environ['UNIVORYA_DATA_DIR'] = DATA
os.environ['UNIVORYA_SEED_DEMO'] = '1'

from app import main  # noqa: E402


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def artwork(size=(1400, 850), label='AURORA', sub='A calmer way to build better habits.'):
    """A richly drawn fixture so the image-backed showcase actually looks real."""
    image = Image.new('RGB', size)
    pixels = image.load()
    width, height = size
    for y in range(height):
        for x in range(width):
            dx, dy = x / width, y / height
            light = max(0, 1 - ((dx - .72) ** 2 + (dy - .35) ** 2) ** .5 * 1.6)
            pixels[x, y] = (int(32 + 76 * light + 26 * dx), int(24 + 25 * light + 35 * dy), int(70 + 95 * light))
    draw = ImageDraw.Draw(image, 'RGBA')
    for i in range(6):
        offset = i * 58
        draw.arc((100 + offset, 70 + offset, width - 100 - offset, height - 30 - offset), 35, 330,
                 fill=(180, 174, 255, 35), width=3)
    draw.rounded_rectangle((182, 155, width - 182, 692), radius=36, fill=(27, 26, 58, 195),
                           outline=(198, 168, 255, 105), width=3)
    draw.rounded_rectangle((212, 187, width - 212, 250), radius=14, fill=(255, 255, 255, 18))
    for i, colour in enumerate([(255, 153, 183, 220), (255, 216, 156, 220), (152, 255, 212, 220)]):
        draw.ellipse((241 + i * 29, 212, 253 + i * 29, 224), fill=colour)
    draw.rounded_rectangle((251, 304, 343, 396), radius=27, fill=(167, 137, 255, 180))
    try:
        bold = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 67)
        small = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 28)
    except OSError:
        bold = small = ImageFont.load_default()
    draw.text((372, 303), label, font=bold, fill=(244, 238, 255, 255))
    draw.text((255, 464), sub, font=small, fill=(196, 180, 233, 255))
    for i in range(7):
        draw.rounded_rectangle((255 + i * 118, 550, 341 + i * 118, 610), radius=16,
                               fill=(141, 113, 226, 140 if i % 2 else 210))
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


def screenshot(width=520, height=360, tone=(90, 60, 190), caption=''):
    image = Image.new('RGB', (width, height), tone)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, 42), fill=(24, 24, 44))
    for i in range(3):
        draw.ellipse((16 + i * 20, 14, 30 + i * 20, 28), fill=(200 - i * 40, 160, 220))
    draw.rounded_rectangle((28, 76, width - 28, 190), radius=12, fill=(255, 255, 255))
    draw.rounded_rectangle((28, 208, width - 120, 268), radius=12, fill=(20, 20, 40))
    draw.rounded_rectangle((28, 286, width - 28, 330), radius=12, fill=(230, 226, 255))
    if caption:
        draw.text((34, 400 // 2), caption, fill=(255, 255, 255))
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


def seed_pagination(base):
    """Enough real projects that /explore genuinely needs more than one page."""
    with httpx.Client(base_url=base, timeout=20) as client:
        client.post('/api/auth/register', json={'name': 'Pagination Planter', 'username': 'planter' + str(time.time())[-6:].replace('.', ''),
                                                'email': 'planter@example.invalid', 'password': 'Correct-Horse-59'})
        slugs = []
        for i in range(20):
            response = client.post('/api/projects', data={
                'title': 'Deep Shelf Project ' + str(i), 'tagline': 'Another seeded creation so pagination has work to do.',
                'description': 'Seeded only so the show-more control has a second page to fetch in the browser smoke test.',
                'category': 'Open Source', 'stage': 'Building', 'tech': 'SQLite', 'cover_style': 'slate'})
            assert response.status_code == 201, response.text
            slugs.append(response.json()['slug'])
        return slugs


def run(base):
    # Chrome reports every non-2xx response as a console error, so store the origin URL with each
    # message: the deliberately provoked 401/404 below must not mask a genuinely broken asset.
    errors, console = [], []
    expected_failures = {base + '/api/projects', base + '/api/makers/nobody-at-all',
                         base + '/api/projects/%25zz-not-a-slug'}
    with sync_playwright() as playwright:
        executable = shutil.which('chromium') or shutil.which('chromium-browser') or shutil.which('google-chrome')
        browser = playwright.chromium.launch(headless=not os.environ.get('UNIVORYA_HEADED'),
                                             executable_path=executable, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('console', lambda message: console.append((message.text, message.location.get('url', ''))) if message.type == 'error' else None)

        page.goto(base + '/', wait_until='networkidle')
        page.locator('.project-card').first.wait_for(timeout=15000)
        cards = page.locator('.project-card').count()
        print(f'home renders {cards} seeded demo cards, h1 = {page.locator("h1").first.inner_text()!r}')
        assert cards >= 8

        # Fonts are self-hosted now: they must actually load under font-src 'self'.
        page.evaluate('() => document.fonts.ready')
        loaded = page.evaluate('() => document.fonts.size')
        family = page.evaluate("() => getComputedStyle(document.querySelector('h1')).fontFamily")
        print(f'self-hosted fonts: {loaded} faces registered, h1 uses {family}')
        assert loaded >= 4 and 'Jakarta' in family

        # The motion layer must be live, not just declared.
        running = page.evaluate('() => document.getAnimations().filter(a => a.playState === "running").length')
        keyframes = page.evaluate("""() => {let n=0;for(const s of document.styleSheets){try{for(const r of s.cssRules){if(r.type===CSSRule.KEYFRAMES_RULE)n++}}catch{}}return n}""")
        print(f'animation layer: {keyframes} @keyframes rules, {running} animations running')
        assert keyframes >= 10 and running >= 3

        page.locator('#discover').scroll_into_view_if_needed()
        page.wait_for_timeout(900)
        revealed = page.locator('.project-card.is-visible').count()
        print(f'scroll reveals: {revealed} cards animated into view')
        assert revealed >= 6
        page.screenshot(path=str(DOCS / 'home-desktop.png'), full_page=True)

        # URL state: filters must survive a reload.
        page.locator('#search-input').fill('Orbit')
        page.wait_for_timeout(700)
        assert 'q=Orbit' in page.url, page.url
        page.locator('#stage-select').select_option('Beta')
        page.wait_for_timeout(500)
        assert 'stage=Beta' in page.url
        page.reload(wait_until='networkidle')
        page.wait_for_timeout(900)
        assert page.locator('#search-input').input_value() == 'Orbit', 'search must survive reload'
        assert page.locator('#stage-select').input_value() == 'Beta'
        print('deep link survives reload:', page.url.replace(base, ''))
        page.locator('#search-input').fill('')
        page.wait_for_timeout(700)

        # Pagination: the P0 bug left 91% of the catalogue unreachable.
        page.goto(base + '/explore', wait_until='networkidle')
        page.locator('.project-card').first.wait_for(timeout=15000)
        first_page = page.locator('.project-card').count()
        total = int(page.locator('#results-count').inner_text().split()[0])
        page.locator('[data-action="load-more"]').click()
        page.wait_for_timeout(1200)
        grown = page.locator('.project-card').count()
        print(f'show more: {first_page} -> {grown} cards of {total} total')
        assert grown > first_page
        assert page.locator('.toast.error').count() == 0, page.locator('.toast.error').all_inner_texts()
        page.screenshot(path=str(DOCS / 'explore-pagination.png'), full_page=False)

        # Responsive overflow sweep.
        for width in (320, 390, 768, 1024):
            page.set_viewport_size({'width': width, 'height': 900})
            page.wait_for_timeout(350)
            overflow = page.evaluate('() => document.documentElement.scrollWidth - document.documentElement.clientWidth')
            assert overflow <= 0, f'{width}px overflows by {overflow}px'
        print('responsive sweep: no horizontal overflow at 320/390/768/1024')

        # Mobile drawer behaviour.
        page.set_viewport_size({'width': 390, 'height': 844})
        page.goto(base + '/', wait_until='networkidle')
        page.evaluate('home()')
        page.locator('[data-action="menu"]').wait_for(state='visible')
        page.locator('[data-action="menu"]').click()
        page.wait_for_timeout(400)
        opened = page.evaluate("""() => {const nav=document.querySelector('.nav-links');
          return {open:nav.classList.contains('mobile-open'), expanded:document.querySelector('[data-action="menu"]').getAttribute('aria-expanded'),
                  locked:getComputedStyle(document.body).overflowY, veil:!!document.querySelector('.drawer-veil')}}""")
        print('drawer opened:', opened)
        assert opened['open'] and opened['expanded'] == 'true' and opened['locked'] == 'hidden' and opened['veil']
        page.screenshot(path=str(DOCS / 'home-mobile.png'), full_page=False)
        page.keyboard.press('Tab')
        inside = page.evaluate("() => document.querySelector('.nav-links').contains(document.activeElement)")
        assert inside, 'focus must be trapped inside the open drawer'
        page.keyboard.press('Escape')
        page.wait_for_timeout(350)
        closed = page.evaluate('''() => {const nav=document.querySelector('.nav-links');
          return {open:nav.classList.contains('mobile-open'),
                  expanded:document.querySelector('[data-action="menu"]').getAttribute('aria-expanded')}}''')
        assert not closed['open'] and closed['expanded'] == 'false'
        page.locator('[data-action="menu"]').click()
        page.wait_for_timeout(300)
        page.mouse.click(195, 620)
        page.wait_for_timeout(350)
        assert page.evaluate("() => !document.querySelector('.nav-links').classList.contains('mobile-open')"), 'outside click must close the drawer'
        print('drawer: Escape, outside click, focus trap and scroll lock all behave')
        page.set_viewport_size({'width': 1440, 'height': 900})

        # Signup, then a project carrying a cover AND screenshots.
        page.goto(base + '/submit', wait_until='networkidle')
        page.locator('.gate-card').wait_for()
        page.locator('.gate-card [href="/join"]').click()
        page.locator('#full-name').fill('Morgan Builder')
        page.locator('#user-name').fill('morganbuilds')
        page.locator('#email').fill('morgan@test.example')
        meter = page.locator('#pw-meter')
        page.locator('#password').fill('strong')
        page.wait_for_timeout(200)
        weak_score = meter.get_attribute('data-score')
        page.locator('#password').fill('strong-user-password-9')
        page.wait_for_timeout(200)
        strong_score = meter.get_attribute('data-score')
        print(f'password meter: weak={weak_score} strong={strong_score}')
        assert weak_score == '1' and strong_score == '3'
        page.locator('#auth-form button[type=submit]').click()
        page.locator('#project-form').wait_for(timeout=15000)

        page.locator('#cover').set_input_files({'name': 'morgan-cover.png', 'mimeType': 'image/png', 'buffer': artwork()})
        page.locator('#screenshots').set_input_files([
            {'name': 'dashboard.png', 'mimeType': 'image/png', 'buffer': screenshot(720, 480, (72, 52, 150), 'Dashboard')},
            {'name': 'mobile.png', 'mimeType': 'image/png', 'buffer': screenshot(520, 760, (30, 120, 130), 'Mobile')}])
        page.wait_for_timeout(600)
        tiles = page.locator('.media-tile').count()
        spec = page.locator('#cover-zone .upload-spec').inner_text()
        print(f'upload feedback: {tiles} screenshot tiles, cover spec "{spec}"')
        assert tiles == 2 and '×' in spec
        page.locator('.media-tile.is-new').first.screenshot(path=str(DOCS / 'upload-tile.png'))

        page.locator('#title').fill('Aurora Habit Tracker')
        page.locator('#tagline').fill('A calmer way to track your everyday routines.')
        page.locator('#description').fill('Aurora is an independent habit-tracking web application that celebrates small everyday progress, thoughtful routines and reflection with a clean, distraction-free interface.')
        page.locator('#category').select_option('Web App')
        page.locator('#stage').select_option('Beta')
        page.locator('#tech').fill('Python, FastAPI, JavaScript')
        page.locator('#tags').fill('wellness, productivity')
        page.locator('#repo_url').fill('https://github.com/example/aurora')
        # The creator's preview target has to be a server on this machine, so the smoke test points
        # it at the app itself: a real loopback URL that answers, and can be proven to open.
        preview_target = base + '/explore'
        page.locator('#preview_url').fill(preview_target)
        page.screenshot(path=str(DOCS / 'submission-form.png'), full_page=True)

        # A dead session must not destroy what the maker typed.
        page.context.clear_cookies()
        page.locator('#project-form button[type=submit]').click()
        page.wait_for_timeout(1400)
        assert '/login' in page.url, page.url
        page.locator('#email').fill('morgan@test.example')
        page.locator('#password').fill('strong-user-password-9')
        page.locator('#auth-form button[type=submit]').click()
        page.locator('#project-form').wait_for(timeout=15000)
        page.wait_for_timeout(400)
        restored = page.locator('#title').input_value()
        kept_tiles = page.locator('.media-tile').count()
        restored_preview = page.locator('#preview_url').input_value()
        print(f'session recovery: title restored to {restored!r}, {kept_tiles} picked screenshots still queued, '
              f'local preview address restored to {restored_preview!r}')
        assert restored == 'Aurora Habit Tracker' and kept_tiles == 2
        assert restored_preview == preview_target, 'the private loopback address was lost with the draft'

        page.locator('#project-form button[type=submit]').click()
        page.locator('.detail-header h1').wait_for(timeout=15000)
        assert 'Aurora Habit Tracker' in page.locator('.detail-header h1').inner_text()
        shots = page.locator('.shot').count()
        # The cover was picked before the session dropped, so it must have travelled with the draft.
        cover = page.locator('.detail-art .cover-art.has-photo img')
        assert cover.count() == 1, 'cover image survived the 401 round-trip'
        assert cover.evaluate('img => img.decode().then(()=>img.naturalWidth)') > 150
        uploads = page.locator('.snapshot-row', has_text='Uploads').inner_text()
        print(f'published project: {shots} screenshots, cover kept, snapshot row {uploads!r}')
        assert shots == 2 and '3 images' in uploads
        detail_url = page.url
        # The creator gets their stored loopback address back; nobody else is offered this box at all.
        preview_box = page.locator('.local-preview-box')
        assert preview_box.count() == 1, 'the owner lost the local preview panel'
        assert preview_box.locator('code').first.inner_text() == preview_target
        assert preview_box.locator('[data-action="open-local-preview"]').count() == 1
        assert 'scripts/showcase.py' in preview_box.inner_text(), 'the terminal route was not offered'
        # Screenshots live below the fold: they should only reveal once scrolled into view.
        assert page.locator('.shot.is-visible').count() == 0
        page.locator('.shot-grid').scroll_into_view_if_needed()
        page.wait_for_timeout(900)
        assert page.locator('.shot.is-visible').count() == shots
        page.screenshot(path=str(DOCS / 'published-project.png'), full_page=True)

        page.locator('.shot').first.click()
        page.locator('.lightbox.is-open').wait_for(timeout=5000)
        caption = page.locator('.lightbox figcaption').inner_text()
        page.screenshot(path=str(DOCS / 'lightbox.png'))
        page.keyboard.press('ArrowRight')
        page.wait_for_timeout(300)
        second = page.locator('.lightbox figcaption').inner_text()
        page.keyboard.press('Escape')
        page.wait_for_timeout(400)
        assert page.locator('.lightbox.is-open').count() == 0
        print(f'lightbox: "{caption}" -> "{second}" -> closed with Escape')

        # Pressing the owner's button must really hand the address to another browser window.
        # This runs after the reveal checks because clicking scrolls the aside into view.
        with page.expect_popup(timeout=15000) as popped:
            page.locator('.local-preview-box [data-action="open-local-preview"]').click()
        window = popped.value
        window.wait_for_selector('.project-card', timeout=15000)
        assert window.url.rstrip('/') == preview_target.rstrip('/'), window.url
        print(f'local preview opened in its own window at {window.url}')
        window.close()

        before = int(page.locator('#like-count').inner_text())
        page.locator('[data-action="like"]').click()
        page.wait_for_timeout(600)
        after = int(page.locator('#like-count').inner_text())
        assert after == before + 1 and page.locator('.btn.liked').count() == 1
        print(f'like: {before} -> {after}')

        # Discover it the way a visitor would.
        page.locator('.nav-links [href="/explore"]').click()
        page.locator('#search-input').fill('Aurora Habit')
        page.wait_for_timeout(700)
        assert page.locator('.project-card').count() == 1
        assert page.locator('.project-title').inner_text() == 'Aurora Habit Tracker'
        print('public search discovery: passed')

        # Editing must let go of one screenshot and keep the rest.
        page.locator('.project-title').click()
        page.locator('[href^="/edit/"]').click()
        page.locator('#project-form').wait_for(timeout=15000)
        assert page.locator('#title').input_value() == 'Aurora Habit Tracker'
        assert page.locator('#preview_url').input_value() == preview_target, \
            'the edit form must prefill the stored loopback address'
        listed = [tile.replace('\n', ' ') for tile in page.locator('.media-tile').all_inner_texts()]
        print(f'edit form lists {len(listed)} stored images: {listed}')
        assert page.locator('.media-tile').count() == 3, 'cover + 2 screenshots should be listed'
        # Every stored row came from project_media, so each tile carries that row's own facts.
        months = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
        stamped_tiles = page.locator('.media-tile time')
        assert stamped_tiles.count() == 3, 'a stored upload lost its upload date'
        stamped = [(stamped_tiles.nth(i).inner_text(), stamped_tiles.nth(i).get_attribute('datetime')) for i in range(3)]
        print(f'stored uploads dated from their own rows: {stamped}')
        for label, iso in stamped:
            assert any(month in label for month in months), (label, iso)
            assert len(label.split()[-1]) == 4 and label.split()[-1].isdigit(), label
            assert iso.startswith('20') and 'T' in iso, iso
        page.locator('.media-tile [data-action="drop-existing-media"]').last.click()
        page.wait_for_timeout(250)
        page.locator('#title').fill('Aurora Habit Studio')
        page.locator('#stage').select_option('Launched')
        page.locator('#project-form button[type=submit]').click()
        page.locator('.detail-header h1').wait_for(timeout=15000)
        assert 'Aurora Habit Studio' in page.locator('.detail-header h1').inner_text()
        assert page.locator('.shot').count() == 1, 'the removed screenshot must be gone'
        assert 'Launched' in page.locator('.detail-header .stage').inner_text()
        print('owner edit, screenshot removal and published update: passed')

        # Dropping the last stored screenshot sends keep_media as an empty list, which the
        # browser cannot distinguish from omitting it unless the server checks presence.
        page.locator('[href^="/edit/"]').click()
        page.locator('#project-form').wait_for(timeout=15000)
        assert page.locator('.media-tile').count() == 2, 'cover + the one screenshot left should be listed'
        page.locator('.media-tile [data-action="drop-existing-media"]').click()
        page.wait_for_timeout(250)
        assert page.locator('.media-tile:not(.is-dropped)').count() == 1, 'only the cover should stay queued'
        assert page.locator('.media-tile [data-action="restore-media"]').count() == 1, 'a dropped shot must be restorable'
        page.locator('#project-form button[type=submit]').click()
        page.locator('.detail-header h1').wait_for(timeout=15000)
        assert page.locator('.shot').count() == 0, 'an emptied gallery must stay empty'
        assert page.locator('.detail-art .cover-art.has-photo img').count() == 1, 'the cover must survive'
        assert page.locator('.local-preview-box').count() == 1, 'editing wiped the stored preview address'
        page.screenshot(path=str(DOCS / 'gallery-cleared.png'), full_page=False)
        print('clearing the whole screenshot gallery from the edit form: passed')

        # Maker profile resolves and animates its counters.
        page.goto(base + '/makers/morganbuilds', wait_until='networkidle')
        page.locator('.maker-header').wait_for(timeout=10000)
        page.wait_for_timeout(1200)
        stats = page.locator('.maker-stats .stat b').all_inner_texts()
        print('maker profile stats:', stats)
        assert int(stats[0]) == 1
        page.screenshot(path=str(DOCS / 'maker-profile.png'), full_page=True)
        page.goto(base + '/makers/nobody-at-all', wait_until='networkidle')
        assert 'No creator found' in page.locator('.gate-card h1').inner_text()
        print('unknown creator: honest 404 instead of a fabricated profile')

        # An unknown client route must say so instead of quietly rendering the explore grid.
        page.goto(base + '/a-path-nobody-wrote', wait_until='networkidle')
        page.locator('.gate-card h1').wait_for(timeout=10000)
        assert 'Nothing lives at this address' in page.locator('.gate-card h1').inner_text()
        assert page.locator('.project-card').count() == 0, 'the not-found route leaked the grid'
        assert 'Page not found' in page.title(), page.title()
        page.screenshot(path=str(DOCS / 'not-found.png'), full_page=False)
        print('unknown route: honest not-found page instead of the explore grid')

        # A broken %xx in a URL must degrade, not throw and leave the shell empty.
        page.goto(base + '/p/%zz-not-a-slug', wait_until='networkidle')
        page.locator('.gate-card h1, .detail-header h1').first.wait_for(timeout=10000)
        assert page.locator('#app').inner_html().strip(), 'a malformed URL blanked the page'
        print('malformed percent-encoded path: handled without a crash')

        # A signed-out visitor is gated on /submit, and the gate offers a way through.
        signed_out = browser.new_context(viewport={'width': 1440, 'height': 900})
        guest = signed_out.new_page()
        guest.goto(base + '/submit', wait_until='networkidle')
        guest.locator('.gate-card h1').wait_for(timeout=10000)
        assert guest.locator('#project-form').count() == 0, 'the publish form rendered without a session'
        assert guest.locator('.gate-card [href="/join"]').count() == 1, 'no path to create an account'
        assert guest.locator('.gate-card [href="/login"]').count() == 1, 'no path to sign in'
        guest.locator('.gate-card [href="/join"]').click()
        guest.locator('#auth-form').wait_for(timeout=10000)
        assert guest.locator('#full-name').count() == 1, 'the join form lost its name field'
        guest.goto(base + '/dashboard', wait_until='networkidle')
        guest.locator('#auth-form').wait_for(timeout=10000)
        assert '/login' in guest.url, f'/dashboard should send a signed-out visitor to /login, got {guest.url}'
        assert guest.locator('.project-card, .dashboard-page').count() == 0, 'a private dashboard leaked'
        # The creator's loopback address is not public information, in the DOM or in the API.
        guest.goto(detail_url, wait_until='networkidle')
        guest.locator('.detail-header h1').wait_for(timeout=10000)
        assert guest.locator('.local-preview-box').count() == 0, 'a visitor was shown the creator\'s local address'
        payload = guest.evaluate("""async slug => (await (await fetch('/api/projects/' + slug)).json()).project""",
                                 detail_url.rstrip('/').rsplit('/', 1)[-1])
        assert 'preview_url' not in payload, f'the API handed a visitor {payload.get("preview_url")!r}'
        print('local preview address: absent from a signed-out visitor\'s DOM and API payload')
        signed_out.close()
        print('signed-out gates: /submit gates in place, /dashboard redirects to /login')

        # A public URL typed into the private field must be refused, visibly, through the real form.
        page.goto(base + '/submit', wait_until='networkidle')
        page.locator('#project-form').wait_for(timeout=15000)
        page.locator('#title').fill('Project With A Deployed Preview')
        page.locator('#tagline').fill('A tagline that clears validation so the server can refuse the address.')
        page.locator('#description').fill('An honest description long enough to pass every rule except the one this '
                                          'step is testing, which is that a preview address stays on this machine.')
        page.locator('#preview_url').fill('https://deployed.example.com')
        page.locator('#project-form button[type=submit]').click()
        page.wait_for_timeout(1400)
        refused = page.locator('#project-error').inner_text()
        assert '/p/' not in page.url, 'a non-loopback preview address was published'
        assert 'preview' in refused.lower(), refused
        print('the form refused a public preview address:', refused)
        # Playwright's fill() fires the input event, so this also rewrites the browser draft that
        # the sweep below restores from; leaving the rejected value queued would fail every entry.
        page.locator('#preview_url').fill('')
        page.wait_for_timeout(300)

        # Every category the product offers has to survive the same journey: form -> database -> animated gallery.
        sweep = ['Web App', 'Mobile App', 'AI & ML', 'Dev Tool', 'Open Source', 'Design', 'Idea', 'Other']
        stages = ['Idea', 'Building', 'Beta', 'Launched']
        swept = {}
        for index, category in enumerate(sweep):
            page.goto(base + '/submit', wait_until='networkidle')
            page.locator('#project-form').wait_for(timeout=15000)
            page.locator('#title').fill(f'{category} Sweep Entry {index}')
            page.locator('#tagline').fill('A tagline written by the browser smoke test to clear validation.')
            page.locator('#description').fill('An honest description of a showcase entry, here to prove that this category '
                                              'reaches the database, the gallery and the motion layer like every other one.')
            page.locator('#category').select_option(category)
            page.locator('#stage').select_option(stages[index % len(stages)])
            page.locator('#tech').fill('FastAPI, SQLite')
            page.locator('#tags').fill('sweep, ' + category.lower().replace(' ', '-').replace('&', 'and'))
            page.locator('#cover').set_input_files({'name': 'sweep-cover.png', 'mimeType': 'image/png',
                                                    'buffer': screenshot(760, 500, (40 + index * 26, 96, 205))})
            if index % 2 == 0:
                page.locator('#screenshots').set_input_files([
                    {'name': 'sweep-shot.png', 'mimeType': 'image/png',
                     'buffer': screenshot(640, 420, (205, 130 - index * 11, 74))}])
            page.locator('#project-form button[type=submit]').click()
            page.locator('.detail-header h1').wait_for(timeout=20000)
            swept[category] = page.url.rstrip('/').rsplit('/', 1)[-1]
        print('published through the real form, one per category:', ', '.join(swept))

        # The rows have to exist in the database, not just on the screen.
        con = sqlite3.connect(main.DB_PATH)
        con.row_factory = sqlite3.Row
        stored = con.execute("""SELECT p.title, p.category, p.stage, COUNT(m.id) media,
                                       SUM(m.kind='cover') covers, SUM(m.kind='screenshot') shots
                                FROM projects p LEFT JOIN project_media m ON m.project_id=p.id
                                WHERE p.title LIKE '%Sweep Entry%' GROUP BY p.id
                                ORDER BY p.id""").fetchall()
        refused_row = con.execute("SELECT 1 FROM projects WHERE title LIKE '%Deployed Preview%'").fetchone()
        aurora = con.execute("SELECT preview_url FROM projects WHERE title='Aurora Habit Studio'").fetchone()
        con.close()
        assert len(stored) == 8, [row['title'] for row in stored]
        assert refused_row is None, 'the refused submission still reached the database'
        assert aurora and aurora['preview_url'] == preview_target, \
            f'the stored row kept {aurora!r} instead of the address the form was given'
        print('persisted preview_url on the edited project:', aurora['preview_url'])
        for index, category in enumerate(sweep):
            row = stored[index]
            assert row['category'] == category, (row['title'], row['category'])
            assert row['stage'] == stages[index % len(stages)], (row['title'], row['stage'])
            assert row['covers'] == 1, f'{category} lost its cover row'
            assert row['shots'] == (1 if index % 2 == 0 else 0), f'{category} screenshot rows: {row["shots"]}'
        print('database rows per category:',
              '; '.join(f"{row['category']}={row['media']}img" for row in stored))

        # And the motion layer has to actually finish on the creator's own pages.
        def still_hidden(path, selector):
            page.goto(base + path, wait_until='networkidle')
            page.wait_for_timeout(700)
            height = page.evaluate('() => document.body.scrollHeight')
            for offset in range(0, height + 400, 400):
                page.evaluate(f'() => window.scrollTo(0, {offset})')
                page.wait_for_timeout(90)
            page.wait_for_timeout(700)
            cards = page.locator(selector)
            faded = [cards.nth(i).evaluate('el => getComputedStyle(el).opacity') for i in range(cards.count())]
            return cards.count(), sum(1 for value in faded if float(value) < 0.05)

        for label, path, selector in [('dashboard', '/dashboard', '.project-card'),
                                       ('own maker profile', '/makers/morganbuilds', '.project-card'),
                                       ('category filter', '/explore?category=Design', '.project-card'),
                                       ('swept project detail', '/p/' + swept['AI & ML'], '.reveal')]:
            total, hidden = still_hidden(path, selector)
            assert total and hidden == 0, f'{label}: {hidden} of {total} never animated in'
            print(f'{label}: all {total} {selector} elements animated to full opacity')

        # The creator sees what they uploaded about each image, from the database row.
        page.goto(base + '/p/' + swept['AI & ML'], wait_until='networkidle')
        page.locator('.shot').first.wait_for(timeout=15000)
        caption = page.locator('.shot-bar').first.inner_text().replace('\n', ' ')
        page.locator('.shot').first.click()
        page.locator('.lightbox.is-open').wait_for(timeout=10000)
        lightbox = page.locator('.lightbox figcaption').inner_text()
        page.keyboard.press('Escape')
        print(f'own upload facts on screen: tile "{caption}" / lightbox "{lightbox}"')
        assert '×' in caption and 'KB' in caption, caption
        assert '×' in lightbox and 'KB' in lightbox, lightbox

        # Reduced motion must stay complete but still.
        calm = browser.new_context(viewport={'width': 1440, 'height': 900}, reduced_motion='reduce')
        calm_page = calm.new_page()
        calm_page.goto(base + '/', wait_until='networkidle')
        calm_page.locator('.project-card').first.wait_for(timeout=15000)
        calm_page.locator('#discover').scroll_into_view_if_needed()
        calm_page.wait_for_timeout(600)
        visible = calm_page.locator('.project-card.is-visible').count()
        opacity = calm_page.locator('.project-card').first.evaluate('el => getComputedStyle(el).opacity')
        moving = calm_page.evaluate('() => document.getAnimations().filter(a => a.playState === "running").length')
        print(f'prefers-reduced-motion: {visible} cards visible, opacity {opacity}, {moving} running animations')
        assert visible >= 6 and opacity == '1' and moving == 0
        calm_page.screenshot(path=str(DOCS / 'reduced-motion.png'), full_page=True)
        calm.close()

        unexpected = [f'{text} <- {url}' for text, url in console if url not in expected_failures]
        if unexpected:
            print('console errors:', unexpected[:5])
        assert not unexpected, unexpected[:5]
        assert not errors, errors
        browser.close()
    print('Visual and browser-interaction smoke test: PASSED')


def preview():
    port = free_port()
    base = f'http://127.0.0.1:{port}'
    server = uvicorn.Server(uvicorn.Config(main.app, host='127.0.0.1', port=port, log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(80):
            try:
                if httpx.get(base + '/api/health', timeout=1).status_code == 200:
                    break
            except Exception:
                time.sleep(0.25)
        else:
            raise RuntimeError('uvicorn did not come up')
        seed_pagination(base)
        if os.environ.get('UNIVORYA_HEADED'):
            print('Opening a visible Chromium window at', base)
        run(base)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        shutil.rmtree(DATA, ignore_errors=True)


if __name__ == '__main__':
    preview()
