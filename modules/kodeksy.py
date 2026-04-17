import os,re,json,time,logging,requests,html2text
from pathlib import Path
from datetime import datetime
from bs4 import BeautifulSoup

BASE_DIR=Path.home()/'consultant-data'
RAW_DIR=BASE_DIR/'kodeksy'/'raw-html'
MD_DIR=BASE_DIR/'kodeksy'/'converted-md'
LOG_PATH=BASE_DIR/'kodeksy.log'
SESSION_PATH=BASE_DIR/'session.json'
BASE_DIR.mkdir(parents=True,exist_ok=True)

logging.basicConfig(level=logging.INFO,format='%(asctime)s [%(levelname)s] %(message)s',handlers=[logging.FileHandler(str(LOG_PATH),encoding='utf-8'),logging.StreamHandler()])
log=logging.getLogger(__name__)

HEADERS={'User-Agent':'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36','Accept-Language':'ru-RU,ru;q=0.9'}

KODEKSY=[
    ('Гражданский кодекс РФ часть 1','https://www.consultant.ru/document/cons_doc_LAW_5142/'),
    ('Гражданский кодекс РФ часть 2','https://www.consultant.ru/document/cons_doc_LAW_9027/'),
    ('Гражданский кодекс РФ часть 3','https://www.consultant.ru/document/cons_doc_LAW_34154/'),
    ('Гражданский кодекс РФ часть 4','https://www.consultant.ru/document/cons_doc_LAW_64629/'),
    ('Налоговый кодекс РФ часть 1','https://www.consultant.ru/document/cons_doc_LAW_19671/'),
    ('Налоговый кодекс РФ часть 2','https://www.consultant.ru/document/cons_doc_LAW_28165/'),
    ('Трудовой кодекс РФ','https://www.consultant.ru/document/cons_doc_LAW_34683/'),
    ('Уголовный кодекс РФ','https://www.consultant.ru/document/cons_doc_LAW_10699/'),
    ('КоАП РФ','https://www.consultant.ru/document/cons_doc_LAW_34661/'),
    ('Арбитражный процессуальный кодекс РФ','https://www.consultant.ru/document/cons_doc_LAW_37800/'),
    ('Гражданский процессуальный кодекс РФ','https://www.consultant.ru/document/cons_doc_LAW_39570/'),
    ('Уголовно-процессуальный кодекс РФ','https://www.consultant.ru/document/cons_doc_LAW_34481/'),
    ('Земельный кодекс РФ','https://www.consultant.ru/document/cons_doc_LAW_33773/'),
    ('Жилищный кодекс РФ','https://www.consultant.ru/document/cons_doc_LAW_51057/'),
    ('Семейный кодекс РФ','https://www.consultant.ru/document/cons_doc_LAW_8982/'),
    ('Бюджетный кодекс РФ','https://www.consultant.ru/document/cons_doc_LAW_19702/'),
    ('Лесной кодекс РФ','https://www.consultant.ru/document/cons_doc_LAW_64299/'),
    ('Водный кодекс РФ','https://www.consultant.ru/document/cons_doc_LAW_60683/'),
    ('Градостроительный кодекс РФ','https://www.consultant.ru/document/cons_doc_LAW_51040/'),
    ('Уголовно-исполнительный кодекс РФ','https://www.consultant.ru/document/cons_doc_LAW_12940/'),
    ('Воздушный кодекс РФ','https://www.consultant.ru/document/cons_doc_LAW_19912/'),
    ('Кодекс торгового мореплавания РФ','https://www.consultant.ru/document/cons_doc_LAW_26677/'),
    ('Таможенный кодекс ЕАЭС','https://www.consultant.ru/document/cons_doc_LAW_215315/'),
]

def load_session():
    if not SESSION_PATH.exists(): return {}
    return {c['name']:c['value'] for c in json.loads(SESSION_PATH.read_text(encoding='utf-8'))}

def safe_fn(t):
    t=re.sub(r'[^\w\s-]','',t.lower())
    return re.sub(r'[\s]+','_',t.strip())[:80]

def fetch(url):
    cookies=load_session()
    for a in range(3):
        try:
            r=requests.get(url,headers=HEADERS,cookies=cookies,timeout=30)
            r.raise_for_status(); return r
        except requests.RequestException as e:
            log.warning(f'  Попытка {a+1}/3: {e}')
            if a<2: time.sleep(5)
    return None

def extract_text(html):
    soup=BeautifulSoup(html,'html.parser')
    # Основной контент
    ct=soup.find('div',class_=lambda c: c and 'document-page' in c and 'content' in c)
    if not ct: ct=soup.find('body') or soup
    # Убираем мусор
    for tag in ct.find_all(['nav','script','style','noscript']): tag.decompose()
    for tag in ct.find_all(class_=lambda c: c and 'breadcrumb' in ' '.join(c if c else [])): tag.decompose()
    # HTML -> Markdown
    h2=html2text.HTML2Text(); h2.ignore_links=False; h2.ignore_images=True; h2.body_width=0
    md=h2.handle(str(ct))
    # Убираем навигационный футер в конце (список кодексов)
    footer_patterns=[
        r'\n\s*\*\s*\[Гражданский кодекс \(ГК РФ\)\]',
        r'\n\s*\[Гражданский кодекс \(ГК РФ\)\]',
        r'\[Производственный календарь на 20\d{2}',
        r'\[Минимальный размер оплаты труда',
    ]
    for pat in footer_patterns:
        matches=list(re.finditer(pat,md))
        if matches:
            pos=matches[-1].start()
            if pos>len(md)*0.8:
                md=md[:pos].strip(); break
    # Убираем хлебные крошки в начале
    md=re.sub(r'^(\s*\*\s*\[.*?\]\(.*?\)\s*)+','',md).strip()
    md=re.sub(r'\[Вход в систему\]\([^)]+\)\s*','',md)
    return md.strip()

def download_doc(name, url, raw_dir, md_dir, category):
    log.info(f'Скачиваем: {name}')
    resp=fetch(url)
    if not resp: log.error(f'  Ошибка!'); return False
    html=resp.text
    md=extract_text(html)
    dd=datetime.now().isoformat()
    slug=safe_fn(name)
    raw_dir.mkdir(parents=True,exist_ok=True)
    md_dir.mkdir(parents=True,exist_ok=True)
    (raw_dir/f'{slug}.html').write_text(html,encoding='utf-8')
    full_md=f'---\ntitle: {name}\nsource_url: {url}\ndate_downloaded: {dd}\ncategory: {category}\n---\n\n# {name}\n\n'+md
    (md_dir/f'{slug}.md').write_text(full_md,encoding='utf-8')
    log.info(f'  OK: {slug}.md ({len(md.splitlines())} строк, {len(md):,} символов)')
    return True

def main():
    log.info('='*60); log.info('Загрузка Кодексов РФ'); log.info('='*60)
    ok=0; fail=0
    for name,url in KODEKSY:
        if download_doc(name,url,RAW_DIR,MD_DIR,'kodeks'): ok+=1
        else: fail+=1
        time.sleep(2)
    log.info(f'Готово: {ok} скачано, {fail} ошибок')

if __name__=='__main__': main()
