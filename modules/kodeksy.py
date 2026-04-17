"""
modules/kodeksy.py — загрузка всех Кодексов РФ

Использование:
    python3 modules/kodeksy.py
    python3 modules/kodeksy.py --force
    python3 modules/kodeksy.py --check
    python3 modules/kodeksy.py --notify
"""
import os, sys, time, logging, argparse, requests
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
from modules.base import fetch, extract_markdown, save_document, safe_fn
from config import BASE_DIR

RAW_DIR = BASE_DIR / "kodeksy" / "raw-html"
MD_DIR  = BASE_DIR / "kodeksy" / "converted-md"
LOG_PATH = BASE_DIR / "kodeksy.log"
BASE_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(str(LOG_PATH), encoding="utf-8"), logging.StreamHandler()]
)
log = logging.getLogger(__name__)

KODEKSY = [
    ("Гражданский кодекс РФ часть 1",         "https://www.consultant.ru/document/cons_doc_LAW_5142/"),
    ("Гражданский кодекс РФ часть 2",         "https://www.consultant.ru/document/cons_doc_LAW_9027/"),
    ("Гражданский кодекс РФ часть 3",         "https://www.consultant.ru/document/cons_doc_LAW_34154/"),
    ("Гражданский кодекс РФ часть 4",         "https://www.consultant.ru/document/cons_doc_LAW_64629/"),
    ("Налоговый кодекс РФ часть 1",           "https://www.consultant.ru/document/cons_doc_LAW_19671/"),
    ("Налоговый кодекс РФ часть 2",           "https://www.consultant.ru/document/cons_doc_LAW_28165/"),
    ("Трудовой кодекс РФ",                    "https://www.consultant.ru/document/cons_doc_LAW_34683/"),
    ("Уголовный кодекс РФ",                   "https://www.consultant.ru/document/cons_doc_LAW_10699/"),
    ("Уголовно-процессуальный кодекс РФ",     "https://www.consultant.ru/document/cons_doc_LAW_34481/"),
    ("Уголовно-исполнительный кодекс РФ",     "https://www.consultant.ru/document/cons_doc_LAW_12940/"),
    ("КоАП РФ",                               "https://www.consultant.ru/document/cons_doc_LAW_34661/"),
    ("Арбитражный процессуальный кодекс РФ",  "https://www.consultant.ru/document/cons_doc_LAW_37800/"),
    ("Гражданский процессуальный кодекс РФ",  "https://www.consultant.ru/document/cons_doc_LAW_39570/"),
    ("Земельный кодекс РФ",                   "https://www.consultant.ru/document/cons_doc_LAW_33773/"),
    ("Лесной кодекс РФ",                      "https://www.consultant.ru/document/cons_doc_LAW_64299/"),
    ("Водный кодекс РФ",                      "https://www.consultant.ru/document/cons_doc_LAW_60683/"),
    ("Жилищный кодекс РФ",                    "https://www.consultant.ru/document/cons_doc_LAW_51057/"),
    ("Градостроительный кодекс РФ",           "https://www.consultant.ru/document/cons_doc_LAW_51040/"),
    ("Семейный кодекс РФ",                    "https://www.consultant.ru/document/cons_doc_LAW_8982/"),
    ("Бюджетный кодекс РФ",                   "https://www.consultant.ru/document/cons_doc_LAW_19702/"),
    ("Воздушный кодекс РФ",                   "https://www.consultant.ru/document/cons_doc_LAW_13744/"),
    ("Кодекс торгового мореплавания РФ",      "https://www.consultant.ru/document/cons_doc_LAW_22916/"),
    ("Таможенный кодекс ЕАЭС",                "https://www.consultant.ru/document/cons_doc_LAW_215315/"),
]


def download_one(name: str, url: str, force: bool = False) -> dict:
    slug = safe_fn(name)
    md_path = MD_DIR / f"{slug}.md"
    if md_path.exists() and not force:
        chars = len(md_path.read_text(encoding='utf-8'))
        log.info(f"  Пропуск: {name} ({chars:,} симв)")
        return {"name": name, "status": "skipped", "chars": chars}
    log.info(f"  Скачиваем: {name}")
    resp = fetch(url)
    if not resp:
        return {"name": name, "status": "error", "error": "Не удалось загрузить"}
    md = extract_markdown(resp.text)
    if 'доступен по расписанию' in md:
        log.warning(f"  Заблокирован: {name}")
        return {"name": name, "status": "blocked"}
    (RAW_DIR / f"{slug}.html").parent.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_DIR / f"{slug}.html").write_text(resp.text, encoding='utf-8')
    result = save_document(name, url, md, RAW_DIR, MD_DIR, "kodeks")
    log.info(f"  OK: {result['lines']} строк, {result['chars']:,} симв")
    return result


def run(force: bool = False, check: bool = False) -> list:
    log.info("=" * 60)
    log.info(f"Кодексы РФ ({'force' if force else 'только новые'})")
    log.info("=" * 60)
    if check:
        results = []
        for name, _ in KODEKSY:
            p = MD_DIR / f"{safe_fn(name)}.md"
            if p.exists():
                results.append({"name": name, "status": "ok", "chars": len(p.read_text(encoding='utf-8'))})
            else:
                results.append({"name": name, "status": "missing"})
        return results
    results = []
    for name, url in KODEKSY:
        r = download_one(name, url, force=force)
        results.append(r)
        if r["status"] not in ("skipped",):
            time.sleep(2)
    ok = [r for r in results if r["status"] == "ok"]
    skip = [r for r in results if r["status"] == "skipped"]
    err = [r for r in results if r["status"] in ("error", "blocked")]
    log.info(f"Готово: {len(ok)} скачано, {len(skip)} пропущено, {len(err)} ошибок")
    return results


def send_telegram(results: list):
    load_dotenv(Path.home() / ".config" / "consultant" / ".env")
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "321681061")
    if not token: return
    ok  = [r for r in results if r["status"] == "ok"]
    err = [r for r in results if r["status"] in ("error", "blocked")]
    lines = [f"📚 *Кодексы РФ обновлены*\n✅ Скачано: *{len(ok)}*"]
    for r in ok:
        lines.append(f"  • {r['name']} ({r.get('chars',0):,} симв)")
    if err:
        lines.append(f"❌ Ошибок: *{len(err)}*")
    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": "\n".join(lines), "parse_mode": "Markdown"},
        timeout=10
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force",  action="store_true")
    parser.add_argument("--check",  action="store_true")
    parser.add_argument("--notify", action="store_true")
    args = parser.parse_args()
    results = run(force=args.force, check=args.check)
    if args.notify:
        send_telegram(results)
