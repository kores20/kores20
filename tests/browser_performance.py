"""Latency-injected browser benchmark; PDF generation is excluded from timing.

PERF_ROOT can point at an older checkout to compare the same interactions.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

ROOT=Path(os.getenv('PERF_ROOT',Path(__file__).resolve().parents[1]))
BASELINE=bool(os.getenv('PERF_BASELINE'))
URL='http://127.0.0.1:8095'
with tempfile.TemporaryDirectory() as tmp:
    env=dict(os.environ,DATA_DIR=tmp,PORT='8095',DATABASE_URL='',APP_PASSWORD='')
    server=subprocess.Popen([sys.executable,'-m','app.server'],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:urllib.request.urlopen(URL+'/health',timeout=1);break
            except OSError:time.sleep(.1)
        with sync_playwright() as pw:
            options={'headless':True}
            if os.getenv('CHROMIUM_PATH'):options['executable_path']=os.environ['CHROMIUM_PATH']
            browser=pw.chromium.launch(**options);page=browser.new_page()
            seed=page.request.get(URL+'/api/new').json()
            orders=[dict(seed,uid=f'perf-{i}',id=str(264000000+i),site='المروج') for i in range(30)]
            assert page.request.post(URL+'/api/save',data={'orders':orders}).ok
            requests=[];held=[];hold=False;fail=False;errors=[]
            def route(request):
                path=urlparse(request.request.url).path
                requests.append(path)
                if path=='/api/save' and hold:
                    held.append(request);return
                if path=='/api/save' and fail:
                    request.fulfill(status=503,json={'error':'اختبار انقطاع مؤقت'});return
                time.sleep(.1)  # Simulate 100 ms of network latency per API request.
                if path=='/api/export':request.fulfill(status=202,json={'job':'benchmark'})
                elif path=='/api/jobs/benchmark':request.fulfill(json={'state':'done','done':30,'total':30,'filename':'benchmark.zip'})
                elif path=='/api/download/benchmark':request.fulfill(body=b'benchmark',content_type='application/zip',headers={'Content-Disposition':'attachment; filename="benchmark.zip"'})
                else:request.continue_()
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('**/api/**',route)
            page.goto(URL);page.wait_for_function("document.querySelector('#stat-all').textContent==='30'")
            page.wait_for_timeout(350)
            startup_missing=requests.count('/api/missing-batch');requests.clear()
            start=time.monotonic();page.locator('[data-open="perf-0"]').click();page.wait_for_selector('#editor[open]')
            for tab in ['checks','forms','meters','data','checks','data']:
                page.click(f'[data-tab="{tab}"]')
                page.wait_for_function('(tab)=>document.querySelector("#editor-tabs .selected").dataset.tab===tab',arg=tab)
            navigation_seconds=time.monotonic()-start;navigation_requests=len(requests);requests.clear()
            page.fill('[data-field="permit"]','1216');page.locator('[data-field="permit"]').press('Tab')
            page.wait_for_function("document.querySelector('#save-state').textContent.includes('البيانات محفوظة')")
            page.wait_for_timeout(700)
            save_requests=requests.count('/api/save');missing_requests=requests.count('/api/missing');requests.clear()
            if not BASELINE:
                # Keep the first save in flight while a newer edit is queued.
                hold=True
                page.fill('[data-field="permit"]','1111');page.locator('[data-field="permit"]').press('Tab')
                page.wait_for_function("document.querySelector('#save-state').textContent.includes('جارٍ الحفظ')")
                page.wait_for_timeout(150);assert len(held)==1
                page.fill('[data-field="permit"]','2222');page.locator('[data-field="permit"]').press('Tab')
                hold=False;held.pop().continue_()
                page.wait_for_function("document.querySelector('#save-state').textContent.includes('البيانات محفوظة')")
                stored=page.request.get(URL+'/api/state').json()['orders']
                assert next(o for o in stored if o['uid']=='perf-0')['permit']=='2222'
                # Failed saves remain visibly unsaved and retry with the latest edit.
                fail=True
                page.fill('[data-field="permit"]','3333');page.locator('[data-field="permit"]').press('Tab')
                page.wait_for_function("document.querySelector('#save-state').textContent.includes('لم يتم الحفظ')")
                fail=False
                page.fill('[data-field="permit"]','4444');page.locator('[data-field="permit"]').press('Tab')
                page.wait_for_function("document.querySelector('#save-state').textContent.includes('البيانات محفوظة')")
                stored=page.request.get(URL+'/api/state').json()['orders']
                assert next(o for o in stored if o['uid']=='perf-0')['permit']=='4444'
                requests.clear()
            page.click('[data-close="editor"]');page.wait_for_timeout(150);requests.clear()
            # Force 30 changed drafts; only measure saving before the export job.
            page.evaluate("orders.forEach(o=>o.description+=' اختبار')")
            start=time.monotonic()
            with page.expect_download(timeout=30000):page.evaluate('exportOrders(orders)')
            export_seconds=time.monotonic()-start;export_save_requests=requests.count('/api/save');export_missing_requests=requests.count('/api/missing')
            if not BASELINE:
                assert startup_missing==0
                assert navigation_requests==0,navigation_requests
                assert save_requests==1 and missing_requests==0,(save_requests,missing_requests)
                assert export_save_requests==1 and export_missing_requests==0
                assert not errors,errors
            print(json.dumps({'baseline':BASELINE,'simulated_api_latency_ms':100,
                'navigation_requests':navigation_requests,'navigation_seconds':round(navigation_seconds,2),
                'single_edit_save_requests':save_requests,'single_edit_missing_requests':missing_requests,
                'batch30_save_requests':export_save_requests,'batch30_missing_requests':export_missing_requests,
                'batch30_before_pdf_seconds':round(export_seconds,2)},ensure_ascii=False))
            browser.close()
    finally:server.terminate();server.wait(timeout=5)
