"""Real edit/reload of panel and termination checks, including saved fixed defaults."""
from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1];URL='http://127.0.0.1:8093'
ARTIFACTS=Path('/workspace/output/forms-web');ARTIFACTS.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory() as tmp:
    env=dict(os.environ,DATA_DIR=tmp,PORT='8093',DATABASE_URL='',APP_PASSWORD='')
    server=subprocess.Popen([sys.executable,'-m','app.server'],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for _ in range(80):
            try:urllib.request.urlopen(URL+'/health',timeout=1);break
            except OSError:time.sleep(.1)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True,executable_path=os.getenv('CHROMIUM_PATH','/usr/bin/chromium'))
            page=browser.new_page(viewport={'width':1440,'height':1050});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            schema=page.request.get(URL+'/api/schema').json();seed=page.request.get(URL+'/api/new').json()
            seed.update(uid='checks-a',id='264000001',date='2026-11-10',works=['panel','termination','joint'])
            other=deepcopy(seed);other.update(uid='checks-b',id='264000002')
            settings=schema['defaults'];settings['formConfigs']={
                '07':{'checks':{f'07.{i}':{'mode':'fixed','value':'نعم'} for i in range(1,14)}},
                '10':{'checks':{f'10-termination.{i}':{'mode':'fixed','value':'نعم'} for i in range(1,14)}}}
            assert page.request.post(URL+'/api/save',data={'orders':[seed,other],'settings':settings}).ok
            page.goto(URL);page.wait_for_function("document.querySelector('#stat-all').textContent==='2'")
            page.locator('[data-open="checks-a"]').click();page.wait_for_selector('#editor[open]')
            page.click('[data-tab="checks"]');page.select_option('#check-code','07')
            assert page.locator('.checklist-row').count()==13
            assert page.locator('.row-num').all_text_contents()[:4]==['1','2.1','2.2','3']
            assert '20 سم' in page.locator('.checklist-row').nth(1).inner_text()
            assert 'راجع نصه' not in page.locator('#editor-content').inner_text()
            assert page.locator('[data-check-index][disabled]').count()==0
            page.locator('[data-check-index="1"][data-check-value="لا"]').click()
            page.wait_for_function("document.querySelector('#save-state').textContent.includes('البيانات محفوظة')")
            page.click('[data-close="editor"]');page.reload()
            page.wait_for_function("document.querySelector('#stat-all').textContent==='2'")
            page.locator('[data-open="checks-a"]').click();page.wait_for_selector('#editor[open]')
            page.click('[data-tab="checks"]');page.select_option('#check-code','07')
            assert 'selected' in page.locator('[data-check-index="1"][data-check-value="لا"]').get_attribute('class')
            page.locator('[data-check-reset="1"]').click()
            page.wait_for_selector('[data-check-reset="1"]',state='detached')
            assert 'selected' in page.locator('[data-check-index="1"][data-check-value="نعم"]').get_attribute('class')
            page.click('[data-all-check="لا ينطبق"]')
            page.wait_for_function("document.querySelector('#save-state').textContent.includes('البيانات محفوظة')")
            assert page.locator('[data-check-value="لا ينطبق"].selected').count()==13
            page.select_option('#check-code','10-termination')
            assert page.locator('.checklist-row').count()==13
            assert 'ترتيب الأطوار' in page.locator('.checklist-row').nth(11).inner_text()
            assert 'راجع نصه' not in page.locator('#editor-content').inner_text()
            page.locator('[data-check-index="11"][data-check-value="لا"]').click()
            page.wait_for_function("document.querySelector('#save-state').textContent.includes('البيانات محفوظة')")
            page.screenshot(path=str(ARTIFACTS/'termination-checklist-desktop.png'))
            page.set_viewport_size({'width':390,'height':844})
            page.screenshot(path=str(ARTIFACTS/'termination-checklist-mobile.png'))
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            page.set_viewport_size({'width':1440,'height':1050})
            page.select_option('#check-code','10-joint')
            assert page.locator('.checklist-row').count()==14
            assert 'شهادة تأهيل' in page.locator('.checklist-row').first.inner_text()
            page.click('[data-close="editor"]');page.reload()
            page.wait_for_function("document.querySelector('#stat-all').textContent==='2'")
            state=page.request.get(URL+'/api/state').json();saved={o['uid']:o for o in state['orders']}
            assert saved['checks-a']['checkOverrides']['10-termination.12']=='لا'
            assert saved['checks-b']['checkOverrides']=={}
            assert state['settings']['formConfigs']==settings['formConfigs']
            assert page.request.post(URL+'/api/preview',data={'order':saved['checks-a'],'form':'10'}).ok
            page.locator('[data-open="checks-b"]').click();page.wait_for_selector('#editor[open]')
            page.click('[data-tab="checks"]');page.select_option('#check-code','10-termination')
            assert 'selected' in page.locator('[data-check-index="11"][data-check-value="نعم"]').get_attribute('class')
            assert not errors,errors
            browser.close();print('PASS: original panel/termination/joint labels and numbers, editable fixed defaults, individual/bulk changes, reset/reload, separate orders, PDF preview, mobile')
    finally:server.terminate();server.wait(timeout=5)
