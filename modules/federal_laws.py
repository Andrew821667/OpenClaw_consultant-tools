"""modules/federal_laws.py — выкачка Федеральных законов из КонсультантПлюс.

Список ФЗ не захардкожен: модуль ходит в К+ под пользовательской учёткой и
автоматически берёт его из карточки поиска (раздел «Законодательство», фильтр
«Вид документа = Федеральный закон»). По состоянию на 05.2026 в этом срезе
≈12 200 документов (ФЗ + ФКЗ, со всеми редакциями).

Маршрут навигации (разведан 2026-05-29):
  login.consultant.ru → cloud.consultant.ru/cloud/cgi/online.cgi
  ?req=card&cardDiv=LAW&rnd=<RND>
   → клик «Законодательство NNN» (sidebar)
   → клик «Уточнить по реквизитам» (правая панель)
   → клик «Вид документа»
   → чек «Федеральный закон»
   → «Применить»
   → отфильтрованный список ?req=query&cacheid=...

Использование:
  python3 modules/federal_laws.py --smoke 10       # smoke-тест на N документов
  python3 modules/federal_laws.py --check          # что уже скачано
  python3 modules/federal_laws.py --all            # все 12K (опасно, ~7 часов)
  python3 modules/federal_laws.py --force          # перекачать существующие
  python3 modules/federal_laws.py --notify         # отчёт в Telegram
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import html2text
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from playwright.sync_api import Page, sync_playwright

sys.path.insert(0, str(Path(__file__).parent.parent))
from auth.session import kill_restriction, click_exit_in_restriction  # noqa: E402
from config import BASE_DIR  # noqa: E402
from modules.base import fetch as base_fetch, extract_markdown, save_document  # noqa: E402

ENV_PATH = Path.home() / '.config' / 'consultant' / '.env'
SESSION_PATH = BASE_DIR / 'session.json'
FZ_BASE = BASE_DIR / 'federal-laws'
LOG_PATH = BASE_DIR / 'federal_laws.log'

# ФЗ (обычные) и ФКЗ (конституционные) лежат в разных подпапках,
# но имеют общий manifest и lifecycle (один прогон discovers обоих)
PATHS = {
    'fz': {
        'raw': FZ_BASE / 'fz' / 'raw-html',
        'md':  FZ_BASE / 'fz' / 'converted-md',
        'kind_match': 'Федеральный закон',
    },
    'fkz': {
        'raw': FZ_BASE / 'fkz' / 'raw-html',
        'md':  FZ_BASE / 'fkz' / 'converted-md',
        'kind_match': 'Федеральный конституционный закон',
    },
}
MANIFEST_PATH = FZ_BASE / 'manifest.json'

for sub in PATHS.values():
    sub['raw'].mkdir(parents=True, exist_ok=True)
    sub['md'].mkdir(parents=True, exist_ok=True)


def _classify(meta: dict) -> str:
    """Вернуть 'fkz' / 'fz' / 'unknown' на основе meta['kind']."""
    k = (meta or {}).get('kind') or ''
    if 'конституционн' in k.lower():
        return 'fkz'
    if 'федеральный закон' in k.lower():
        return 'fz'
    return 'unknown'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(str(LOG_PATH), encoding='utf-8'),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

load_dotenv(ENV_PATH)
USERNAME = os.getenv('CONSULTANT_USERNAME', '')
PASSWORD = os.getenv('CONSULTANT_PASSWORD', '')

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36')


# ──────────────────────────────────────────────────────────────────────
# Утилиты
# ──────────────────────────────────────────────────────────────────────

def safe_fn(text: str) -> str:
    t = re.sub(r'[^\w\s-]', '', text.lower())
    return re.sub(r'\s+', '_', t.strip())[:90]


def parse_law_meta(title: str) -> dict:
    """Распарсить заголовок ФЗ на компоненты.

    Пример: 'Федеральный закон от 26.07.2017 N 187-ФЗ (ред. от 04.08.2023) "О ..."'
    """
    meta = {'kind': None, 'date': None, 'number': None, 'edition_date': None, 'title': None}
    m = re.match(
        r'^(Федеральный\s+(?:конституционный\s+)?закон)\s+от\s+(\d{2}\.\d{2}\.\d{4})\s+N\s+([\w\-/]+)',
        title, re.IGNORECASE,
    )
    if m:
        meta['kind'] = m.group(1).strip()
        meta['date'] = m.group(2)
        meta['number'] = m.group(3)
    ed = re.search(r'\(ред\.\s+от\s+(\d{2}\.\d{2}\.\d{4})', title)
    if ed:
        meta['edition_date'] = ed.group(1)
    name_m = re.search(r'"([^"]+)"', title)
    if name_m:
        meta['title'] = name_m.group(1).strip()
    return meta


def load_manifest() -> List[dict]:
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))
    return []


def save_manifest(items: List[dict]) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding='utf-8',
    )


# ──────────────────────────────────────────────────────────────────────
# Браузерная сессия К+
# ──────────────────────────────────────────────────────────────────────

class ConsultantSession:
    """Playwright-обёртка: login + переиспользование cookies + навигация."""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._pw = None
        self.browser = None
        self.ctx = None
        self.page: Optional[Page] = None
        self.rnd: Optional[str] = None

    def __enter__(self):
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(headless=self.headless, args=['--no-sandbox'])
        storage = None
        if SESSION_PATH.exists():
            try:
                cookies = json.loads(SESSION_PATH.read_text(encoding='utf-8'))
                storage = {'cookies': cookies, 'origins': []}
            except Exception:
                pass
        self.ctx = self.browser.new_context(
            viewport={'width': 1600, 'height': 1000},
            user_agent=UA,
            locale='ru-RU',
            storage_state=storage,
        )
        self.page = self.ctx.new_page()
        self._ensure_logged_in()
        return self

    def __exit__(self, *exc):
        try:
            if self.ctx:
                cookies = self.ctx.cookies()
                SESSION_PATH.write_text(
                    json.dumps(cookies, ensure_ascii=False, indent=2),
                    encoding='utf-8',
                )
        finally:
            try:
                self.ctx.close() if self.ctx else None
                self.browser.close() if self.browser else None
                self._pw.stop() if self._pw else None
            except Exception:
                pass

    def _ensure_logged_in(self):
        p = self.page
        for license_attempt in range(2):
            p.goto('https://login.consultant.ru/', wait_until='domcontentloaded', timeout=30000)
            time.sleep(3)
            login_form = p.locator('input[name="LoginForm[login]"]').count() > 0
            if 'Авторизация' in p.title() or login_form:
                log.info('Логин в К+')
                self._do_login()
            else:
                log.info('Сессия переиспользована')
            ok = kill_restriction(p)
            if ok:
                break
            # Модалка не закрылась — К+ держит занятой лицензию.
            # Кликаем «Выйти из системы», ждём, переавторизуемся с чистого листа.
            log.warning('Лицензия занята другой сессией — кликаем «Выйти из системы» и логинимся заново')
            click_exit_in_restriction(p)
            time.sleep(10)
            # Удаляем cookies — следующий логин будет полностью чистым
            try:
                self.ctx.clear_cookies()
            except Exception:
                pass
            time.sleep(5)
        else:
            raise RuntimeError('Не удалось получить доступ к К+ после 2 попыток')

        self.rnd = self._extract_rnd()
        log.info(f'rnd={self.rnd}')
        if not self.rnd:
            raise RuntimeError('Не удалось извлечь rnd из дашборда')

    def _do_login(self):
        if not USERNAME or not PASSWORD:
            raise SystemExit(f'CONSULTANT_USERNAME/PASSWORD не заданы в {ENV_PATH}')
        p = self.page
        p.goto('https://login.consultant.ru/', wait_until='networkidle', timeout=30000)
        p.locator('input[name="LoginForm[login]"]').fill(USERNAME)
        time.sleep(0.3)
        p.locator('input[name="LoginForm[password]"]').fill(PASSWORD)
        time.sleep(0.3)
        p.locator('#buttonLogin').click()
        try:
            p.wait_for_load_state('networkidle', timeout=20000)
        except Exception:
            pass
        time.sleep(4)

    def _extract_rnd(self) -> Optional[str]:
        for fr in self.page.frames:
            try:
                hrefs = fr.eval_on_selector_all('a', 'els => els.map(e => e.href)') or []
            except Exception:
                continue
            for h in hrefs:
                m = re.search(r'[?&]rnd=([^&]+)', h or '')
                if m:
                    return m.group(1)
        for fr in self.page.frames:
            m = re.search(r'[?&]rnd=([^&]+)', fr.url)
            if m:
                return m.group(1)
        return None


# ──────────────────────────────────────────────────────────────────────
# Discovery — список ФЗ из карточки поиска
# ──────────────────────────────────────────────────────────────────────

def discover_federal_laws(sess: ConsultantSession, limit: Optional[int],
                          only: Optional[str] = None) -> List[dict]:
    """Пройти весь маршрут до отфильтрованного списка ФЗ и вернуть [{title, url}, …].

    Args:
        limit: если задан — возвращает не больше N документов после фильтрации.
        only: 'fz' / 'fkz' / None. None = оба вида.

    Returns: список items, каждый с title, url, public_url, doc_id, meta, kind.
    """
    p = sess.page
    rnd = sess.rnd

    card_url = f'https://cloud.consultant.ru/cloud/cgi/online.cgi?req=card&cardDiv=LAW&rnd={rnd}'

    def goto_card_with_modal_handling(max_attempts: int = 3) -> None:
        """Открыть card-search, обрабатывая возможный modal.

        После клика «Попробовать ещё раз» К+ возвращает на Стартовую страницу,
        поэтому надо ПЕРЕнавигировать на card_url и проверять, что сайдбар
        Законодательство NNN присутствует."""
        for attempt in range(max_attempts):
            p.goto(card_url, wait_until='domcontentloaded', timeout=30000)
            try:
                p.wait_for_load_state('load', timeout=15000)
            except Exception:
                pass
            time.sleep(4)
            modal_ok = kill_restriction(p)
            time.sleep(3)
            # Проверка: сайдбар Карточки поиска присутствует?
            body = p.inner_text('body')
            if re.search(r'Законодательство\s*\d', body):
                log.info(f'  card-search загружен (попытка {attempt + 1})')
                return
            log.warning(f'  Сайдбар не найден после попытки {attempt + 1} — повторная навигация')
            time.sleep(3)
        raise RuntimeError('Не удалось открыть Карточку поиска после 3 попыток')

    log.info('Шаг 1/5: открываем Карточку поиска (раздел Законодательство)')
    goto_card_with_modal_handling()

    log.info('Шаг 2/5: клик «Законодательство NNN» в сайдбаре')
    p.locator('text=/Законодательство\\s*\\d/').first.click(timeout=20000)
    time.sleep(12); kill_restriction(p)
    try:
        p.wait_for_load_state('networkidle', timeout=20000)
    except Exception:
        pass
    time.sleep(5); kill_restriction(p)

    log.info('Шаг 3/5: клик «Уточнить по реквизитам»')
    p.locator('text=Уточнить по реквизитам').first.click(timeout=15000)
    time.sleep(5); kill_restriction(p); time.sleep(2)

    log.info('Шаг 4/5: клик «Вид документа» (открывает словарь)')
    p.locator('text="Вид документа"').first.click(timeout=15000)
    time.sleep(5); kill_restriction(p); time.sleep(2)

    log.info('Шаг 5/5: чек «Федеральный закон» + «Применить»')
    p.locator('text="Федеральный закон"').first.click(timeout=15000)
    time.sleep(2)
    p.locator('button:has-text("Применить")').first.click(timeout=15000)
    time.sleep(8); kill_restriction(p)
    try:
        p.wait_for_load_state('networkidle', timeout=20000)
    except Exception:
        pass
    time.sleep(5); kill_restriction(p)

    log.info(f'Фильтр применён, URL: {p.url[:160]}…')

    # Считываем общее количество и собираем ссылки
    body = p.inner_text('body')
    total_m = re.search(r'\[\d+:(\d+)\]', body)
    total = int(total_m.group(1)) if total_m else None
    log.info(f'Всего отфильтрованных ФЗ: {total}')

    items: List[dict] = []
    seen_urls = set()

    def harvest_current_page() -> int:
        """Собрать видимые на данный момент ссылки на документы (dedup по URL).

        Список ВИРТУАЛИЗИРОВАН: при скролле старые row удаляются из DOM,
        новые добавляются. Поэтому вызываем многократно во время скролла.
        """
        added = 0
        links = p.eval_on_selector_all(
            'a',
            "els => els.filter(e => e.offsetWidth || e.offsetHeight)"
            ".map(e => ({text:(e.innerText||'').trim(), href:e.href}))"
            ".filter(l => l.href.includes('online.cgi') && l.href.includes('req=doc'))",
        ) or []
        for ln in links:
            url = ln['href']
            title = ln['text']
            if not title or len(title) < 10:
                continue
            if url in seen_urls:
                continue
            seen_urls.add(url)
            m = re.search(r'[?&]n=(\d+)', url)
            doc_id = m.group(1) if m else None
            public_url = (
                f'https://www.consultant.ru/document/cons_doc_LAW_{doc_id}/'
                if doc_id else None
            )
            meta = parse_law_meta(title)
            items.append({
                'title': title, 'url': url, 'doc_id': doc_id,
                'public_url': public_url, 'meta': meta,
                'kind': _classify(meta),
            })
            added += 1
        return added

    def scroll_results():
        """Проскроллить виртуализированный список вниз на ~один экран."""
        try:
            p.evaluate(
                """() => {
                    const list = document.querySelector('.x-page-search-results__list');
                    if (!list) return;
                    list.scrollTop = list.scrollHeight;
                    const rows = list.querySelectorAll('.x-list__row');
                    if (rows.length) rows[rows.length - 1].scrollIntoView({block: 'end'});
                }"""
            )
        except Exception:
            pass

    def have_enough() -> bool:
        if not limit:
            return False
        if only:
            return sum(1 for it in items if it['kind'] == only) >= limit
        return len(items) >= limit

    # Scroll-harvest: чередуем сбор и скролл, пока не наберём нужное или
    # список не перестанет расти.
    max_scrolls = 8 if limit else 400  # full-run: до 400 скроллов (~12K/30)
    stale = 0
    harvest_current_page()
    for i in range(max_scrolls):
        if have_enough():
            break
        before = len(items)
        scroll_results()
        time.sleep(2.5)
        kill_restriction(p)
        harvest_current_page()
        gained = len(items) - before
        if gained == 0:
            stale += 1
            if stale >= 3:
                log.info(f'  Список перестал расти на скролле {i + 1} — стоп')
                break
        else:
            stale = 0
        if (i + 1) % 10 == 0:
            log.info(f'  …скролл {i + 1}, собрано {len(items)} (цель {limit or total})')

    fz_count = sum(1 for it in items if it['kind'] == 'fz')
    fkz_count = sum(1 for it in items if it['kind'] == 'fkz')
    log.info(f'Собрано всего: ФЗ={fz_count}, ФКЗ={fkz_count}, итого={len(items)}')

    if only:
        items = [it for it in items if it['kind'] == only]
        log.info(f'После фильтра only={only}: {len(items)}')

    if limit:
        items = items[:limit]
        log.info(f'Берём первые {len(items)} из {total or "?"}')

    return items


# ──────────────────────────────────────────────────────────────────────
# Загрузка одного документа
# ──────────────────────────────────────────────────────────────────────

def _extract_doc_markdown(html: str) -> str:
    """Очистить HTML страницы документа cloud.consultant.ru и сконвертировать в md."""
    soup = BeautifulSoup(html, 'html.parser')

    # Контейнер документа в X-фреймворке К+
    container = (
        soup.find('div', class_=lambda c: c and 'x-page-document' in ' '.join(c)) or
        soup.find('div', class_=lambda c: c and 'document-content' in ' '.join(c)) or
        soup.find('div', class_=lambda c: c and 'document-page' in ' '.join(c)) or
        soup.find('body') or soup
    )

    # Удаляем навигацию/служебные элементы
    for tag in container.find_all(['nav', 'script', 'style', 'noscript', 'header', 'footer']):
        tag.decompose()
    for tag in container.find_all(
        class_=lambda c: c and any(
            k in ' '.join(c) for k in (
                'header', 'sidebar', 'toolbar', 'menu', 'top-bar', 'tab',
                'breadcrumb', 'controls', 'actions', 'panel-controls',
            )
        )
    ):
        tag.decompose()

    h = html2text.HTML2Text()
    h.ignore_links = False
    h.ignore_images = True
    h.body_width = 0
    md = h.handle(str(container))

    # Финальная зачистка
    md = re.sub(r'\n{4,}', '\n\n\n', md)
    return md.strip()


SHORT_DOC_THRESHOLD = 8000  # ниже этого считаем что страница — только TOC


def _fetch_full_text(public_url: str, toc_html: str) -> str:
    """Стратегия для www.consultant.ru:

    1. Скачиваем главную страницу — это может быть либо TOC (3-15K симв),
       либо уже полный кодекс (100K+).
    2. Парсим ВСЕ ссылки на статьи вида /document/cons_doc_LAW_<ID>/<HASH>/
       по ЛЮБОМУ <ID> (не только текущему URL — TOC редакции часто ссылается
       на базовый ФКЗ с другим ID).
    3. Если статей ≥2 и сама страница короткая (< 15K) → concat.
    4. Иначе возвращаем TOC как есть.
    """
    from bs4 import BeautifulSoup as BS
    toc_md = extract_markdown(toc_html)

    # Если страница большая (как кодекс с 700+ статьями в одной странице) —
    # это уже полный текст
    if len(toc_md) >= SHORT_DOC_THRESHOLD * 5:
        return toc_md

    # Парсим ВСЕ article-style ссылки на этой странице
    soup = BS(toc_html, 'html.parser')
    article_re = re.compile(r'^/document/cons_doc_LAW_(\d+)/[a-f0-9]{32,}/?$')
    base_ids: dict = {}
    for a in soup.find_all('a', href=True):
        m = article_re.match(a['href'])
        if not m:
            continue
        bid = m.group(1)
        base_ids.setdefault(bid, []).append(a['href'])

    if not base_ids:
        return toc_md

    # Берём БАЗОВЫЙ ID = тот, на который больше всего ссылок
    base_id = max(base_ids, key=lambda k: len(base_ids[k]))
    # Уникальные пути этого base_id, сохраняя порядок
    seen = set(); article_paths = []
    for p in base_ids[base_id]:
        if p not in seen:
            seen.add(p); article_paths.append(p)
    if len(article_paths) < 2:
        return toc_md

    log.info(f'    TOC ({len(toc_md)} симв), base_law=LAW_{base_id}, {len(article_paths)} статей → concat')
    parts = [toc_md, '\n\n---\n\n# Полный текст\n\n']
    for i, path in enumerate(article_paths, 1):
        r = base_fetch(f'https://www.consultant.ru{path}')
        if not r:
            continue
        parts.append(extract_markdown(r.text))
        parts.append('\n\n---\n\n')
        if i % 20 == 0:
            log.info(f'    …скачано {i}/{len(article_paths)} статей')
        time.sleep(0.3)
    return ''.join(parts)


def download_one(title: str, public_url: str, force: bool = False) -> dict:
    """Скачать ОДИН документ ФЗ или ФКЗ через публичный www.consultant.ru.

    Раскладывает по подпапкам fz/ или fkz/ согласно meta['kind'].
    """
    slug = safe_fn(title)
    meta = parse_law_meta(title)
    kind_key = _classify(meta)
    if kind_key == 'unknown':
        log.warning(f'  Не классифицирован: {title[:80]}')
        return {'title': title, 'url': public_url, 'status': 'error',
                'error': 'unknown_kind', 'meta': meta}

    md_dir = PATHS[kind_key]['md']
    raw_dir = PATHS[kind_key]['raw']
    md_path = md_dir / f'{slug}.md'

    if md_path.exists() and not force:
        chars = len(md_path.read_text(encoding='utf-8'))
        log.info(f'  Пропуск (есть, {kind_key}): {title[:80]} ({chars:,} симв)')
        return {'title': title, 'url': public_url, 'status': 'skipped',
                'chars': chars, 'kind': kind_key}

    log.info(f'  [{kind_key.upper()}] Скачиваем: {title[:80]}')
    resp = base_fetch(public_url)
    if not resp:
        return {'title': title, 'url': public_url, 'status': 'error',
                'error': 'fetch_failed', 'kind': kind_key}

    raw_path = raw_dir / f'{slug}.html'
    raw_path.write_text(resp.text, encoding='utf-8')

    md = _fetch_full_text(public_url, resp.text)
    if 'доступен по расписанию' in md:
        log.warning(f'  Заблокирован: {title[:60]}')
        return {'title': title, 'url': public_url, 'status': 'blocked',
                'kind': kind_key}
    if len(md) < 500:
        log.warning(f'  Слишком короткий ({len(md)} симв)')
        return {'title': title, 'url': public_url, 'status': 'error',
                'error': 'too_short', 'sample': md[:200], 'kind': kind_key}

    category = 'federal_constitutional_law' if kind_key == 'fkz' else 'federal_law'
    frontmatter = (
        '---\n'
        f'title: {title}\n'
        f'source_url: {public_url}\n'
        f'date_downloaded: {datetime.now().isoformat()}\n'
        f'category: {category}\n'
        f'kind: {meta.get("kind") or ""}\n'
        f'number: {meta.get("number") or ""}\n'
        f'date: {meta.get("date") or ""}\n'
        f'edition_date: {meta.get("edition_date") or ""}\n'
        '---\n\n'
    )
    md_path.write_text(frontmatter + f'# {title}\n\n' + md, encoding='utf-8')
    lines = md.count('\n') + 1
    log.info(f'  OK [{kind_key.upper()}]: {lines} строк, {len(md):,} симв')
    return {
        'title': title, 'url': public_url, 'status': 'ok',
        'chars': len(md), 'lines': lines, 'slug': slug, 'meta': meta,
        'kind': kind_key,
    }


# ──────────────────────────────────────────────────────────────────────
# Main run / check / notify
# ──────────────────────────────────────────────────────────────────────

def run(smoke: Optional[int], force: bool, only: Optional[str] = None) -> List[dict]:
    log.info('=' * 64)
    flt = f'only={only}, ' if only else ''
    log.info(f'Федеральные законы ({flt}{"smoke=" + str(smoke) if smoke else "all"})')
    log.info('=' * 64)

    # Фаза 1: discovery через Playwright (нужны session-cookies + JS-навигация)
    results: List[dict] = []
    with ConsultantSession(headless=True) as sess:
        items = discover_federal_laws(sess, limit=smoke, only=only)

    # Фаза 2: скачивание через requests + cookies (на публичных www-URL)
    log.info(f'Найдено для выкачки: {len(items)}')
    for i, it in enumerate(items, 1):
        log.info(f'[{i}/{len(items)}] {it["title"][:90]}')
        if not it.get('public_url'):
            log.warning(f'  Нет public_url (n не извлечён) — пропуск')
            results.append({'title': it['title'], 'url': it['url'],
                            'status': 'error', 'error': 'no_public_url'})
            continue
        r = download_one(it['title'], it['public_url'], force=force)
        results.append(r)
        time.sleep(2)

    # Обновим manifest
    manifest = load_manifest()
    by_url = {m['url']: m for m in manifest}
    for r in results:
        if r['status'] == 'ok':
            by_url[r['url']] = {
                'title': r['title'], 'url': r['url'], 'slug': r['slug'],
                'meta': r.get('meta', {}),
                'date_downloaded': datetime.now().isoformat(),
                'chars': r['chars'],
            }
    save_manifest(list(by_url.values()))

    ok = sum(1 for r in results if r['status'] == 'ok')
    sk = sum(1 for r in results if r['status'] == 'skipped')
    err = sum(1 for r in results if r['status'] in ('error', 'blocked'))
    log.info(f'Готово: скачано {ok}, пропущено {sk}, ошибок {err}')
    return results


def check() -> List[dict]:
    log.info('Проверка локального состояния:')
    out = []
    for k, sub in PATHS.items():
        files = sorted(sub['md'].glob('*.md'))
        log.info(f'\n  [{k.upper()}] {sub["md"]}: {len(files)} файлов')
        for f in files[:10]:
            chars = len(f.read_text(encoding='utf-8'))
            log.info(f'    {f.name} ({chars:,} симв)')
            out.append({'kind': k, 'name': f.name, 'chars': chars})
        if len(files) > 10:
            log.info(f'    ...и ещё {len(files) - 10}')
    return out


def send_telegram(results: List[dict]) -> None:
    import requests
    load_dotenv(ENV_PATH)
    token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.getenv('TELEGRAM_CHAT_ID', '321681061')
    if not token:
        log.info('TELEGRAM_BOT_TOKEN не задан — пропускаем уведомление')
        return
    ok = [r for r in results if r['status'] == 'ok']
    err = [r for r in results if r['status'] in ('error', 'blocked')]
    lines = [f'📜 *Федеральные законы*\n✅ Скачано: *{len(ok)}*']
    for r in ok[:10]:
        lines.append(f'  • {r["title"][:60]} ({r.get("chars", 0):,} симв)')
    if len(ok) > 10:
        lines.append(f'  …и ещё {len(ok) - 10}')
    if err:
        lines.append(f'❌ Ошибок: *{len(err)}*')
    try:
        requests.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            json={'chat_id': chat_id, 'text': '\n'.join(lines), 'parse_mode': 'Markdown'},
            timeout=10,
        )
    except Exception as e:
        log.warning(f'Telegram error: {e}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    g = parser.add_mutually_exclusive_group()
    g.add_argument('--smoke', type=int, metavar='N',
                   help='Smoke-режим: скачать первые N документов из отфильтрованного списка')
    g.add_argument('--all', action='store_true',
                   help='Полный режим: скачать все (12K+ ФЗ — ОПАСНО)')
    g.add_argument('--check', action='store_true', help='Только показать локальное состояние')
    parser.add_argument('--only', choices=('fz', 'fkz'),
                       help='Скачать только ФЗ или только ФКЗ (по умолчанию — оба)')
    parser.add_argument('--force', action='store_true',
                       help='Перекачать существующие')
    parser.add_argument('--notify', action='store_true', help='Отчёт в Telegram')
    args = parser.parse_args()

    if args.check:
        check(); sys.exit(0)
    if args.all:
        results = run(smoke=None, force=args.force, only=args.only)
    elif args.smoke:
        results = run(smoke=args.smoke, force=args.force, only=args.only)
    else:
        parser.error('Укажите --smoke N, --all или --check')

    if args.notify:
        send_telegram(results)
