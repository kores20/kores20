"""Upload fixture images, persist privately, edit placement and remove them."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as tmp:
    directory=Path(tmp)
    for kind in ['signature','stamp']:
        im=Image.new('RGBA',(220,100),(255,255,255,0));draw=ImageDraw.Draw(im)
        if kind=='signature':draw.line([(20,60),(60,25),(100,65),(180,35)],fill='blue',width=4)
        else:draw.rectangle((20,15,200,85),outline='blue',width=4);draw.text((70,40),'TEST ONLY',fill='blue')
        im.save(directory/(kind+'.png'))
    env=dict(os.environ,DATA_DIR=str(directory/'data'),PORT='8096')
    server=subprocess.Popen([sys.executable,'-m','app.server'],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=open(directory/'server.log','w'))
    try:
        for _ in range(60):
            try:urllib.request.urlopen('http://127.0.0.1:8096/api/state',timeout=1);break
            except OSError:time.sleep(.1)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True,executable_path=os.getenv('CHROMIUM_PATH','/usr/bin/chromium'))
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto('http://127.0.0.1:8096');page.wait_for_selector('#empty:not(.hidden)')
            page.click('#nav-signing');page.wait_for_selector('#signing-dialog[open]')
            page.wait_for_function("document.querySelector('#signing-owner').value==='علي الشهري'")
            for kind in ['signature','stamp']:
                page.set_input_files('#signing-'+kind+'-file',directory/(kind+'.png'))
                page.click('[data-signing-upload="'+kind+'"]')
                page.wait_for_selector('#signing-'+kind+'-preview:not(.hidden)')
                raw=page.request.get('http://127.0.0.1:8096/api/signing/image?kind='+kind)
                assert raw.ok and raw.body()==(directory/(kind+'.png')).read_bytes()
            page.click('#signing-save');page.wait_for_function("document.querySelector('#signing-message').textContent.includes('حفظت إعدادات')")
            status=page.request.get('http://127.0.0.1:8096/api/signing').json()
            assert all(status['assets'][kind] for kind in ['signature','stamp'])
            page.click('[data-close="signing-dialog"]');page.reload()
            page.click('#nav-signing');page.wait_for_selector('#signing-stamp-preview:not(.hidden)')
            page.click('[data-close="signing-dialog"]');page.click('#nav-forms')
            page.wait_for_selector('#form-manager[open]');page.wait_for_function("document.querySelector('#fm-field').querySelector('option[value=consultantStamp]')!==null")
            page.select_option('#fm-field','consultantStamp')
            page.wait_for_selector('[data-fm-number="x"]');assert page.locator('[data-fm-number="fontSize"]').count()==0
            page.locator('[data-fm-number="x"]').fill('40')
            page.locator('[data-fm-number="width"]').fill('150')
            page.click('#fm-save');page.wait_for_function("document.querySelector('#fm-dirty').textContent==='لا توجد تعديلات غير محفوظة'")
            state=page.request.get('http://127.0.0.1:8096/api/state').json()
            assert state['settings']['formConfigs']['01']['images']['consultantStamp']['box'][0]==40
            page.select_option('#fm-field','consultantSignature');page.wait_for_selector('#fm-manage-signing')
            page.click('#fm-manage-signing');page.wait_for_selector('#signing-dialog[open]')
            page.click('[data-signing-delete="signature"]');page.wait_for_selector('#signing-signature-empty:not(.hidden)')
            assert page.request.get('http://127.0.0.1:8096/api/signing').json()['assets']['signature'] is None
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            assert not errors,errors
            browser.close();print('PASS: private original-image upload, reload, enable settings, image placement/resize, nested dialogs, delete, mobile')
    except Exception:
        print((directory/'server.log').read_text()[-5000:]);raise
    finally:server.terminate();server.wait(timeout=5)
