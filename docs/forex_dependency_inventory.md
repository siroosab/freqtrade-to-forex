# Inventory وابستگی‌ها و ماژول‌های crypto-only

این سند نتیجه بررسی اولیه برای گیت بعدی پروژه است: `native parity + dependency inventory`.

## 1) دسته‌بندی پایدار برای مسیر فارکس

### لازم برای OANDA/native forex
- `pandas`, `numpy`, `bottleneck`, `numexpr`, `scipy`
- `fastapi`, `pydantic`, `uvicorn`, `pyjwt`, `websockets`, `httpx`
- `SQLAlchemy`, `requests`, `python-dateutil`, `pytz`, `packaging`
- `psutil`, `schedule`, `janus`, `aiohttp`, `asyncio`-compatible stack

این وابستگی‌ها در مسیرهای `freqtrade/forex/*` و API/UI مورد استفاده قرار می‌گیرند و برای اجرای OANDA native مستقیم لازم هستند.

### لازم برای Freqtrade core، اما نه برای یک راه‌حل فارکس محدود
- `ccxt` (برای exchange abstraction و پشتیبانیِ گسترده رمزارز)
- `python-telegram-bot`
- `pycoingecko`
- `python-rapidjson`, `orjson`
- `questionary`, `prompt-toolkit`, `rich`, `jinja2`

این بسته‌ها برای پروژهٔ اصلی Freqtrade و ابزارهای عمومی مفیدند، اما برای اجرای یک exchange خاص `oanda` و مسیر forex بدون crypto assumptions، الزام مستقیم ندارند.

## 2) ماژول‌های crypto-only یا legacy-heavy

### دسته `legacy / optional`
- `freqtrade/exchange/*` و سازوکارهای پایه CCXT
- `freqtrade/plugins/pairlists/*` و pairlistهای مبتنی بر market metadata کریپتو
- `freqtrade/rpc/telegram.py`
- `freqtrade.rpc.fiat_convert` و converters بر پایه `CryptoToFiat`
- `docs/*` و configهای نمونه‌ای که فرض `binance`/`USDT` دارند

### دسته `adapter-possible`
- `freqtrade/exchange/oanda.py` و façade native OANDA
- `freqtrade/forex/native_core.py`
- `freqtrade/forex/native_protections.py`
- `freqtrade/forex/ledger.py`

### برچسب نهایی قابلیت‌ها برای cleanup

- **لازم:** `freqtrade/forex/*`، `freqtrade/exchange/oanda.py`، FastAPI/UI API،
	SQLite ledger و market/price/candle contracts فارکس.
- **قابل adapter:** native pairlist facade، wallet/pricing/protection bridge و
	persistence facade؛ این‌ها باید contractهای Freqtrade را بدون واردکردن فرض
	crypto پیاده کنند.
- **خارج از scope:** Telegram، FreqUI operational plugins، crypto pairlists
	پویا و ابزارهای fiat conversion برای مسیر اصلی فارکس.
- **حذف‌شدنی:** importها و dependencyهایی که فقط در مسیر CCXT/crypto استفاده
	می‌شوند، اما حذف آن‌ها فقط پس از full regression و native data gate مجاز است.

این ماژول‌ها از آن‌جا که برای فارکس به‌صورت native و broker-aware نوشته شده‌اند، می‌توانند جایگزین یا جداسازی‌شدهٔ بخش‌های legacy شوند.

## 3) تصمیم پیشنهادی برای cleanup

| دسته | مثال‌ها | تصمیم |
|---|---|---|
| لازم | `freqtrade/forex/*`, `fastapi`, `pandas`, `oanda` adapter | حفظ و harden در native path |
| قابل adapter | `freqtrade/exchange/oanda.py`, `ledger.py`, `native_core.py`, `native_protections.py` | جایگزین یا جداکننده legacy |
| خارج از scope | `telegram`, `frequi`, UI operational plugins | plugin/optional |
| حذف‌پذیر | crypto-only pairlists، legacy CCXT-only assumptions، ساختارهای نام‌گذاری غیر فارکس | حذف پس از parity gate |

1. `ccxt` را در مسیر `oanda` به‌صورت non-required برای forex adapter نگه داریم.
2. ماژول‌های `pairlist` و `market` کریپتو را به‌صورت optional و خارج از core forex path حفظ کنیم.
3. `telegram`/`rpc` را فقط به‌صورت plugin operational نگه داریم.
4. `freqtrade/forex/*` به عنوان مسیر اصلی native Forex و OANDA باقی بماند.
5. حذف نهایی فقط بعد از green test suite و parity gate انجام شود.

## 4) نتیجه فعلی

بررسی اولیه نشان می‌دهد که برای پروژهٔ فارکس، بخش اصلی مسیر اصلی عبارت‌اند از:
- `freqtrade/forex/*`
- `freqtrade/forex/native_*`
- `freqtrade/exchange/oanda.py`
- `freqtrade/forex/api.py`

در این مرحله، وابستگی‌های crypto-only از مسیر پیش‌فرض نصب حذف شده‌اند و به‌صورت `optional`/`crypto` منتقل شده‌اند؛ بدین ترتیب نصب پایهٔ فارکس فقط شامل core/native path می‌شود و legacy crypto tools به‌صورت explicit opt-in باقی می‌مانند.

### وضعیت cleanup
- [x] `ccxt` از وابستگی‌های پیش‌فرض حذف شد و به extras `crypto` منتقل شد.
- [x] `python-telegram-bot`، `pycoingecko`، `jinja2`، `rich` و ابزارهای CLI/UX مرتبط به extras `crypto` منتقل شدند.
- [x] `questionary` و `prompt-toolkit` از نصب پیش‌فرض حذف شدند و به‌عنوان toolchain legacy/optional حفظ شدند.
- [x] مسیر `freqtrade/forex/*` و OANDA/native برای نصب پایه باقی ماند.
- [ ] حذف نهایی moduleهای legacy فقط پس از green regression suite و جهت‌گیری واضح پروژه انجام شود.
