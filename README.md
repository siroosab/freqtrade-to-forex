# Forex Trading Platform

یک پلتفرم مستقل برای اجرای استراتژی‌های معامله‌گری فارکس، دریافت داده‌های بازار،
بک‌تست، مدیریت ریسک و اتصال به حساب OANDA Practice است. رابط کاربری از طریق API
داخلی پروژه ارائه می‌شود و اطلاعات حساب از صفحه تنظیمات سرور وارد می‌شود.

## امکانات اصلی

- اجرای API و رابط کاربری در یک سرویس
- اتصال به OANDA Practice برای دریافت داده و اجرای معاملات آزمایشی
- بک‌تست و تحلیل عملکرد استراتژی‌ها
- مدیریت موقعیت، حد ریسک و هزینه‌های معامله
- اجرای استراتژی نمونه EMA Cross
- حالت Paper برای آزمایش بدون ارسال سفارش واقعی

## نصب روی Ubuntu

دستورهای زیر را روی سرور Ubuntu و از پوشه پروژه اجرا کنید.

### 1. نصب پیش‌نیازها

اسکریپت نصب به `sudo`، `git`، ابزارهای کامپایل و یکی از نسخه‌های Python
3.11 تا 3.14 نیاز دارد. اگر Python روی سرور نصب نیست، ابتدا آن را نصب کنید.

```bash
sudo apt update
sudo apt install -y git curl build-essential python3.11 python3.11-venv python3.11-dev
```

نسخه Python را بررسی کنید:

```bash
python3.11 --version
```

### 2. دریافت پروژه

اگر پروژه هنوز روی سرور نیست:

```bash
git clone https://github.com/siroosab/freqtrade-to-forex.git ~/forex_bot
cd ~/forex_bot
```

چون مخزن خصوصی است، GitHub هنگام clone با HTTPS به نام کاربری و Personal
Access Token نیاز دارد؛ رمز حساب GitHub را وارد نکنید.

اگر می‌خواهید از SSH استفاده کنید، کلید باید روی خود VPS ساخته و به حساب
GitHub اضافه شده باشد:

```bash
ssh-keygen -t ed25519 -C "vps-forex-bot"
cat ~/.ssh/id_ed25519.pub
```

مقدار نمایش‌داده‌شده را در GitHub در مسیر `Settings > SSH and GPG keys` اضافه
کنید، سپس روی VPS تست کنید:

```bash
ssh -T git@github.com
git clone git@github.com:siroosab/freqtrade-to-forex.git ~/forex_bot
cd ~/forex_bot
```

### 3. نصب اولیه یا نصب ناموفق قبلی

این دستورها را وقتی استفاده کنید که پروژه تازه روی سرور clone شده است، یا نصب
قبلی قبل از ساخته‌شدن کامل `.venv` متوقف شده است:

```bash
cd ~/forex_bot
git pull --ff-only origin stable
chmod +x setup.sh
./setup.sh --install-forex
```

این دستورها محیط مجازی `.venv` را می‌سازند، وابستگی‌های لازم را نصب می‌کنند و
فایل تنظیمات اولیه را در `user_data/config.json` ایجاد می‌کنند. در این مرحله
هیچ اطلاعات حسابی در فایل‌ها یا دستور نصب ذخیره نمی‌شود. این مسیر را برای
به‌روزرسانی روزمره استفاده نکنید، چون `.venv` را دوباره می‌سازد.

### 4. فعال‌کردن محیط Python

```bash
source .venv/bin/activate
```

برای اطمینان از نصب:

```bash
python --version
python -c "import freqtrade; print('Forex platform is ready')"
```

### 5. اجرای API

```bash
python -m uvicorn freqtrade.forex.api:app --host 0.0.0.0 --port 8090
```

این API build production رابط React را نیز از همان پورت سرو می‌کند؛ بنابراین
برای استفاده معمول روی VPS نیازی به اجرای جداگانه Vite یا پورت `5173` نیست.

### احراز هویت رابط و API

پیش از ورود به رابط، در محیط اجرای API متغیر `FOREX_API_USERS_JSON` را با
کاربران موردنیاز تنظیم کنید. هیچ حساب یا گذرواژهٔ پیش‌فرضی وجود ندارد و بدون
کاربر پیکربندی‌شده، ورود با خطای پیکربندی رد می‌شود. مقدار این متغیر باید یک
شیء JSON از نام کاربری به گذرواژه و نقش باشد:

```json
{
  "operator": {
    "password": "REPLACE_WITH_A_LONG_RANDOM_PASSWORD",
    "role": "operator"
  },
  "admin": {
    "password": "REPLACE_WITH_ANOTHER_LONG_RANDOM_PASSWORD",
    "role": "admin"
  }
}
```

گذرواژه‌ها باید حداقل ۱۲ نویسه داشته باشند. مقدار JSON را در secret manager یا
فایل محیطی خارج از مخزن نگه دارید و آن را commit نکنید. نقش‌های مجاز
`viewer`، `operator` و `admin` هستند. نشست پس از ۸ ساعت منقضی می‌شود و عملیات
تغییردهنده علاوه بر نقش به نشست معتبر و CSRF همان نشست نیاز دارند.

پس از ورود به `/login`، در صورت نیاز به راه‌اندازی اولیه به صفحه `/setup`
هدایت می‌شوید. اطلاعات OANDA فقط پس از ورود ارسال و ذخیره می‌شوند. برای استفاده
از راه‌اندازی اولیه، حساب operator یا admin لازم است.

حالا در مرورگر باز کنید:

```text
http://SERVER_IP:8090/login
```

به‌جای `SERVER_IP`، آدرس IP سرور را قرار دهید. در `/setup`، محیط Practice یا
Live و token همان محیط را انتخاب کنید. دکمه کشف حساب فقط درخواست‌های GET
می‌فرستد؛ برای Live فقط CFD با کد `003` و Spread Betting با کد `002` قابل
انتخاب‌اند. در Practice اگر OANDA tag نوع حساب را برنگرداند، حساب فقط وقتی با
عنوان `Practice / V20` نمایش داده می‌شود که summary و فهرست instrumentها قابل
خواندن باشند. حساب‌های دیگر قابل انتخاب نیستند.

برای Live، تأیید جداگانه در صفحه لازم است و سرور نیز باید با
`OANDA_LIVE_CONFIRM=1` راه‌اندازی شده باشد. این flag فقط فعال‌سازی حساب Live را
مجاز می‌کند و انتخاب حالت کاربر همان Practice یا Live است؛ هیچ مود سومی در setup
وجود ندارد. بعد از تأیید حساب، ابزارهای معاملاتی و محدودیت ریسک را تنظیم کنید.

اگر API را با systemd کاربر اجرا می‌کنید و عمداً می‌خواهید Live را در setup
فعال کنید، این override را بسازید و سرویس را restart کنید:

```bash
mkdir -p ~/.config/systemd/user/freqtrade-forex.service.d
printf '[Service]\nEnvironment=OANDA_LIVE_CONFIRM=1\n' > ~/.config/systemd/user/freqtrade-forex.service.d/live-confirm.conf
systemctl --user daemon-reload
systemctl --user restart freqtrade-forex
```

این flag فقط Live setup را مجاز می‌کند؛ در این پروژه حالت‌های مجاز فقط
Practice و Live هستند.

### 5.1. اجرای خودکار سیگنال‌های تأییدشده

پس از تأیید strategy و timeframe هر جفت‌ارز در Review، اجرای خودکار را از
پنل **Trading overview** در Dashboard فعال کنید. این worker کندل‌های تکمیل‌شده
را می‌خواند، سیگنال‌های long و short strategy تأییدشده را ارزیابی می‌کند و
سفارش Market را از مسیر OANDA ثبت می‌کند. وضعیت اجرا و نتیجهٔ هر چرخه در همان
پنل نمایش داده می‌شود؛ فعال‌سازی Live علاوه بر تأیید Live در Setup به دسترسی
admin و تأیید صریح کاربر نیاز دارد.

پیش از فعال‌سازی، در **Risk controls → Pre-trade** برای هر جفت‌ارز سمت مجاز،
حداکثر تعداد Units، بودجهٔ ریسک و حداکثر exposure را تنظیم کنید؛ در بخش
**After order controls** نیز stop loss را تعیین کنید. تنظیم پیش‌فرض سمت `NONE`
است؛ این حالت اجازهٔ ورود جدید نمی‌دهد. برای ورود در هر دو جهت، سمت را به
`BOTH` تغییر دهید. داشبورد مقادیر `unitsAvailable` LONG و SHORT را مستقیماً از
قیمت OANDA نمایش می‌دهد تا اپراتور بتواند سقف Units را آگاهانه انتخاب کند.
مقدار نمایش‌داده‌شده لحظه‌ای و تضمین‌شده نیست و ممکن است پیش از ارسال تغییر کند.

حجم خودکار با بودجهٔ ریسک محاسبه می‌شود و از هیچ‌یک از این محدودیت‌ها عبور
نمی‌کند: سقف Units تنظیم‌شده، حداکثر exposure، `unitsAvailable` بروکر و حجم
قابل‌مشاهده در **Depth of Market**. OANDA در پاسخ fill یا cancel نهایی است؛ اگر
قیمت، مشخصات ابزار، عمق بازار، stop loss یا اطلاعات لازم در دسترس نباشد،
سفارش ارسال نمی‌شود. تنظیمات Risk بین restartهای سرور در
`user_data/oanda/risk-config.json` نگهداری می‌شوند.

اهرم را نمی‌توان به‌عنوان پارامتر مستقل هر سفارش OANDA تنظیم کرد؛ مارجین و
`marginRate` بر اساس حساب و ابزار معاملاتی توسط OANDA تعیین می‌شوند. داشبورد
نرخ مارجین و مارجین آزاد گزارش‌شده از بروکر را نشان می‌دهد و ظرفیت قابل‌معاملهٔ
بروکر نیز در سقف Units موجود منعکس می‌شود. ورودی دستی اهرم عمداً برای جلوگیری
از نمایش کنترلی که روی سفارش اثر ندارد حذف شده است.

معاملهٔ خودکار باز با سیگنال خروج strategy یا سیگنال ورود مخالف بسته می‌شود.
در حالت معکوس‌کردن، سفارش جدید فقط پس از تأیید fill سفارش بستن ارسال می‌شود.
معاملهٔ دستی باز روی همان جفت‌ارز مانع ورود خودکار می‌شود و توسط این worker
مدیریت یا بسته نمی‌شود. خاموش‌کردن کنترل اجرای خودکار جلوی چرخه‌های بعدی را
می‌گیرد؛ برای تأیید اجرای واقعی در محیط عملیاتی، نتیجهٔ Fill/Cancel را در
Dashboard و تاریخچهٔ معاملات OANDA بررسی کنید. تست‌های پروژه با client آزمایشی
انجام می‌شوند و سفارش Live ارسال نمی‌کنند.

### 5.2. رفتار صحیح سفارش‌های Market و علت لغو توسط OANDA

در OANDA، پاسخ HTTP 200 از `POST /v3/accounts/{account}/orders` به‌تنهایی
به معنای Filled شدن سفارش نیست. برای حالت Market Order باید پاسخ کامل
بروکر بررسی شود و به‌ویژه فیلدهای `orderFillTransaction` و
`orderCancelTransaction.reason` در نظر گرفته شوند.

- اگر `orderFillTransaction` وجود داشته باشد، سفارش واقعی اجرا شده است و
  فیلد `fillPrice` باید استفاده شود.
- اگر `orderCancelTransaction` وجود داشته باشد، سفارش توسط OANDA لغو شده و
  علت دقیق در `orderCancelTransaction.reason` آمده است؛ مثلاً
  `INSUFFICIENT_MARGIN`, `CLIENT_REQUEST`, `MARKET_HALTED` و غیره.
- عبارت سادهٔ `Order Cancelled` به‌تنهایی کافی نیست، چون علت واقعی باید در
  UI و API نمایش داده شود.

در این پروژه، نتیجهٔ سفارش API باید این شکل باشد:

```json
{
  "status": "cancelled",
  "orderId": "12345",
  "transactionId": "67890",
  "fillPrice": null,
  "reason": "INSUFFICIENT_MARGIN",
  "cancelReason": "INSUFFICIENT_MARGIN"
}
```

این نکته برای تست‌های Practice و Live مهم است: نباید سفارش فقط به خاطر اینکه
HTTP 200 برگشته است، به‌عنوان Filled فرض شود؛ باید وضعیت نهایی broker
مشاهده و در داشبورد نشان داده شود.

### 5.3. رفع خطای 404 فایل‌های UI بعد از به‌روزرسانی

اگر بعد از `git pull` یا به‌روزرسانی پروژه، صفحه `/setup` باز می‌شود اما
فایل‌های JavaScript و CSS داخل `/assets/...` با خطای `404 Not Found`
برمی‌گردند، مشکل معمولاً مربوط به build قدیمی رابط کاربری است. این اتفاق
زمانی رخ می‌دهد که فایل‌های Vite جدید تولید نشده‌اند و `index.html` به نام
حافظه‌دار قدیمی اشاره می‌کند.

در این حالت، پروژه را از شاخه `stable` بگیرید، محیط مجازی را فعال کنید، UI را
بازسازی کنید و سپس سرور را دوباره راه‌اندازی کنید:

```bash
cd ~/forex_bot
git fetch --all
git checkout stable
git pull --ff-only origin stable

source .venv/bin/activate

npm --prefix apps/ui install
npm --prefix apps/ui run build

pkill -f "uvicorn freqtrade.forex.api:app" || true
python -m uvicorn freqtrade.forex.api:app --host 0.0.0.0 --port 8090
```

اگر فقط از ترمینال می‌خواهید اجرا کنید، همین دستور آخر بدون systemd کافی است.
در این حالت نباید `/assets/index-*.js` 404 بدهد و رابط کاربری باید بدون خطای
asset باز شود. اگر هنوز 404 مشاهده کردید، حتماً `Ctrl + F5` یا refresh کامل
مرورگر را انجام دهید و مطمئن شوید یک نسخهٔ قبلی از uvicorn هنوز در حال اجرا
نیست.

## به‌روزرسانی نصب موجود

### 6. به‌روزرسانی نصب سالم

اگر نصب قبلی کامل است و فقط می‌خواهید تغییرات جدید پروژه را دریافت کنید، از
پوشه پروژه و در حالی که محیط مجازی فعال نیست، فقط این دستورها را اجرا کنید:

```bash
cd ~/forex_bot
./setup.sh --update-forex
```

این دستور کد جدید را از شاخه `stable` دریافت می‌کند، وابستگی‌های Python را
به‌روز می‌کند و نصب editable پروژه را refresh می‌کند. محیط `.venv` و فایل
`user_data/config.json` حذف یا دوباره‌سازی نمی‌شوند.

قبل از به‌روزرسانی، تغییرات محلی را commit یا stash کنید. پس از پایان update،
اگر API با `systemd` اجرا می‌شود آن را restart کنید:

```bash
systemctl --user restart freqtrade-forex
systemctl --user status freqtrade-forex
```

## اجرای سرویس در پس‌زمینه

برای اجرای دائمی روی Ubuntu می‌توانید از `systemd` استفاده کنید. نصب پروژه
در این راهنما داخل `~/forex_bot` انجام شده است و فایل `freqtrade.service` نیز
همین مسیر را استفاده می‌کند. سرویس کاربر را فعال کنید:

```bash
mkdir -p ~/.config/systemd/user
cp ~/forex_bot/freqtrade.service ~/.config/systemd/user/freqtrade-forex.service
systemctl --user daemon-reload
systemctl --user enable --now freqtrade-forex
systemctl --user status freqtrade-forex
```

مشاهده لاگ‌ها:

```bash
journalctl --user -u freqtrade-forex -f
```

برای اجرای سرویس بعد از logout و reboot، یک‌بار lingering را فعال کنید:

```bash
sudo loginctl enable-linger "$USER"
```

## تنظیمات و امنیت

  reverse proxy مانند Nginx و HTTPS استفاده کنید.

```text
http://SERVER_IP:8090/health
```

## CLI دانلود داده، Hyperopt و بک‌تست

فرمان‌های زیر از endpointهای فقط‌خواندنی OANDA برای دریافت داده استفاده
می‌کنند. اعتبارنامه را در `user_data/config.json` یا متغیرهای محیطی
`OANDA_TOKEN` و `OANDA_ACCOUNT_ID` تنظیم کنید. در Windows، همین فرمان‌ها را
داخل PowerShell فعال‌شده با `.venv` اجرا کنید.

```bash
# دانلود مستقل؛ داده در کش محلی ذخیره می‌شود
python -m freqtrade.forex download-data --pair EUR/USD --timeframe 1h --count 5000

# Hyperopt استراتژی در 5m با کمتر از 30 روز بازار فارکس
python -m freqtrade.forex hyperopt --pair EUR/USD --timeframe 5m --count 5000 --epochs 30 --strategy ForexEmaStrategy

# آموزش/بازیابی LightGBMRegressor و Hyperopt آستانهٔ پیش‌بینی
python -m freqtrade.forex hyperopt --pair EUR/USD --timeframe 5m --count 5000 --epochs 30 --strategy ForexEmaStrategy --freqaimodel LightGBMRegressor

# بک‌تست همان بازهٔ محدود
python -m freqtrade.forex backtest --pair EUR/USD --timeframe 5m --count 5000 --strategy ForexEmaStrategy

# اجبار به دانلود تازه هنگام بک‌تست یا پاک‌کردن کش
python -m freqtrade.forex backtest --pair EUR/USD --timeframe 5m --count 5000 --strategy ForexEmaStrategy --freqaimodel LightGBMRegressor --refresh-data
python -m freqtrade.forex cache-clear --pair EUR/USD --timeframe 1h

# مشاهدهٔ strategy، timeframe و وضعیت تأیید جفت‌ارزها
python -m freqtrade.forex show-timeframes

# مشاهدهٔ تنظیمات و وضعیت تأیید یک جفت‌ارز مشخص
python -m freqtrade.forex show-timeframes --pair EUR/USD
```

در Hyperopt استراتژی‌های Freqtrade، جدول `minimal_roi` نیز با سه پلهٔ زمانی و
سه پلهٔ سود، متناسب با تایم‌فریم و نوسان میانهٔ True Range در کندل‌های بخش
آموزش جست‌وجو می‌شود. نوسان برای مقایسه به بازده معمول 5m نرمال می‌شود:
نوسان کمتر دامنهٔ زمان نگهداری را بلندتر و هدف سود را کوچک‌تر می‌کند؛ نوسان بیشتر
دامنهٔ زمان را کوتاه‌تر و هدف سود را متناسباً بزرگ‌تر می‌کند. هدف‌ها بر اساس
حرکت موردانتظار در افق هر پله تعیین می‌شوند، محدود می‌مانند و با دقت اعشاری
مناسب برای فارکس نمونه‌گیری می‌شوند. دادهٔ اعتبارسنجی در برآورد نوسان وارد
نمی‌شود. گزارش Hyperopt مقدار نوسان معمول 5m و طبقهٔ کم/متوسط/زیاد آن را نیز
نمایش می‌دهد. بهترین جدول در گزارش با نام
`best_minimal_roi` ذخیره می‌شود. پارامترهای فضای `sell`/`exit` حذف می‌شوند؛
پارامترهای ورود فروش (مانند آستانهٔ ورود short) باید در استراتژی با فضای `buy`
تعریف شوند. در بک‌تست، خروج بر اساس ROI مستقل از سیگنال خروج انجام می‌شود.
در Hyperopt وب، جدول ROI و پارامترهای سازندهٔ آن در گزارش و نتیجهٔ منتظر تأیید
ذخیره می‌شوند؛ پس از تأیید، همراه همان جفت‌ارز، strategy و تایم‌فریم ثبت و در
سیگنال‌ها و بک‌تست بعدی (از جمله مسیر FreqAI) اعمال می‌شوند. Hyperopt زمان‌بندی‌شده
نیز از همین مسیر استفاده می‌کند؛ گزارش تأییدنشده به‌تنهایی تنظیم زنده را عوض نمی‌کند.

`ForexEmaStrategy` جهت 4h را با EMA سریع/کند و شیب EMA سریع می‌سنجد؛ در اجرای
زنده، آخرین کندل در حال تشکیل 4h نیز در این جهت‌سنجی وارد می‌شود. ورود 5m فقط
پس از بازپس‌گیری EMA سریع و تأیید RSI، و هم‌جهت با روند 4h مجاز است. پارامترهای
EMAهای هر دو تایم‌فریم، RSI و حداقل ADX روند 4h در Hyperopt قابل تنظیم‌اند؛
حداقل ADX صفر فیلتر روند را غیرفعال می‌کند. برای AI، مدل
`LightGBMRegressor` بازده آیندهٔ کندل‌های بسته‌شده را پیش‌بینی می‌کند و پیش‌بینی
فقط وقتی اجازهٔ ورود دارد که setup تایم‌فریم پایین و جهت 4h نیز تأییدش کنند.
نتیجهٔ 30 روز صرفاً غربال اولیه است؛ پیش از استفادهٔ عملی، آن را روی بازه‌های
جداگانه و جفت‌ارزهای دیگر نیز ارزیابی کنید.

زیر‌فرمان‌های `show-timeframes` تنظیمات محلی را بدون اتصال به OANDA نمایش
می‌دهند. پیش از اجرای `dry-run`، مطمئن شوید strategy و timeframe هر جفت‌ارز با
نسخهٔ تأییدشده در Review مطابقت دارند.

Hyperopt ابتدا مدل را با featureها و targetهای strategy آموزش می‌دهد، یا
در صورت تطبیق `identifier`، داده، feature schema و تنظیمات مدل از cache
می‌خواند؛ سپس پارامترهای قابل‌بهینه‌سازی strategy را روی predictionهای
validation بررسی می‌کند. بک‌تست همان مدل/prediction cache و پارامترهای گزارش
Hyperopt را reuse می‌کند و نتیجه را فقط برای بازهٔ out-of-sample می‌سنجد.
گزارش Hyperopt، وزن مدل، prediction cache و گزارش بک‌تست در
`user_data/hyperopt_results/` ذخیره می‌شوند.

در بک‌تست و Hyperopt، حدضرر و حدسود با OHLC کندل‌های موجود شبیه‌سازی می‌شوند؛
این شبیه‌سازی tick-level نیست و اگر هر دو سطح در یک کندل لمس شوند، حدضرر
محافظه‌کارانه زودتر فرض می‌شود.

خلاصهٔ جدول‌مانند بک‌تست شامل تعداد معاملات، wins/draws/losses، win rate،
سود خالص و میانگین هر معامله به ارز حساب، gross profit/loss، profit factor،
حداکثر drawdown، هزینه‌ها و متوسط مدت معامله است؛ JSON کامل معاملات و منحنی
equity همچنان پس از جدول چاپ می‌شود. در `user_data/config.json`، بخش
`freqai` برای `identifier`، train/backtest window، featureها، target horizon،
split و `model_training_parameters` قابل تنظیم است. CLI برای ترکیب با یک
strategy، وجود featureهای `%`، targetهای `&`، فراخوانی `self.freqai.start()`
و مصرف target در منطق ورود/خروج را لازم می‌داند.

## اجرای تست‌ها

با فعال‌بودن محیط مجازی:

```bash
python -m pytest tests/forex tests/test_ui_backend_contract.py -q
```

## مستندات تکمیلی

- [راهنمای نصب Forex](docs/forex_setup.md)
- [راهنمای اجرای OANDA Practice](docs/oanda_practice.md)
- [برنامه توسعه پروژه](docs/forex_project_plan.md)
- [تاریخچه تغییرات Forex](docs/forex_changelog.md)

## هشدار ریسک

این نرم‌افزار برای آزمایش و توسعه ساخته شده است. ابتدا با حساب Practice و
حالت Paper کار کنید. معامله‌گری با سرمایه واقعی ریسک مالی دارد و مسئولیت
تصمیم‌ها و نتایج استفاده از نرم‌افزار بر عهده کاربر است.