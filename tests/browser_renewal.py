"""Upload, review, correct, reload and download renewed forms in a real browser."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

import fitz
from playwright.sync_api import sync_playwright

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import pdf_engine

ROOT=Path(__file__).resolve().parents[1];URL='http://127.0.0.1:8092'
ARTIFACTS=Path('/workspace/output/forms-web');ARTIFACTS.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory() as tmp:
    directory=Path(tmp);path=directory/'264000001-old.pdf'
    with fitz.open(pdf_engine.template_path('07')) as original:
        document=fitz.open();document.insert_pdf(original);page=document[0]
        page.insert_text((30,30),'OLD ONLY MARKER',fontsize=8)
        page.insert_text((420,154),'264000001',fontsize=10,color=pdf_engine.BLUE)
        pdf_engine.tick(page,350,216)
        attachment=document.new_page();attachment.insert_text((70,100),'Attachment must remain unchanged')
        document.save(path);document.close()
    source_sha=hashlib.sha256(path.read_bytes()).hexdigest()
    env=dict(os.environ,DATA_DIR=str(directory/'data'),PORT='8092',DATABASE_URL='',APP_PASSWORD='')
    log=open(directory/'server.log','w')
    server=subprocess.Popen([sys.executable,'-m','app.server'],cwd=ROOT,env=env,stdout=log,stderr=log)
    try:
        for _ in range(80):
            try:urllib.request.urlopen(URL+'/health',timeout=1);break
            except OSError:time.sleep(.1)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True,executable_path=os.getenv('CHROMIUM_PATH','/usr/bin/chromium'))
            page=browser.new_page(viewport={'width':1600,'height':1100});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(URL);page.wait_for_selector('#empty:not(.hidden)');page.click('#nav-renewal')
            page.wait_for_selector('#renewal-view:not(.hidden)')
            page.set_input_files('#rn-file',path);page.click('#rn-upload')
            page.wait_for_selector('#rn-review:not(.hidden)',timeout=90000)
            page.wait_for_function("document.querySelector('#rn-review').inert===false")
            assert page.locator('#rn-page option').count()==2
            assert page.input_value('#rn-model')=='07'
            assert page.is_disabled('#rn-export')
            page.select_option('#rn-field','id');page.select_option('#rn-mode','text')
            page.fill('#rn-text','264000009')
            page.wait_for_function("document.querySelector('#rn-save-state').textContent==='حُفظت التعديلات'")
            # Drag the new field; source file stays unchanged.
            box=page.locator('#rn-target-box').bounding_box();assert box
            page.mouse.move(box['x']+box['width']/2,box['y']+box['height']/2);page.mouse.down();page.mouse.move(box['x']+box['width']/2+8,box['y']+box['height']/2+3,steps=4);page.mouse.up()
            page.wait_for_function("document.querySelector('#rn-save-state').textContent==='حُفظت التعديلات'")
            page.select_option('#rn-field','07.1');page.select_option('#rn-mode','نعم')
            page.check('#rn-confirm')
            page.wait_for_function("rn.page.confirmed===true")
            page.click('#rn-next');page.wait_for_function("rn.page.index===1&&!rn.loading")
            assert page.is_checked('#rn-keep-original')
            assert 'لم نتعرف' in page.locator('#rn-page-warning').inner_text()
            page.check('#rn-confirm');page.wait_for_function("rn.page.confirmed===true")
            page.click('#rn-prev');page.wait_for_function("rn.page.index===0&&!rn.loading")
            page.screenshot(path=str(ARTIFACTS/'renewal-desktop.png'))
            page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(ARTIFACTS/'renewal-mobile.png'))
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            page.set_viewport_size({'width':1600,'height':1100})
            page.reload();page.wait_for_selector('#empty:not(.hidden)');page.click('#nav-renewal')
            page.wait_for_selector('#rn-review:not(.hidden)');page.wait_for_function("rn.page&&rn.page.confirmed&&!rn.loading")
            page.select_option('#rn-field','id');assert page.input_value('#rn-text')=='264000009'
            key=page.evaluate('rn.id')
            stored=page.request.get(URL+'/api/renewal/'+key+'/page?page=0').json()
            assert next(f for f in stored['fields'] if f['id']=='id')['text']=='264000009'
            original=page.request.get(URL+'/api/renewal/'+key+'/original').body()
            assert hashlib.sha256(original).hexdigest()==source_sha
            with page.expect_download(timeout=60000) as download:page.click('#rn-export')
            result=ARTIFACTS/'renewal-browser.pdf';download.value.save_as(result)
            with fitz.open(result) as output:
                assert len(output)==2
                assert 'OLD ONLY MARKER' not in output[0].get_text()
                assert 'Attachment must remain unchanged' in output[1].get_text()
            assert page.request.get(URL+'/api/state').json()['orders']==[]
            page.wait_for_function('!rn.loading')
            page.click('#rn-delete');page.wait_for_selector('#rn-review',state='hidden');assert page.locator('.rn-upload').is_visible()
            assert page.request.get(URL+'/api/renewal/'+key).status==400
            assert not errors,errors
            browser.close();print('PASS: PDF upload/matching, ink/check transfer, text correction, drag, per-page review, unknown page retained, reload, real download, original hash, no order changes, delete, mobile')
    except Exception:
        log.flush();print((directory/'server.log').read_text()[-3000:]);raise
    finally:server.terminate();server.wait(timeout=5);log.close()
