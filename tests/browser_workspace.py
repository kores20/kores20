"""Real missing-data editing and export/delivery lifecycle, isolated user data."""
from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1];URL='http://127.0.0.1:8094'
ARTIFACTS=Path('/workspace/output/forms-web');ARTIFACTS.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory() as tmp:
    env=dict(os.environ,DATA_DIR=tmp,PORT='8094',DATABASE_URL='',APP_PASSWORD='')
    log=open(Path(tmp)/'server.log','w')
    server=subprocess.Popen([sys.executable,'-m','app.server'],cwd=ROOT,env=env,stdout=log,stderr=log)
    try:
        for _ in range(80):
            try:urllib.request.urlopen(URL+'/health',timeout=1);break
            except OSError:time.sleep(.1)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True,executable_path=os.getenv('CHROMIUM_PATH','/usr/bin/chromium'))
            page=browser.new_page(viewport={'width':1500,'height':1050});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            seed=page.request.get(URL+'/api/new').json()
            seed.update(date='2026-11-08',site='المروج',feeder='000609',description='تركيب عداد',permit='1216',
                mainBreaker='400',voltage='230',operator='مدخل اختبار',operatorId='109789',
                meters=[{'serial':'ECC2520842109034','breaker':'70','subscription':'4222_0002_00_717649'}],
                customFields={'01':{'custom-reading':'existing'}})
            orders=[]
            for i in range(4):
                o=deepcopy(seed);o.update(uid=f'workspace-{i}',id=str(264800001+i));orders.append(o)
            orders[0].update(feeder='',operatorId='');orders[0]['assessments']['inspection'][2]=''
            orders[1].update(date='',inspector='');orders[1]['customFields']={}
            orders[2]['works']=[];orders[3]['meters']=[]
            config={'fields':{'custom-reading':{'mode':'input','label':'رقم السجل الإضافي','required':True,'inputType':'text','box':[40,400,140,420]}}}
            assert page.request.post(URL+'/api/forms/save',data={'code':'01','config':config}).ok
            assert page.request.post(URL+'/api/save',data={'orders':orders}).ok
            page.goto(URL);page.wait_for_function("document.querySelector('#stat-all').textContent==='4'")
            page.click('#nav-missing');page.wait_for_selector('#missing-view:not(.hidden)')
            def row(uid,key):return page.locator(f'tr[data-missing-uid="{uid}"][data-missing-key="{key}"]')
            def save(uid,key,value):
                r=row(uid,key);field=r.locator('[data-missing-input]')
                if field.evaluate('(el)=>el.tagName')=='SELECT':field.select_option(value)
                else:field.fill(value)
                r.locator('[data-missing-save]').click();r.wait_for(state='detached')
            # Keep an unsaved field while a different row is saved and re-rendered.
            row('workspace-0','operatorId').locator('input').fill('00109789')
            assert 'بانتظار الحفظ' in page.locator('#save-state').inner_text()
            save('workspace-0','feeder','000609')
            assert row('workspace-0','operatorId').locator('input').input_value()=='00109789'
            row('workspace-0','operatorId').locator('[data-missing-save]').click()
            row('workspace-0','operatorId').wait_for(state='detached')
            save('workspace-0','assessments.inspection.2','لا')
            save('workspace-1','date','2026-11-08');save('workspace-1','inspector','فيصل العمري')
            save('workspace-1','customFields.01.custom-reading','000123')
            # Conditional works and meter questions become available immediately.
            row('workspace-2','works').locator('input[value="panel"]').check()
            row('workspace-2','works').locator('[data-missing-save]').click();row('workspace-2','works').wait_for(state='detached')
            save('workspace-3','meters','2')
            assert row('workspace-3','meters.0.serial').count()==1 and row('workspace-3','meters.1.serial').count()==1
            row('workspace-3','meters.0.subscription').locator('[data-missing-skip]').click()
            row('workspace-3','meters.0.subscription').wait_for(state='detached')
            page.fill('#missing-search','264800004')
            assert all('264800004' in x for x in page.locator('#missing-body .missing-order-link').all_text_contents())
            page.fill('#missing-search','');page.select_option('#missing-section','العدادات')
            assert page.locator('#missing-body tr').count()>0
            page.screenshot(path=str(ARTIFACTS/'missing-dashboard-desktop.png'))
            page.set_viewport_size({'width':390,'height':844})
            page.screenshot(path=str(ARTIFACTS/'missing-dashboard-mobile.png'))
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            page.set_viewport_size({'width':1500,'height':1050})
            page.reload();page.wait_for_function("document.querySelector('#stat-all').textContent==='4'")
            state=page.request.get(URL+'/api/state').json();stored={o['uid']:o for o in state['orders']}
            assert stored['workspace-0']['feeder']=='000609' and stored['workspace-0']['operatorId']=='00109789'
            assert stored['workspace-1']['inspector']=='فيصل العمري' and stored['workspace-1']['customFields']['01']['custom-reading']=='000123'
            assert stored['workspace-2']['works']==['panel'] and len(stored['workspace-3']['meters'])==2
            assert 'meters.0.subscription' in stored['workspace-3']['skipped']
            page.click('#nav-missing');assert row('workspace-3','meters.0.subscription').count()==0
            page.click('#missing-back');page.locator('[data-open="workspace-0"]').click()
            page.wait_for_selector('#editor[open]');assert 'جاهز للتصدير' in page.locator('#editor-workflow').inner_text()
            with page.expect_download(timeout=30000):page.click('#export-order')
            page.wait_for_function("document.querySelector('#editor-workflow .workflow-badge').textContent==='تم التصدير'")
            page.locator('#editor-workflow [data-delivery-stage="delivered"]').click()
            page.wait_for_function("document.querySelector('#editor-workflow .workflow-badge').textContent==='تم التسليم'")
            page.fill('[data-field="permit"]','1217');page.locator('[data-field="permit"]').press('Tab')
            page.wait_for_function("document.querySelector('#editor-workflow .workflow-badge').textContent==='عُدّل بعد التصدير'")
            assert page.locator('#editor-workflow [data-delivery-stage="delivered"]').count()==0
            page.click('[data-close="editor"]');page.select_option('#stage-filter','needs_export')
            assert page.locator('#orders-body tr').count()==1
            page.screenshot(path=str(ARTIFACTS/'workflow-tracking-desktop.png'))
            page.reload();page.wait_for_function("document.querySelector('#stat-all').textContent==='4'")
            assert page.request.get(URL+'/api/state').json()['statuses']['workspace-0']['workflow']['stage']=='needs_export'
            # Preview alone must not count as a new export; JSON backup retains tracking.
            current=next(o for o in page.request.get(URL+'/api/state').json()['orders'] if o['uid']=='workspace-0')
            assert page.request.post(URL+'/api/preview',data={'order':current,'form':'inspection'}).ok
            assert page.request.get(URL+'/api/state').json()['statuses']['workspace-0']['workflow']['stage']=='needs_export'
            backup=page.request.get(URL+'/api/backup').json();assert backup['progress']['workspace-0']['exportFingerprint']
            # Large question lists are paginated and retain edits between pages.
            extra=deepcopy(seed);extra.update(uid='workspace-large',id='264800005',works=['transformer','rmu','termination','earthing','panel','excavation'])
            assert page.request.post(URL+'/api/save',data={'orders':[extra]}).ok
            page.reload();page.wait_for_function("document.querySelector('#stat-all').textContent==='5'")
            page.click('#nav-missing')
            assert page.locator('#missing-body tr').count()==40
            row('workspace-large','transformerKva').locator('input').fill('777')
            page.click('#missing-next');assert row('workspace-large','transformerKva').count()==0
            page.click('#missing-prev');assert row('workspace-large','transformerKva').locator('input').input_value()=='777'
            assert not errors,errors
            print('PASS: missing text/date/choice/checks/works/meters/custom fields, draft retention, skip/search/mobile, real PDF export/delivery/edit warning, reload/backup, no JS errors')
            browser.close()
    except Exception:
        log.flush();print((Path(tmp)/'server.log').read_text()[-5000:]);raise
    finally:server.terminate();server.wait(timeout=5);log.close()
