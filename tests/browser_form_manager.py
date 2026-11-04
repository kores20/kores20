"""Real browser exercise of form rules, fixed values, drag, save, reset and reload."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
ARTIFACTS=Path('/workspace/output/forms-web');ARTIFACTS.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory() as tmp:
    env=dict(os.environ,DATA_DIR=tmp,PORT='8098')
    process=subprocess.Popen([sys.executable,'-m','app.server'],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=open(Path(tmp)/'server.log','w'))
    try:
        for _ in range(60):
            try:urllib.request.urlopen('http://127.0.0.1:8098/api/state',timeout=1);break
            except OSError:time.sleep(.1)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True,executable_path=os.getenv('CHROMIUM_PATH','/usr/bin/chromium'))
            page=browser.new_page(viewport={'width':1512,'height':1050});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto('http://127.0.0.1:8098');page.wait_for_selector('#empty:not(.hidden)')
            o=page.request.get('http://127.0.0.1:8098/api/new').json()
            o.pop('customFields',None)  # Orders saved before this feature must also work.
            o.update(id='264000001',date='2026-10-21',site='المروج',description='تركيب عداد',feeder='000609',permit='1216',mainBreaker='400',operator='مدخل البيانات',operatorId='109789',meters=[{'serial':'ECC2520842100000','breaker':'70','subscription':'000000'}])
            assert page.request.post('http://127.0.0.1:8098/api/save',data={'orders':[o]}).ok
            page.reload();page.wait_for_function("document.querySelector('#stat-all').textContent==='1'")
            page.click('#nav-forms');page.wait_for_selector('#form-manager[open]')
            page.wait_for_function("document.querySelector('#fm-field').options.length>0")
            page.select_option('#fm-model','LV')
            page.wait_for_function("document.querySelector('#fm-field').querySelector('option[value=representative]')!==null")
            page.select_option('#fm-field','representative');page.select_option('#fm-mode','fixed')
            page.fill('#fm-text','ميرزا');page.locator('[data-fm-number="fontSize"]').fill('28')
            # Real pointer dragging and resizing must change the saved rectangle.
            marker=page.locator('[data-fm-box="representative"]');bounds=marker.bounding_box()
            page.mouse.move(bounds['x']+bounds['width']/2,bounds['y']+bounds['height']/2)
            page.mouse.down();page.mouse.move(bounds['x']+bounds['width']/2+8,bounds['y']+bounds['height']/2,steps=5);page.mouse.up()
            handle=marker.locator('.fm-resize').bounding_box()
            page.mouse.move(handle['x']+handle['width']/2,handle['y']+handle['height']/2)
            page.mouse.down();page.mouse.move(handle['x']+handle['width']/2-5,handle['y']+handle['height']/2,steps=5);page.mouse.up()
            page.wait_for_function("document.querySelector('#fm-preview-state').textContent==='معاينة التعديلات الحالية'")
            page.wait_for_timeout(500)
            assert page.locator('#fm-error').is_hidden(),page.locator('#fm-error').inner_text()
            page.screenshot(path=str(ARTIFACTS/'form-manager-desktop.png'),full_page=True)
            page.select_option('#fm-field','LV.1');page.select_option('#fm-mode','fixed');page.select_option('#fm-check-value','لا')
            # Three distinct visibility modes, independently saved for each form.
            page.select_option('#fm-model','07');page.wait_for_function("document.querySelector('#fm-field').querySelector('option[value=feeder]')!==null")
            page.select_option('#fm-visibility','always')
            page.select_option('#fm-model','24');page.wait_for_function("document.querySelector('#fm-field').querySelector('option[value=\"24.9\"]')!==null")
            page.select_option('#fm-visibility','never')
            page.select_option('#fm-model','05');page.wait_for_function("document.querySelector('#fm-field').querySelector('option[value=excavationSummary]')!==null")
            page.select_option('#fm-visibility','works');page.uncheck('[data-fm-visibility-work="excavation"]');page.check('[data-fm-visibility-work="meter"]')
            page.click('#fm-save');page.wait_for_function("document.querySelector('#fm-dirty').textContent==='لا توجد تعديلات غير محفوظة'")
            saved=page.request.get('http://127.0.0.1:8098/api/state').json()
            cfg=saved['settings']['formConfigs'];lv=cfg['LV']
            assert lv['fields']['representative']['text']=='ميرزا'
            assert lv['fields']['representative']['fontSize']==28
            assert lv['fields']['representative']['box'][0]>108
            assert lv['fields']['representative']['box'][2]-lv['fields']['representative']['box'][0]<117
            assert lv['checks']['LV.1']=={'mode':'fixed','value':'لا'}
            assert saved['orders']==[o]
            status=page.request.post('http://127.0.0.1:8098/api/missing',data={'order':o}).json()
            assert '07' in status['forms'] and '05' in status['forms'] and '24' not in status['forms']
            assert status['lvChecks'][0]=='لا'
            # Settings survive a browser reload; custom text and a reset persist too.
            page.reload();page.click('#nav-forms');page.wait_for_selector('#form-manager[open]')
            page.select_option('#fm-model','LV');page.wait_for_function("document.querySelector('#fm-field').querySelector('option[value=representative]')!==null")
            page.select_option('#fm-field','representative');assert page.input_value('#fm-text')=='ميرزا'
            page.click('#fm-add-text');page.fill('#fm-text','ملاحظة ثابتة');page.click('#fm-save')
            page.wait_for_function("document.querySelector('#fm-dirty').textContent==='لا توجد تعديلات غير محفوظة'")
            page.select_option('#fm-model','24');page.wait_for_function("document.querySelector('#fm-field').querySelector('option[value=\"24.9\"]')!==null")
            page.click('#fm-reset');page.wait_for_function("document.querySelector('#fm-visibility').value==='works'")
            assert page.is_checked('[data-fm-visibility-work="meter"]')
            page.click('#fm-save');page.wait_for_function("document.querySelector('#fm-dirty').textContent==='لا توجد تعديلات غير محفوظة'")
            assert '24' in page.request.post('http://127.0.0.1:8098/api/missing',data={'order':o}).json()['forms']
            # Add a fillable field in the admin, then fill it on a legacy order.
            page.select_option('#fm-model','01');page.wait_for_function("document.querySelector('#fm-field').querySelector('option[value=handoverNotes]')!==null")
            page.click('#fm-add-field');page.fill('#fm-label','اسم الفني');page.check('#fm-required')
            page.fill('#fm-input-preview','اسم للمعاينة فقط')
            added_id=page.input_value('#fm-field')
            page.click('#fm-save');page.wait_for_function("document.querySelector('#fm-dirty').textContent==='لا توجد تعديلات غير محفوظة'")
            state=page.request.get('http://127.0.0.1:8098/api/state').json()
            assert 'customFields' not in state['orders'][0]
            page.click('[data-close="form-manager"]')
            page.locator('[data-open]').click();page.wait_for_selector('#editor[open]')
            page.click('[data-tab="custom"]')
            selector=f'[data-field="customFields.01.{added_id}"]'
            page.fill(selector,'محمد الفني');page.wait_for_timeout(800)
            stored=page.request.get('http://127.0.0.1:8098/api/state').json()['orders'][0]
            assert isinstance(stored['customFields'],dict) and stored['customFields']['01'][added_id]=='محمد الفني'
            page.click('[data-close="editor"]');page.reload()
            page.wait_for_function("document.querySelector('#stat-all').textContent==='1'")
            page.locator('[data-open]').click();page.click('[data-tab="custom"]')
            assert page.input_value(selector)=='محمد الفني'
            page.click('[data-close="editor"]')
            # Reader font size is adjustable and remembered on this device.
            page.select_option('#ui-font-size','20');page.reload()
            page.wait_for_function("document.querySelector('#ui-font-size').value==='20'")
            assert page.evaluate("getComputedStyle(document.documentElement).fontSize")=='20px'
            page.select_option('#ui-font-size','16')
            page.click('#nav-forms');page.wait_for_selector('#form-manager[open]')
            # Narrow-screen controls remain usable and the page does not overflow.
            page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(ARTIFACTS/'form-manager-mobile.png'),full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            assert not errors,errors
            browser.close()
            print('PASS: form manager, drag/resize, fixed checks, visibility, persistence, fillable custom fields, legacy orders, reader font size, reset, mobile')
    except Exception:
        print((Path(tmp)/'server.log').read_text()[-8000:])
        raise
    finally:process.terminate();process.wait(timeout=5)
