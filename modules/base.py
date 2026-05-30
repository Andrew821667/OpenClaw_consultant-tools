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

# Порог: если markdown главной страницы документа короче этого — считаем что
# это оглавление (TOC), а не полный текст, и собираем статьи отдельно.
SHORT_DOC_THRESHOLD = 8000

# Любая строка-пункт списка, целиком являющаяся ссылкой на документ
# consultant.ru: оглавление статей, футер с кодексами, хлебные крошки.
# Покрывает форматы: (/document/...), (//www.consultant.ru/...), (http://...).
_NAV_LINK_RE = re.compile(
    r'^\s*\*\s*\[[^\]]*\]\('
    r'(?:'
    r'(?:https?:)?//[^)]*consultant\.ru/[^)]*'   # абсолютные ссылки на КП
    r'|/?(?:document|law)/[^)]*'                  # относительные /document/, /law/
    r')'
    r'\)\s*$',
    re.IGNORECASE,
)
# Прочие навигационные строки
_NAV_MISC_RE = re.compile(
    r'^\s*\*\s*\[(?:Главная|Документы)\]', re.IGNORECASE
)


def strip_nav(md: str) -> str:
    """Убрать навигационные link-списки (оглавление статей, футер с кодексами,
    хлебные крошки), которые www.consultant.ru дублирует на каждой странице."""
    out = []
    for line in md.splitlines():
        if _NAV_LINK_RE.match(line) or _NAV_MISC_RE.match(line):
            continue
        if line.strip() in ('Открыть полный текст документа', 'КонсультантПлюс'):
            continue
        out.append(line)
    cleaned = re.sub(r'\n{3,}', '\n\n', '\n'.join(out))
    return cleaned.strip()


def fetch_full_text(public_url: str, toc_html: str) -> str:
    """Вернуть полный markdown документа с www.consultant.ru.

    На www.consultant.ru документ бывает в двух формах:
      1. Одна страница с полным текстом (кодексы, 100K+ символов).
      2. Страница-оглавление (TOC) со ссылками на статьи (многие ФЗ/ФКЗ).

    Если страница длинная (≥ SHORT_DOC_THRESHOLD*5) — это уже полный текст.
    Иначе ищем article-ссылки `/document/cons_doc_LAW_<BASE_ID>/<HASH>/`,
    берём BASE_ID с наибольшим числом ссылок (он может отличаться от ID
    редакции в URL), скачиваем все статьи и конкатенируем.

    Требует, чтобы caller передал HTML уже скачанной главной страницы.
    """
    toc_md = extract_markdown(toc_html)
    if len(toc_md) >= SHORT_DOC_THRESHOLD * 5:
        return toc_md

    soup = BeautifulSoup(toc_html, 'html.parser')
    article_re = re.compile(r'^/document/cons_doc_LAW_(\d+)/[a-f0-9]{32,}/?$')
    base_ids: dict = {}
    for a in soup.find_all('a', href=True):
        m = article_re.match(a['href'])
        if not m:
            continue
        base_ids.setdefault(m.group(1), []).append(a['href'])

    if not base_ids:
        return toc_md

    base_id = max(base_ids, key=lambda k: len(base_ids[k]))
    seen = set()
    article_paths = []
    for path in base_ids[base_id]:
        if path not in seen:
            seen.add(path)
            article_paths.append(path)
    if len(article_paths) < 2:
        return toc_md

    log.info(f"    TOC ({len(toc_md)} симв), base_law=LAW_{base_id}, "
             f"{len(article_paths)} статей → concat")
    # Заголовок документа из TOC (первые строки до списка ссылок) сохраняем,
    # но сам TOC-список не дублируем — статьи идут подряд, очищенные от навигации.
    parts = ['# Полный текст\n\n']
    for i, path in enumerate(article_paths, 1):
        r = fetch(f'https://www.consultant.ru{path}')
        if not r:
            continue
        article_md = strip_nav(extract_markdown(r.text))
        if article_md:
            parts.append(article_md)
            parts.append('\n\n')
        if i % 20 == 0:
            log.info(f"    …скачано {i}/{len(article_paths)} статей")
        time.sleep(0.3)
    return ''.join(parts)


def save_document(name: str, url: str, md: str, raw_dir: Path, md_dir: Path, category: str) -> dict:
    slug = safe_fn(name)
    dd = datetime.now().isoformat()
    raw_dir.mkdir(parents=True, exist_ok=True)
    md_dir.mkdir(parents=True, exist_ok=True)
    full_md = f"---\ntitle: {name}\nsource_url: {url}\ndate_downloaded: {dd}\ncategory: {category}\n---\n\n# {name}\n\n{md}"
    (md_dir / f"{slug}.md").write_text(full_md, encoding='utf-8')
    return {"name": name, "status": "ok", "lines": len(md.splitlines()), "chars": len(md), "slug": slug}
