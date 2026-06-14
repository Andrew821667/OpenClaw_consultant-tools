# -*- coding: utf-8 -*-
"""Edition-diff: для скачанных НПА сверяет редакцию в К+ (navigate по doc_id →
заголовок «ред. от DATE») со сохранённой в .md. Изменённые перекачивает целиком
(перезапись .md, frontmatter сохраняется, edition обновляется) и пишет их doc_id
в манифест refs/changed_docids.txt — для точечного `consultant_importer --update
--only-docids <…>` + `kb_embed --refresh --only-docids <…>` + relink.

Запуск:  python -m modules.edition_check [--limit N] [--category laws|decrees|all]
"""
import sys, re, glob, argparse, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import html2text                                              # noqa: E402
from modules.federal_laws import ConsultantSession, kill_restriction  # noqa: E402

BASE = "/Users/legalai/consultant-data"
CATS = {
    "laws": [BASE+"/kodeksy/converted-md/*.md", BASE+"/federal-laws/fkz/converted-md/*.md",
             BASE+"/federal-laws/fz/converted-md/*.md"],
    "decrees": [BASE+"/decrees/converted-md/*.md"],
}
MANIFEST = BASE + "/refs/changed_docids.txt"
RED = re.compile(r'ред\.\s*от\s*(\d{2})\.(\d{2})\.(\d{4})', re.I)

def edition_key(text):
    m = RED.search(text or "")
    return (m.group(3), m.group(2), m.group(1)) if m else None  # (Y,M,D)

def html_to_md(html):
    h = html2text.HTML2Text(); h.ignore_links = False; h.ignore_images = True; h.body_width = 0
    return re.sub(r"\n{4,}", "\n\n\n", h.handle(html)).strip()

def resave(path, md, new_red):
    """Перезаписать .md: сохранить frontmatter, обновить ред. в title, заменить тело."""
    old = open(path, encoding="utf-8").read()
    if old.startswith('---') and '\n---\n' in old:
        fm = old.split('\n---\n', 1)[0]
        if new_red:
            fm = re.sub(r'(ред\.\s*от\s*)\d{2}\.\d{2}\.\d{4}', rf'\g<1>{new_red}', fm)
        fm += '\n---\n\n'
    else:
        fm = ''
    Path(path).write_text(fm + md, encoding="utf-8")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", choices=["laws","decrees","all"], default="all")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    cats = ["laws","decrees"] if a.category == "all" else [a.category]
    files = []
    for c in cats:
        for pat in CATS[c]: files += sorted(glob.glob(pat))
    if a.limit: files = files[:a.limit]
    print(f"проверяю редакции: {len(files)} документов", flush=True)
    changed = []; checked = errors = 0
    with ConsultantSession(headless=True) as sess:
        p, rnd = sess.page, sess.rnd
        for i, f in enumerate(files, 1):
            head = open(f, encoding="utf-8").read(500)
            mm = re.search(r'cons_doc_LAW_(\d+)', head)
            if not mm: continue
            did = mm.group(1); saved = edition_key(head)
            try:
                p.goto(f"https://cloud.consultant.ru/cloud/cgi/online.cgi?req=doc&base=LAW&n={did}&rnd={rnd}",
                       wait_until="domcontentloaded", timeout=30000)
                time.sleep(3.5); kill_restriction(p); time.sleep(2.5)
                fr = next((x for x in p.frames if "document_inner" in x.url), None)
                if not fr: errors += 1; continue
                html = fr.content()
            except Exception as e:
                errors += 1; print(f"  err {did}: {repr(e)[:50]}", flush=True); continue
            checked += 1
            cur = edition_key(re.sub(r'<[^>]+>', ' ', html)[:600])
            if cur and cur != saved:
                md = html_to_md(html)
                if len(md) > 400 and "по расписанию" not in md.lower():
                    new_red = "%s.%s.%s" % (cur[2], cur[1], cur[0])
                    resave(f, md, new_red)
                    changed.append(did)
                    print(f"  [{i}] ИЗМЕНЕНО {did}: {saved} → {cur}", flush=True)
            if i % 50 == 0:
                print(f"  …{i}/{len(files)} проверено, изменено {len(changed)}", flush=True)
    Path(MANIFEST).write_text("\n".join(changed), encoding="utf-8")
    print(f"ИТОГО: проверено {checked}, изменено {len(changed)}, ошибок {errors}. Манифест: {MANIFEST}", flush=True)

if __name__ == "__main__":
    main()
