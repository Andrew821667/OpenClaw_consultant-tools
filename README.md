# OpenClaw Consultant Tools

Набор модулей для работы с КонсультантПлюс в рамках проекта OpenClaw.

## Модули

| Модуль | Описание | Статус |
|--------|----------|--------|
| `modules/kodeksy.py` | Загрузка всех Кодексов РФ (23 документа) | ✅ |
| `modules/hotdocs.py` | Еженедельные горячие НПА | ✅ |
| `modules/federal_laws.py` | Федеральные законы | 🔄 |
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

# Обновить сессию К+
python3 auth/session.py
```

## Интеграция с OpenClaw

Модули подключаются как tools к агенту `legal` через `tools/openclaw_tool.py`.
