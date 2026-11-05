"""Real browser test, isolated database; does not change a user's orders."""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
ARTIFACTS=Path('/workspace/output/forms-web');ARTIFACTS.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory() as tmp:
    env=dict(os.environ,DATA_DIR=tmp,PORT='8097')
    server=subprocess.Popen([sys.executable,'-m','app.server'],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:urllib.request.urlopen('http://127.0.0.1:8097/api/state',timeout=1);break
            except OSError:time.sleep(.1)
        with sync_playwright() as pw:
            options={'headless':True}
            if os.getenv('CHROMIUM_PATH'):options['executable_path']=os.environ['CHROMIUM_PATH']
            browser=pw.chromium.launch(**options);page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto('http://127.0.0.1:8097');page.wait_for_selector('#empty:not(.hidden)')
            page.click('#new-order');page.wait_for_selector('#editor[open]')
            page.locator('[data-field="id"]').fill('264046574')
            page.locator('[data-field="date"]').fill('2026-11-03')
            page.locator('[data-field="permit"]').fill('1216')
            page.locator('[data-field="description"]').fill('حفر وتمديد')
            page.locator('[data-field="mode"]').select_option('inspection')
            page.wait_for_timeout(900)
            page.click('[data-tab="checks"]');page.wait_for_selector('[data-check-index="0"]')
            page.locator('[data-check-index="0"][data-check-value="لا"]').click()
            page.locator('[data-close="editor"]').click();page.wait_for_timeout(500)
            assert page.locator('#stat-all').inner_text()=='1'
            page.reload();page.wait_for_function("document.querySelector('#stat-all').textContent==='1'")
            page.click('#import-open')
            csv='رقم أمر العمل,رمز العمل,المدينة,الحي,المقاول,تاريخ التنفيذ,رقم المغذي\n'
            csv+='\n'.join(f'{264100000+i},801,أبها,المروج,أفق البنية,2026-10-21,000609' for i in range(30))
            page.fill('#import-text',csv);page.click('#import-submit')
            page.wait_for_function("document.querySelector('#stat-all').textContent==='31'")
            page.wait_for_function("!document.querySelector('#import-dialog').open")
            page.locator('#search').fill('264046574');page.wait_for_timeout(150)
            page.locator('[data-open]').click();page.wait_for_selector('#editor[open]')
            with page.expect_download(timeout=30000) as download:
                page.click('#export-order')
            assert download.value.suggested_filename=='264046574.pdf'
            page.locator('[data-close="editor"]').click()
            # Missing-data wizard: explicit blank decisions persist; work selection works.
            candidate=page.request.get('http://127.0.0.1:8097/api/new').json()
            candidate.update(id='264900001',date='2026-10-21',description='أعمال موقع',permit='1216',works=[])
            page.request.post('http://127.0.0.1:8097/api/save',data={'orders':[candidate]})
            page.reload();page.wait_for_function("document.querySelector('#stat-all').textContent==='32'")
            page.locator('#search').fill('264900001');page.locator('[data-wizard]').click()
            page.wait_for_selector('#wizard[open]');page.wait_for_function("document.querySelector('#wizard-content').dataset.key==='site'");page.click('#wizard-skip');page.wait_for_function("document.querySelector('#wizard-content').dataset.key==='feeder'");page.click('#wizard-skip')
            page.wait_for_selector('[data-wizard-work="panel"]');page.check('[data-wizard-work="panel"]');page.click('#wizard-next')
            page.wait_for_function("document.querySelector('#wizard-content').dataset.kind==='assessment'")
            saved=page.request.get('http://127.0.0.1:8097/api/state').json()['orders']
            edited=next(o for o in saved if o['id']=='264900001')
            assert edited['works']==['panel'] and 'site' in edited['skipped'] and 'feeder' in edited['skipped']
            page.click('[data-close="wizard"]')
            # LV remains automatic, without work-selection or checklist fields.
            page.locator('[data-open]').click();page.wait_for_selector('#editor[open]')
            page.click('[data-tab="checks"]');page.wait_for_selector('#check-code')
            assert page.locator('#check-code option[value="LV"]').count()==0
            automatic=page.request.post('http://127.0.0.1:8097/api/missing',data={'order':edited}).json()
            assert [i+1 for i,v in enumerate(automatic['lvChecks']) if v=='نعم']==[5,6,7,8,9,10]
            assert not any(q['key'].startswith('assessments.LV.') for q in automatic['questions'])
            page.click('[data-tab="forms"]');page.wait_for_selector('[data-preview="LV"]')
            page.click('[data-tab="data"]')
            assert page.locator('[data-work="lv"]').count()==0
            page.check('[data-work="meter"]')
            page.wait_for_function("document.querySelector('#save-state').textContent.includes('البيانات محفوظة')")
            page.click('[data-tab="checks"]');page.wait_for_selector('#check-code')
            assert page.locator('#check-code option[value="LV"]').count()==0
            updated=next(o for o in page.request.get('http://127.0.0.1:8097/api/state').json()['orders'] if o['id']=='264900001')
            automatic=page.request.post('http://127.0.0.1:8097/api/missing',data={'order':updated}).json()
            assert [i+1 for i,v in enumerate(automatic['lvChecks']) if v=='نعم']==list(range(5,15))
            page.click('[data-tab="meters"]');page.wait_for_selector('[data-field="mainBreaker"]')
            page.select_option('[data-field="voltage"]','230')
            page.wait_for_function("document.querySelector('#save-state').textContent.includes('البيانات محفوظة')")
            stored=next(o for o in page.request.get('http://127.0.0.1:8097/api/state').json()['orders'] if o['id']=='264900001')
            assert stored['voltage']=='230'
            assert page.locator('[data-field="date190"], [data-field="date200"]').count()==0
            assert not any(q['key'] in ['date190','date200'] for q in automatic['questions'])
            page.click('[data-close="editor"]')
            # Missing voltage is a two-choice question, not a hidden 400V default.
            stored['voltage']='';stored['skipped']+=['mainBreaker','operator','operatorId','meters']
            page.request.post('http://127.0.0.1:8097/api/save',data={'orders':[stored]})
            page.reload();page.wait_for_function("document.querySelector('#stat-all').textContent==='32'")
            page.fill('#search','264900001');page.locator('[data-wizard]').click()
            page.wait_for_function("document.querySelector('#wizard-content').dataset.kind==='voltage'")
            page.click('[data-answer="400"]')
            page.wait_for_function("document.querySelector('#wizard-content').dataset.kind==='assessment'")
            latest=next(o for o in page.request.get('http://127.0.0.1:8097/api/state').json()['orders'] if o['id']=='264900001')
            assert latest['voltage']=='400'
            page.click('[data-close="wizard"]')
            page.locator('#search').fill('');page.evaluate('window.scrollTo(0,0)')
            page.screenshot(path=str(ARTIFACTS/'website-redesign-desktop.png'))
            page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(ARTIFACTS/'website-redesign-mobile.png'))
            assert not errors,errors
            # Horizontal table scrolling is intentional; the page itself must fit.
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            assert '000609' in page.request.get('http://127.0.0.1:8097/api/state').text()
            print('PASS: create/edit/save/reload, checklist, import30, realPDFdownload, automatic LV rules, mobile, noJSerrors')
            browser.close()
    finally:server.terminate();server.wait(timeout=5)
