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

حالا در مرورگر باز کنید:

```text
http://SERVER_IP:8090/setup
```

به‌جای `SERVER_IP`، آدرس IP سرور را قرار دهید. در `/setup`، محیط Practice یا
Live و token همان محیط را انتخاب کنید. دکمه کشف حساب فقط درخواست‌های GET
می‌فرستد؛ برای Live فقط CFD با کد `003` و Spread Betting با کد `002` قابل
انتخاب‌اند. در Practice اگر OANDA tag نوع حساب را برنگرداند، حساب فقط وقتی با
عنوان `Practice / V20` نمایش داده می‌شود که summary و فهرست instrumentها قابل
خواندن باشند. حساب‌های دیگر قابل انتخاب نیستند.

برای Live، تأیید جداگانه در صفحه لازم است و سرور نیز باید با
`OANDA_LIVE_CONFIRM=1` راه‌اندازی شده باشد. ابتدا execution mode را روی Dry-run
نگه دارید. بعد از تأیید حساب، ابزارهای معاملاتی و محدودیت ریسک را تنظیم کنید.

اگر API را با systemd کاربر اجرا می‌کنید و عمداً می‌خواهید Live را در setup
فعال کنید، این override را بسازید و سرویس را restart کنید:

```bash
mkdir -p ~/.config/systemd/user/freqtrade-forex.service.d
printf '[Service]\nEnvironment=OANDA_LIVE_CONFIRM=1\n' > ~/.config/systemd/user/freqtrade-forex.service.d/live-confirm.conf
systemctl --user daemon-reload
systemctl --user restart freqtrade-forex
```

این flag فقط Live setup را مجاز می‌کند؛ تنظیمات setup همچنان execution را روی
Dry-run نگه می‌دارند.

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

- اطلاعات حساب را فقط از صفحه `/setup` و روی اتصال امن HTTPS وارد کنید.
- پورت `8090` را مستقیماً روی اینترنت عمومی باز نگذارید؛ برای production از
  reverse proxy مانند Nginx و HTTPS استفاده کنید.
- فایل‌های داخل `user_data` و credentialها را commit نکنید.
- برای بررسی سلامت API، آدرس زیر را باز کنید:

```text
http://SERVER_IP:8090/health
```

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