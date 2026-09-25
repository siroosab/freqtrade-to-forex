# Baseline پروژه فارکس

تاریخ ثبت: 2026-09-17

## محیط

- Python: `3.14.0`
- Freqtrade source revision: `2026.8-dirty`
- Freqtrade base tag: `2026.8`
- OANDA API: REST v20 / Practice endpoint configured by `OANDA_ENVIRONMENT=practice`
- Workspace: `d:\SIROOS\freqtrade_to_forex`
- وضعیت نصب: اجرای local checkout موفق است؛ package با `pip show freqtrade` در محیط نصب نیست.
- UI: در این baseline خارج از scope است.

## Revision و وضعیت کاری

- Git revision: `9f10e35`
- Working tree شامل تغییرات پروژه فارکس و فایل‌های مستندات است؛ این baseline آن‌ها را revert نمی‌کند.

## دستورهای رسمی پروژه

```powershell
Set-Location 'd:\SIROOS\freqtrade_to_forex'
python -m pytest tests/forex/test_oanda.py tests/forex/test_oanda_practice_e2e.py -q
python -m freqtrade.forex --help
python -m freqtrade.forex health
python -m freqtrade.forex paper-report --ledger user_data/oanda/paper.sqlite
python -m freqtrade.forex dry-run --pair EUR/USD --timeframe 5m --steps 1 --stop-pips 15 --ledger user_data/oanda/paper.sqlite
python -m freqtrade.forex backtest --pair EUR/USD --timeframe 5m --count 500 --stop-pips 15 --slippage 0.00001 --financing-rate-per-day 0.00001
python -m freqtrade.forex hyperopt --pair EUR/USD --timeframe 5m --count 500 --slippage 0.00001 --financing-rate-per-day 0.00001
python -m freqtrade.forex practice-report
python -m freqtrade.forex practice-run --sessions london,new-york --steps 1
```

تست‌های OANDA که credential می‌خواهند فقط با token Practice که در همان shell تنظیم
شده اجرا شوند. token نباید در فایل، commit یا chat ذخیره شود.

## نتایج قابل تکرار

### آخرین اجرای baseline

آخرین اجرای محلی در 2026-09-23 با virtual environment پروژه انجام شد:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/forex/test_oanda.py tests/forex/test_oanda_practice_e2e.py -q
Push-Location apps/ui; npm run build; Pop-Location
```

نتیجه:

- `136 passed, 2 skipped`
- UI build با TypeScript و Vite موفق شد.
- warningهای موجود از FastAPI/Starlette lifecycle و TestClient هستند و failure ایجاد نکردند.
- lint محدود با Ruff اجرا شد، اما به‌دلیل خطاهای style موجود در سورس و تست‌ها green نیست؛ این نتیجه نباید با شکست تست‌های رفتاری اشتباه شود.

### تست اختصاصی فارکس

دستور:

```powershell
python -m pytest tests/forex/test_oanda.py -q
```

نتیجه در 2026-09-18:

- `111 passed, 2 skipped`
- یک `StarletteDeprecationWarning` درباره استفاده از `httpx` در TestClient
- failure مشاهده نشد.
- دو skip مربوط به E2E اختیاری Practice هستند و بدون credential هیچ requestی
	ارسال نمی‌کنند.

برای اجرای تکرارپذیر در این workspace، command ترجیحی با interpreter صریح
virtual environment این است:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/forex/test_oanda.py tests/forex/test_oanda_practice_e2e.py -q
```

### CLI

دستور:

```powershell
python -m freqtrade.forex --help
```

نتیجه:

- CLI با موفقیت load شد.
- commandهای موجود: `health`, `paper-report`, `dry-run`, `practice`,
  `practice-report`, `practice-run`, `backtest`, `hyperopt`.

## مواردی که در این baseline اجرا نشدند

- `health`, `dry-run`, `backtest` و `hyperopt` به credential OANDA نیاز دارند.
- در shell جاری credential وجود نداشت.
- token قبلی در گفتگو افشا شده بود و نباید برای baseline دوباره استفاده شود؛ قبل از اجرای این چهار command باید token در OANDA revoke و token جدید در shell تنظیم شود.
- lint کامل repository فعلاً به‌دلیل خطاهای style قدیمی سبز نیست؛ smoke surface
	جدید با command زیر قابل تکرار است:

```powershell
& .\.venv\Scripts\python.exe -m ruff check freqtrade/forex freqtrade/exchange/oanda.py freqtrade/exchange/check_exchange.py tests/forex/test_oanda.py --select E,F
```

- type-check رسمی با mypy در `requirements-dev.txt` و تنظیمات `pyproject.toml`
	وجود دارد. command قابل تکرار آن:

```powershell
& .\.venv\Scripts\python.exe -m mypy freqtrade/forex freqtrade/exchange/oanda.py freqtrade/exchange/check_exchange.py --follow-imports=skip
```

- اجرای فعلی mypy به‌دلیل خطاهای type موجود در API/AI/ledger سبز نیست و باید به‌عنوان
	گیت باز باقی بماند.

## معیار خروج از مرحله 0

- [x] roadmap اصلی در `docs/forex_project_plan.md` وجود دارد.
- [x] نسخه محیط و revision در این فایل ثبت شده است.
- [x] commandهای رسمی و قابل تکرار ثبت شده‌اند.
- [x] تست اختصاصی، CLI baseline، آخرین UI build و commandهای lint/type-check ثبت شده‌اند.
- [ ] health، dry-run، backtest و hyperopt با token جدید Practice در یک اجرای baseline ثبت شوند.
- [ ] command و configuration رسمی type-check اضافه شود.

## وضعیت

مرحله 0 هنوز کامل نشده است. مانع‌های باقی‌مانده فقط baseline امن OANDA و تعیین ابزار
lint/type-check هستند؛ تا رفع آن‌ها وارد توسعه domain model مرحله 1 نمی‌شویم.
