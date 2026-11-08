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
    for kind in ['signature','stamp','faisal']:
        im=Image.new('RGBA',(220,100),(255,255,255,0));draw=ImageDraw.Draw(im)
        if kind=='signature':draw.line([(20,60),(60,25),(100,65),(180,35)],fill='blue',width=4)
        elif kind=='stamp':draw.rectangle((20,15,200,85),outline='blue',width=4);draw.text((70,40),'TEST ONLY',fill='blue')
        else:draw.line([(20,25),(70,70),(140,35),(200,60)],fill='red',width=3)
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
            import base64
            # A pre-upgrade upload must remain available without re-uploading it.
            assert page.request.post('http://127.0.0.1:8096/api/signing/upload',data={
                'kind':'signature','base64':base64.b64encode((directory/'signature.png').read_bytes()).decode()}).ok
            page.click('#nav-signing');page.wait_for_selector('#signing-dialog[open]')
            page.wait_for_function("document.querySelector('#signing-owner').value==='علي الشهري'")
            page.wait_for_selector('#signing-signature-preview:not(.hidden)')
            for kind in ['signature','stamp']:
                page.set_input_files('#signing-'+kind+'-file',directory/(kind+'.png'))
                page.click('[data-signing-upload="'+kind+'"]')
                page.wait_for_selector('#signing-'+kind+'-preview:not(.hidden)')
                asset_kind='signature-ali' if kind=='signature' else kind
                page.wait_for_function('(kind)=>document.querySelector("#signing-"+(kind==="signature-ali"?"signature":"stamp")+"-preview").src.includes("kind="+kind)',arg=asset_kind)
                raw=page.request.get('http://127.0.0.1:8096/api/signing/image?kind='+asset_kind)
                assert raw.ok and raw.body()==(directory/(kind+'.png')).read_bytes()
            page.select_option('#signing-owner','فيصل العمري')
            page.wait_for_selector('#signing-signature-empty:not(.hidden)')
            assert 'فيصل العمري' in page.locator('#signing-signature-empty').inner_text()
            assert page.locator('#signing-signature-preview').get_attribute('src') is None
            page.set_input_files('#signing-signature-file',directory/'faisal.png')
            page.click('[data-signing-upload="signature"]')
            page.wait_for_selector('#signing-signature-preview:not(.hidden)')
            assert page.request.get('http://127.0.0.1:8096/api/signing/image?kind=signature-faisal').body()==(directory/'faisal.png').read_bytes()
            page.click('#signing-save');page.wait_for_function("document.querySelector('#signing-message').textContent.includes('حفظت إعدادات')")
            status=page.request.get('http://127.0.0.1:8096/api/signing').json()
            assert all(status['assets'][kind] for kind in ['signature-ali','signature-faisal','stamp'])
            assert status['config']['owner']=='علي الشهري'
            page.select_option('#signing-owner','علي الشهري')
            page.wait_for_function("document.querySelector('#signing-signature-preview').src.includes('kind=signature-ali')")
            page.click('[data-close="signing-dialog"]');page.reload()
            page.click('#nav-signing');page.wait_for_selector('#signing-stamp-preview:not(.hidden)')
            page.click('[data-close="signing-dialog"]')
            # Consultant is chosen per order and defaults only affect new orders.
            page.click('#new-order');page.wait_for_selector('#editor[open]')
            assert page.input_value('[data-field="inspector"]')=='علي الشهري'
            page.select_option('[data-field="inspector"]','فيصل العمري')
            page.wait_for_function("document.querySelector('#save-state').textContent.includes('البيانات محفوظة')")
            page.click('[data-close="editor"]');page.reload()
            page.wait_for_function("document.querySelector('#stat-all').textContent==='1'")
            page.locator('[data-open]').click();page.wait_for_selector('#editor[open]')
            assert page.input_value('[data-field="inspector"]')=='فيصل العمري'
            page.click('[data-close="editor"]');page.click('#new-order');page.wait_for_selector('#editor[open]')
            assert page.input_value('[data-field="inspector"]')=='علي الشهري'
            page.click('[data-close="editor"]')
            page.click('#nav-defaults');page.wait_for_selector('#defaults-dialog[open]')
            page.select_option('#defaults-content [data-field="inspector"]','فيصل العمري')
            page.click('#defaults-save');page.wait_for_function("!document.querySelector('#defaults-dialog').open")
            page.click('#new-order');page.wait_for_selector('#editor[open]')
            assert page.input_value('[data-field="inspector"]')=='فيصل العمري'
            page.click('[data-close="editor"]')
            names=[o['inspector'] for o in page.request.get('http://127.0.0.1:8096/api/state').json()['orders']]
            assert sorted(names)==sorted(['فيصل العمري','علي الشهري','فيصل العمري'])
            page.click('#nav-forms')
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
            page.wait_for_function("document.querySelector('#signing-owner').options.length===2")
            page.select_option('#signing-owner','علي الشهري')
            page.click('[data-signing-delete="signature"]');page.wait_for_selector('#signing-signature-empty:not(.hidden)')
            status=page.request.get('http://127.0.0.1:8096/api/signing').json()
            assert status['assets']['signature'] is None and status['assets']['signature-ali'] is None
            assert status['assets']['signature-faisal'] is not None and status['assets']['stamp'] is not None
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            assert not errors,errors
            browser.close();print('PASS: separate consultant choices/signatures, legacy upload retained, per-order save/defaults/reload, independent deletion, private images, placement, mobile')
    except Exception:
        print((directory/'server.log').read_text()[-5000:]);raise
    finally:server.terminate();server.wait(timeout=5)
