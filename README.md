# OpenClaw Consultant Tools

Набор модулей для работы с КонсультантПлюс в рамках проекта OpenClaw.

## Модули

| Модуль | Описание | Статус |
|--------|----------|--------|
| `modules/kodeksy.py` | Загрузка всех Кодексов РФ (23 документа) | ✅ |
| `modules/hotdocs.py` | Еженедельные горячие НПА | ✅ |
| `modules/federal_laws.py` | Федеральные законы (ФЗ + ФКЗ) | ✅ |
| `modules/gov_decrees.py` | Постановления Правительства | 🔄 |
| `modules/court_practice.py` | Судебная практика (КС РФ, ВС РФ) | 🔄 |

## Установка

```bash
pip install -r requirements.txt
playwright install chromium
```

## Использование

```bash
# Кодексы
python3 modules/kodeksy.py
python3 modules/kodeksy.py --force

# Горячие НПА  
python3 modules/hotdocs.py

# Федеральные законы (ФЗ + ФКЗ, автодискавер из карточки поиска К+)
python3 modules/federal_laws.py --smoke 10        # тест: первые 10
python3 modules/federal_laws.py --smoke 5 --only fz   # только ФЗ
python3 modules/federal_laws.py --smoke 5 --only fkz  # только ФКЗ
python3 modules/federal_laws.py --check           # что уже скачано
python3 modules/federal_laws.py --all             # все ~12K (долго!)

# Обновить сессию К+
python3 auth/session.py
```

Файлы складываются в `~/consultant-data/federal-laws/{fz,fkz}/converted-md/`.

## Интеграция с OpenClaw

Модули подключаются как tools к агенту `legal` через `tools/openclaw_tool.py`.
