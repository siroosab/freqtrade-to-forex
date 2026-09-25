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
git clone git@github.com:siroosab/freqtrade-to-forex.git
cd freqtrade-to-forex
```

اگر از روش HTTPS استفاده می‌کنید:

```bash
git clone https://github.com/siroosab/freqtrade-to-forex.git
cd freqtrade-to-forex
```

### 3. نصب خودکار پروژه

```bash
chmod +x setup.sh
./setup.sh --install-forex
```

این دستور محیط مجازی `.venv` را می‌سازد، وابستگی‌های لازم را نصب می‌کند و
فایل تنظیمات اولیه را در `user_data/config.json` ایجاد می‌کند. در این مرحله
هیچ اطلاعات حسابی در فایل‌ها یا دستور نصب ذخیره نمی‌شود.

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

حالا در مرورگر باز کنید:

```text
http://SERVER_IP:8090/setup
```

به‌جای `SERVER_IP`، آدرس IP سرور را قرار دهید. در صفحه تنظیمات، اطلاعات حساب
OANDA Practice، حالت اجرا، ابزارهای معاملاتی و محدودیت ریسک را وارد کنید.

## به‌روزرسانی نصب موجود

برای دریافت تغییرات جدید، لازم نیست پروژه را دوباره نصب کنید. از پوشه اصلی
پروژه و در حالی که محیط مجازی فعال نیست، اجرا کنید:

```bash
./setup.sh --update-forex
```

این دستور کد جدید را از شاخه `stable` دریافت می‌کند، وابستگی‌های Python را
به‌روز می‌کند و نصب editable پروژه را refresh می‌کند. محیط `.venv` و فایل
`user_data/config.json` حذف یا دوباره‌سازی نمی‌شوند.

قبل از به‌روزرسانی، تغییرات محلی را commit یا stash کنید. پس از پایان update،
اگر API با `systemd` اجرا می‌شود آن را restart کنید:

```bash
sudo systemctl restart freqtrade-forex
sudo systemctl status freqtrade-forex
```

## اجرای سرویس در پس‌زمینه

برای اجرای دائمی روی Ubuntu می‌توانید از `systemd` استفاده کنید. نمونه فایل
سرویس در `freqtrade.service` قرار دارد. مسیرهای داخل فایل را با مسیر واقعی
پروژه روی سرور تطبیق دهید، سپس:

```bash
sudo cp freqtrade.service /etc/systemd/system/freqtrade-forex.service
sudo systemctl daemon-reload
sudo systemctl enable --now freqtrade-forex
sudo systemctl status freqtrade-forex
```

مشاهده لاگ‌ها:

```bash
sudo journalctl -u freqtrade-forex -f
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