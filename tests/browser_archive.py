"""Archive flow using an isolated database; no private PDF templates needed."""
import os, subprocess, tempfile, time, urllib.request
from playwright.sync_api import sync_playwright
from pathlib import Path

os.chdir(Path(__file__).resolve().parents[1])
with tempfile.TemporaryDirectory() as tmp:
    proc=subprocess.Popen(['python','-m','app.server'],env=dict(os.environ,DATA_DIR=tmp,PORT='8098'),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try: urllib.request.urlopen('http://127.0.0.1:8098/api/state'); break
            except OSError: time.sleep(.1)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
            page=browser.new_page(); errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto('http://127.0.0.1:8098');page.click('#new-order')
            page.locator('[data-field="id"]').fill('264123456');page.locator('[data-field="id"]').blur()
            page.locator('[data-field="archived"]').check()
            page.wait_for_timeout(800)
            page.click('[data-close="editor"]')
            assert page.locator('#orders-body tr').count()==0
            assert page.locator('#side-count').inner_text()=='0'
            assert page.locator('#archive-count').inner_text()=='1'
            page.click('#nav-missing')
            assert page.locator('#missing-body tr').count()==0
            page.click('#nav-archive')
            assert page.locator('#orders-body tr').count()==1
            page.locator('#search').fill('264123456')
            assert page.locator('#orders-body tr').count()==1
            page.reload();page.click('#nav-archive')
            assert page.locator('#orders-body tr').count()==1
            state=page.request.get('http://127.0.0.1:8098/api/state').json()
            assert state['orders'][0]['archived'] is True
            backup=page.request.get('http://127.0.0.1:8098/api/backup').json()
            assert backup['orders'][0]['archived'] is True
            page.locator('[data-open]').first.click()
            page.locator('[data-field="archived"]').uncheck();page.wait_for_timeout(800)
            page.click('[data-close="editor"]')
            assert page.locator('#orders-body tr').count()==0
            page.click('#nav-orders')
            assert page.locator('#orders-body tr').count()==1
            page.click('#nav-missing');assert page.locator('#missing-body tr').count()>0
            assert not errors,errors
            browser.close();print('PASS: archive hides from main/missing, counters, search, reload, backup, restore, no JS errors')
    finally:proc.terminate();proc.wait()
