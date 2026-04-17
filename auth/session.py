import json, os, time
from pathlib import Path
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

ENV_PATH = Path.home()/'.config'/'consultant'/'.env'
SESSION_PATH = Path.home()/'consultant-data'/'session.json'
OUTPUT_DIR = Path.home()/'consultant-recon'/'output'
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(ENV_PATH)
USERNAME = os.getenv('CONSULTANT_USERNAME','')
PASSWORD = os.getenv('CONSULTANT_PASSWORD','')
print(f'Логин: {USERNAME[:3]}***')

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, args=['--no-sandbox'])
    ctx = browser.new_context(
        viewport={'width':1280,'height':900},
        user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        locale='ru-RU'
    )
    page = ctx.new_page()

    # Шаг 1: Авторизация
    print('Шаг 1: Авторизация на login.consultant.ru...')
    page.goto('https://login.consultant.ru/', wait_until='networkidle', timeout=30000)
    page.locator('input[name="LoginForm[login]"]').fill(USERNAME)
    time.sleep(0.3)
    page.locator('input[name="LoginForm[password]"]').fill(PASSWORD)
    time.sleep(0.3)
    page.locator('#buttonLogin').click()
    try: page.wait_for_load_state('networkidle', timeout=20000)
    except: pass
    time.sleep(2)
    print(f'  URL после входа: {page.url}')
    ok = 'cloud.consultant.ru' in page.url
    print(f'  Авторизация: {"OK" if ok else "ОШИБКА"}')

    # Шаг 2: Заходим на www.consultant.ru для активации сессии
    print('Шаг 2: Активация сессии на www.consultant.ru...')
    page.goto('https://www.consultant.ru/', wait_until='networkidle', timeout=30000)
    time.sleep(2)
    content = page.content().lower()
    www_ok = any(s in content for s in ['выйти','logout','кабинет','has_ov_account'])
    print(f'  URL: {page.url}')
    print(f'  Авторизован на www: {"OK" if www_ok else "нет сессии"}')

    # Шаг 3: Пробуем платный документ
    print('Шаг 3: Проверяем доступ к платному документу (Воздушный кодекс)...')
    page.goto('https://www.consultant.ru/document/cons_doc_LAW_19912/', wait_until='networkidle', timeout=30000)
    text = page.inner_text('body')
    if 'доступен по расписанию' in text:
        print('  ПЛАТНЫЙ ДОСТУП НЕ РАБОТАЕТ — всё ещё некоммерческая версия')
    elif len(text) > 1000:
        print(f'  ПЛАТНЫЙ ДОСТУП РАБОТАЕТ! Текст: {len(text)} символов')
        print(f'  Первые 200: {text[:200]}')
    else:
        print(f'  Непонятно. Текст ({len(text)} символов): {text[:200]}')

    # Шаг 4: Сохраняем ВСЕ cookies
    cookies = ctx.cookies()
    SESSION_PATH.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\nCookies сохранены: {len(cookies)} шт.')
    domains = set(c.get('domain','') for c in cookies)
    for d in sorted(domains):
        cnt = sum(1 for c in cookies if c.get('domain','') == d)
        print(f'  {d}: {cnt} cookies')

    ctx.close(); browser.close()
