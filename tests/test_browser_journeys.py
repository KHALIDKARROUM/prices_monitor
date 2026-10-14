"""Real Chromium → Waitress → SQLite journeys, with external traffic blocked."""
from datetime import date, timedelta
import csv
import io
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

pytestmark = [pytest.mark.browser, pytest.mark.skipif(os.getenv('RUN_BROWSER_TESTS') != '1', reason='Set RUN_BROWSER_TESTS=1 to run real Chromium journeys')]
ROOT = Path(__file__).resolve().parents[1]
PASSWORD = 'browser-test-password-2026'

@pytest.fixture
def browser_app(tmp_path):
    ready = tmp_path / 'port.txt'
    env = {**os.environ, 'PRICE_MONITOR_DB':str(tmp_path/'browser.db'), 'PRICE_MONITOR_TEST_READY':str(ready),
        'PRICE_MONITOR_DB_BACKEND':'sqlite','PRICE_MONITOR_DATA':str(tmp_path/'absent.csv'),
        'PRICE_MONITOR_BITUMEN_DATA':str(tmp_path/'absent-bitumen.csv'),
        'PRICE_MONITOR_ENV':'development','PRICE_MONITOR_HOST':'127.0.0.1','PRICE_MONITOR_TEAM_MODE':'true',
        'PRICE_MONITOR_SECURE_COOKIES':'false','PRICE_MONITOR_PUBLIC_ORIGIN':'','PRICE_MONITOR_API_KEY':'',
        'PRICE_MONITOR_URL_SCHEME':'http','PRICE_MONITOR_TRUSTED_PROXY':'','PRICE_MONITOR_RATE_LIMIT_ENABLED':'false',
        'PRICE_MONITOR_DEMO':'false','PRICE_MONITOR_ALERT_EMAIL_TO':'','PRICE_MONITOR_ALERT_WEBHOOK_URL':'',
        'PRICE_MONITOR_DIGEST_EMAIL_TO':'','PRICE_MONITOR_ALERT_THRESHOLDS':'{}',
        'FRED_API_KEY':'','EIA_API_KEY':'','ALPHA_VANTAGE_API_KEY':''}
    with (tmp_path/'server.log').open('w',encoding='utf-8') as log:
        process = subprocess.Popen([sys.executable,str(ROOT/'tests/browser_server.py')],cwd=ROOT,env=env,stdout=log,stderr=log)
        try:
            deadline = time.monotonic()+20
            while not ready.exists() and process.poll() is None and time.monotonic()<deadline:
                time.sleep(.05)
            assert ready.exists(), (tmp_path/'server.log').read_text(encoding='utf-8')
            yield f'http://127.0.0.1:{ready.read_text()}'
        finally:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)

@pytest.fixture
def journey(browser_app, request):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as runner:
        browser_executable = os.getenv('PRICE_MONITOR_TEST_BROWSER_EXECUTABLE')
        browser = runner.chromium.launch(**({'executable_path':browser_executable} if browser_executable else {'channel':'chromium'}))
        context = browser.new_context(accept_downloads=True, reduced_motion='reduce')
        # Network isolation proves the UI/charts work without CDN access.
        external = []
        def route(request_route):
            if request_route.request.url.startswith(browser_app+'/'):
                request_route.continue_()
            else:
                external.append(request_route.request.url)
                request_route.abort()
        context.route('**/*',route)
        context.tracing.start(screenshots=True,snapshots=True,sources=True)
        page = context.new_page()
        errors = []
        page.on('pageerror',lambda error:errors.append(str(error)))
        artifacts = ROOT/'test-results/browser'
        artifacts.mkdir(parents=True,exist_ok=True)
        try:
            yield page,browser_app,external
            assert not errors, errors
            assert not external, external
        finally:
            page.screenshot(path=str(artifacts/f'{request.node.name}.png'),full_page=False)
            context.tracing.stop(path=str(artifacts/f'{request.node.name}.zip'))
            context.close(); browser.close()

def login(page, base, name='owner'):
    from playwright.sync_api import expect
    if not page.locator('#sign-in').is_visible():
        page.goto(base)
    page.locator('#sign-in').wait_for(state='visible')
    form = page.locator('#sign-in-form')
    form.locator('[name=username]').fill(name)
    form.locator('[name=password]').fill(PASSWORD)
    form.get_by_role('button',name='Sign in',exact=True).click()
    expect(page.locator('.app-shell')).to_be_visible()
    expect(page.locator('#account-name')).to_contain_text(name)
    page.wait_for_function("document.querySelector('#metric-grid .metric-card') !== null")

def test_reader_sign_in_private_archives_export_and_logout(journey):
    from playwright.sync_api import expect
    page,base,_ = journey
    assert page.request.get(base+'/api/history').status == 401
    assert page.request.get(base+'/export.csv').status == 401
    login(page,base,'reader')
    expect(page.locator('#collect-btn')).to_be_disabled()
    expect(page.locator('#user-form')).to_be_hidden()
    expect(page.locator('#quote-new')).to_be_hidden()
    page.locator('#market-brent-instrument').select_option('brent_spot')
    expect(page.locator('#metric-grid')).to_contain_text('80')
    page.locator('#market-brent-instrument').select_option('brent_futures')
    expect(page.locator('#metric-grid')).to_contain_text('90')
    # The first futures reading has no spot-derived +12.5% change.
    history = page.request.get(base+'/api/history?instrument=brent_futures&page_size=100').json()['items']
    assert all(row['variation_pct'] in (None,0) for row in history)
    page.locator('nav').get_by_role('link',name='History',exact=True).click()
    filters = page.locator('#history-filters')
    filters.locator('[name=product]').select_option('brent')
    filters.locator('[name=instrument]').select_option('brent_spot')
    filters.get_by_role('button',name='Filter',exact=True).click()
    expect(page.locator('#history-count')).to_contain_text('61')
    with page.expect_download() as downloaded:
        page.locator('#history-csv').click()
    rows = list(csv.DictReader(io.StringIO(Path(downloaded.value.path()).read_text(encoding='utf-8-sig'))))
    assert len(rows)==61 and {row['instrument'] for row in rows}=={'brent_spot'}
    assert page.request.post(base+'/api/collect',data={}).status==403
    page.locator('nav').get_by_role('link',name='My account / Team',exact=True).click()
    page.locator('#logout-button').click()
    expect(page.locator('#sign-in')).to_be_visible()
    expect(page.locator('.app-shell')).to_be_hidden()
    assert page.request.get(base+'/api/history').status==401

def test_offline_bundle_independent_axes_summaries_and_mobile_navigation(journey):
    from playwright.sync_api import expect
    page,base,_ = journey
    login(page,base)
    expect(page.locator('#needs-attention')).to_contain_text('active alert')
    expect(page.locator('#needs-attention')).to_contain_text('use forecast with caution')
    page.wait_for_function("window.Chart && Chart.getChart('price-chart')")
    page.get_by_role('button',name='Price',exact=True).click()
    page.wait_for_function("Chart.getChart('price-chart').scales.y_brent !== undefined")
    axes = page.evaluate("Object.entries(Chart.getChart('price-chart').scales).filter(([k])=>k.startsWith('y')).map(([k,v])=>[k,v.options.title.text])")
    assert {row[0] for row in axes}=={'y_brent','y_gasoil'}
    assert any('USD/barrel' in row[1] for row in axes) and any('USD/gallon' in row[1] for row in axes)
    expect(page.locator('#price-chart-summary')).to_contain_text('Latest value')
    expect(page.locator('#forecast-brent-summary')).to_contain_text('95% interval unavailable')
    assert page.locator('#price-chart').get_attribute('aria-describedby')=='price-chart-summary chart-note'
    page.locator('.chart-panel').scroll_into_view_if_needed()
    page.screenshot(path=str(ROOT/'test-results/browser/independent-chart-axes.png'))
    # A reload still paints the local bundle with every external request blocked.
    page.reload()
    page.wait_for_function("window.Chart && Chart.getChart('price-chart')")
    page.set_viewport_size({'width':390,'height':844})
    page.locator('#nav-toggle').click()
    page.locator('nav').get_by_role('link',name='History',exact=True).click()
    expect(page.locator('#nav-toggle')).to_have_attribute('aria-expanded','false')
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.locator('#nav-toggle').click()
    page.locator('nav').get_by_role('link',name='Markets',exact=True).click()

def test_theme_choice_persists_and_recolors_charts(journey):
    from playwright.sync_api import expect
    page,base,_ = journey
    page.goto(base)
    expect(page.locator('#sign-in')).to_be_visible()
    original = page.locator('html').get_attribute('data-theme')
    assert original in {'dark','light'}
    chosen = 'light' if original == 'dark' else 'dark'
    page.locator('#sign-in [data-theme-toggle]').click()
    expect(page.locator('html')).to_have_attribute('data-theme', chosen)
    assert page.evaluate("localStorage.getItem('priceMonitorTheme')") == chosen

    login(page,base)
    expect(page.locator('.app-shell [data-theme-toggle]')).to_have_attribute('aria-label', f'Switch to {original} mode')
    page.wait_for_function("window.Chart && Chart.getChart('price-chart')")
    assert page.evaluate("Chart.getChart('price-chart').options.scales.x.ticks.color") == page.evaluate('PriceMonitorTheme.palette().tick')

    page.locator('.app-shell [data-theme-toggle]').click()
    expect(page.locator('html')).to_have_attribute('data-theme', original)
    page.wait_for_function("Chart.getChart('price-chart').options.scales.x.ticks.color === PriceMonitorTheme.palette().tick")
    page.reload()
    expect(page.locator('html')).to_have_attribute('data-theme', original)
    assert page.evaluate("localStorage.getItem('priceMonitorTheme')") == original
    page.set_viewport_size({'width':390,'height':844})
    expect(page.locator('.app-shell [data-theme-toggle]')).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')


def fill_quote(page, supplier):
    form=page.locator('#quote-editor')
    form.locator('[name=product]').select_option('bitume')
    form.locator('[name=packaging]').select_option('bulk')
    form.locator('[name=incoterm]').select_option('DAP')
    fields={'supplier':supplier,'grade':'50/70','specification':'EN 12591','origin':'Maroc',
        'incoterm_place':'Casablanca','destination':'Casablanca','payment_terms':'30 jours',
        'valid_until':(date.today()+timedelta(days=30)).isoformat(),'delivery_date':(date.today()+timedelta(days=7)).isoformat(),
        'quantity':'10','unit_price':'500','insurance_cost':'25','inspection_cost':'50','transport_cost':'150',
        'duties_cost':'100','taxes_cost':'20','storage_cost':'30','heating_cost':'40','finance_cost':'5'}
    for field,value in fields.items(): form.locator(f'[name={field}]').fill(value)
    form.locator('[name=exchange_rate]').fill('1')

def test_editor_quote_correction_document_budget_alerts_and_admin_audit(journey,tmp_path):
    from playwright.sync_api import expect
    page,base,_=journey
    login(page,base,'editor')
    page.locator('nav').get_by_role('link',name='Suppliers',exact=True).click()
    page.locator('#quote-new').click()
    fill_quote(page,'Browser supplier')
    page.locator('#quote-save').click()
    expect(page.locator('#quote-detail-title')).to_contain_text('Browser supplier')
    expect(page.locator('#quote-detail-content')).to_contain_text('5')
    page.locator('#quote-edit').click()
    page.locator('#quote-editor [name=grade]').fill('70/100')
    page.locator('#quote-editor [name=change_reason]').fill('Corrected supplier grade')
    page.locator('#quote-save').click()
    expect(page.locator('#quote-revisions')).to_contain_text('Corrected supplier grade')
    expect(page.locator('#quote-revisions')).to_contain_text('editor')
    document=tmp_path/'quote.txt'; document.write_text('Test evidence for the quote')
    page.locator('#quote-document-form input[type=file]').set_input_files(str(document))
    page.locator('#quote-document-form [name=change_reason]').fill('Attach test evidence')
    page.locator('#quote-upload').click()
    expect(page.locator('#quote-documents')).to_contain_text('quote.txt')
    with page.expect_download() as download:
        page.locator('#quote-documents').get_by_role('button',name='Download',exact=True).click()
    assert Path(download.value.path()).read_text()=='Test evidence for the quote'
    page.locator('#quote-budget').click()
    page.locator('#procurement-form button[type=submit]').click()
    expect(page.locator('#procurement-history-state')).to_be_hidden()
    expect(page.locator('#procurement-history-table')).to_contain_text('Browser supplier')
    budgets=page.request.get(base+'/api/procurements').json()['purchases']
    assert budgets[0]['total_cost_usd']==5420
    page.locator('nav').get_by_role('link',name='Alerts',exact=True).click()
    page.locator('#alert-new').click()
    page.locator('#alert-form [name=product]').select_option('bitume')
    page.locator('#alert-form [name=threshold]').fill('550')
    page.locator('#alert-save').click()
    expect(page.locator('#alert-list')).to_contain_text('550')
    expect(page.locator('#user-form')).to_be_hidden()
    page.locator('nav').get_by_role('link',name='My account / Team',exact=True).click()
    page.locator('#logout-button').click()
    expect(page.locator('#sign-in')).to_be_visible()
    login(page,base,'owner')
    page.locator('nav').get_by_role('link',name='My account / Team',exact=True).click()
    expect(page.locator('#audit-body')).to_contain_text('editor')
    events=page.request.get(base+'/api/audit?page_size=100').json()['events']
    assert {'quote.created','quote.corrected','document.attached','document.prepared','budget.created','alert.created'} <= {event['action'] for event in events}
    assert all(event['actor']=='editor' for event in events if event['action'] in {'quote.created','quote.corrected','document.attached','budget.created'})
