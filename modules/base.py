"""Базовый класс для всех модулей загрузки документов."""
import re, json, time, logging, requests, html2text
from pathlib import Path
from datetime import datetime
from bs4 import BeautifulSoup
from config import SESSION_PATH, HEADERS

log = logging.getLogger(__name__)

FOOTER_PATTERNS = [
    r'\n\s*\*\s*\[Гражданский кодекс \(ГК РФ\)\]',
    r'\[Производственный календарь на 20\d{2}',
    r'\[Минимальный размер оплаты труда',
]

def load_session() -> dict:
    if not SESSION_PATH.exists():
        return {}
    return {c['name']: c['value'] for c in json.loads(SESSION_PATH.read_text(encoding='utf-8'))}

def safe_fn(t: str) -> str:
    t = re.sub(r'[^\w\s-]', '', t.lower())
    return re.sub(r'[\s]+', '_', t.strip())[:80]

def fetch(url: str, retries: int = 3, delay: int = 5):
    cookies = load_session()
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, cookies=cookies, timeout=30)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            log.warning(f"  Попытка {attempt+1}/{retries}: {e}")
            if attempt < retries - 1:
                time.sleep(delay)
    return None

def extract_markdown(html: str) -> str:
    soup = BeautifulSoup(html, 'html.parser')
    ct = soup.find('div', class_=lambda c: c and 'document-page' in c and 'content' in c)
    if not ct:
        ct = soup.find('body') or soup
    for tag in ct.find_all(['nav','script','style','noscript']): tag.decompose()
    for tag in ct.find_all(class_=lambda c: c and 'breadcrumb' in ' '.join(c if c else [])): tag.decompose()
    h = html2text.HTML2Text()
    h.ignore_links = False; h.ignore_images = True; h.body_width = 0
    md = h.handle(str(ct))
    for pat in FOOTER_PATTERNS:
        matches = list(re.finditer(pat, md))
        if matches:
            pos = matches[-1].start()
            if pos > len(md) * 0.8:
                md = md[:pos].strip(); break
    md = re.sub(r'\n(\s*\*\s*\[(?:Главная|Документы)\]\([^)]+\)\s*\n)+', '\n', md)
    return md.strip()

def save_document(name: str, url: str, md: str, raw_dir: Path, md_dir: Path, category: str) -> dict:
    slug = safe_fn(name)
    dd = datetime.now().isoformat()
    raw_dir.mkdir(parents=True, exist_ok=True)
    md_dir.mkdir(parents=True, exist_ok=True)
    full_md = f"---\ntitle: {name}\nsource_url: {url}\ndate_downloaded: {dd}\ncategory: {category}\n---\n\n# {name}\n\n{md}"
    (md_dir / f"{slug}.md").write_text(full_md, encoding='utf-8')
    return {"name": name, "status": "ok", "lines": len(md.splitlines()), "chars": len(md), "slug": slug}
