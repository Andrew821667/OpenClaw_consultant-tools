"""
modules/federal_laws.py — загрузка ключевых Федеральных законов РФ

Использование:
    python3 modules/federal_laws.py
    python3 modules/federal_laws.py --force
    python3 modules/federal_laws.py --notify
"""
import os, sys, time, logging, argparse, requests
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
from modules.base import fetch, extract_markdown, save_document, safe_fn
from config import BASE_DIR

RAW_DIR  = BASE_DIR / "federal-laws" / "raw-html"
MD_DIR   = BASE_DIR / "federal-laws" / "converted-md"
LOG_PATH = BASE_DIR / "federal_laws.log"
BASE_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(str(LOG_PATH), encoding="utf-8"), logging.StreamHandler()]
)
log = logging.getLogger(__name__)

FEDERAL_LAWS = [
    # Корпоративное / M&A
    ("ФЗ об ООО",                              "https://www.consultant.ru/document/cons_doc_LAW_17819/"),
    ("ФЗ об АО",                               "https://www.consultant.ru/document/cons_doc_LAW_10418/"),
    ("ФЗ о банкротстве",                       "https://www.consultant.ru/document/cons_doc_LAW_39331/"),
    ("ФЗ о госрегистрации юрлиц и ИП",        "https://www.consultant.ru/document/cons_doc_LAW_36далее/"),

    # Контрактная система / закупки
    ("ФЗ 44-ФЗ о контрактной системе",        "https://www.consultant.ru/document/cons_doc_LAW_144624/"),
    ("ФЗ 223-ФЗ о закупках",                  "https://www.consultant.ru/document/cons_doc_LAW_116964/"),

    # АПК / земля / сельское хозяйство
    ("ФЗ об обороте земель сельхозназначения", "https://www.consultant.ru/document/cons_doc_LAW_37816/"),
    ("ФЗ о крестьянском хозяйстве",           "https://www.consultant.ru/document/cons_doc_LAW_79598/"),
    ("ФЗ о развитии сельского хозяйства",     "https://www.consultant.ru/document/cons_doc_LAW_64930/"),
    ("ФЗ о государственной поддержке АПК",    "https://www.consultant.ru/document/cons_doc_LAW_64930/"),

    # Трудовое
    ("ФЗ о занятости населения",               "https://www.consultant.ru/document/cons_doc_LAW_60"),
    ("ФЗ об охране труда",                     "https://www.consultant.ru/document/cons_doc_LAW_406237/"),

    # Налоги / финансы
    ("ФЗ о бухгалтерском учете",               "https://www.consultant.ru/document/cons_doc_LAW_122855/"),
    ("ФЗ об аудиторской деятельности",         "https://www.consultant.ru/document/cons_doc_LAW_96662/"),

    # Исполнение / суды
    ("ФЗ об исполнительном производстве",      "https://www.consultant.ru/document/cons_doc_LAW_71450/"),
    ("ФЗ о третейских судах",                  "https://www.consultant.ru/document/cons_doc_LAW_200300/"),

    # Защита прав
    ("ФЗ о защите прав потребителей",          "https://www.consultant.ru/document/cons_doc_LAW_305/"),
    ("ФЗ о персональных данных",               "https://www.consultant.ru/document/cons_doc_LAW_61801/"),
    ("ФЗ о лицензировании",                    "https://www.consultant.ru/document/cons_doc_LAW_126558/"),
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
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_DIR / f"{slug}.html").write_text(resp.text, encoding='utf-8')
    result = save_document(name, url, md, RAW_DIR, MD_DIR, "federal_law")
    log.info(f"  OK: {result['lines']} строк, {result['chars']:,} симв")
    return result


def run(force: bool = False) -> list:
    log.info("=" * 60)
    log.info(f"Федеральные законы ({'force' if force else 'только новые'})")
    log.info("=" * 60)
    results = []
    for name, url in FEDERAL_LAWS:
        r = download_one(name, url, force=force)
        results.append(r)
        if r["status"] not in ("skipped",):
            time.sleep(2)
    ok  = [r for r in results if r["status"] == "ok"]
    skip = [r for r in results if r["status"] == "skipped"]
    err  = [r for r in results if r["status"] in ("error", "blocked")]
    log.info(f"Готово: {len(ok)} скачано, {len(skip)} пропущено, {len(err)} ошибок")
    return results


def send_telegram(results: list):
    load_dotenv(Path.home() / ".config" / "consultant" / ".env")
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "321681061")
    if not token: return
    ok  = [r for r in results if r["status"] == "ok"]
    err = [r for r in results if r["status"] in ("error", "blocked")]
    lines = [f"📜 *Федеральные законы обновлены*\n✅ Скачано: *{len(ok)}*"]
    for r in ok[:10]:
        lines.append(f"  • {r['name']} ({r.get('chars',0):,} симв)")
    if len(ok) > 10:
        lines.append(f"  ...и ещё {len(ok)-10}")
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
    parser.add_argument("--notify", action="store_true")
    args = parser.parse_args()
    results = run(force=args.force)
    if args.notify:
        send_telegram(results)
