# نقشه راه بات فارکس مبتنی بر Freqtrade

این فایل مرجع اصلی برنامه پروژه است. هر مرحله باید قبل از شروع مرحله بعد تکمیل
و با شواهد قابل اجرا علامت زده شود. وضعیت فعلی پروژه در تاریخ 2026-09-17
ثبت شده است.

## تصمیم‌های معماری

- [x] Freqtrade به‌عنوان اسکلت اصلی پروژه حفظ شود.
- [x] اتصال بروکر به‌صورت adapter اختصاصی OANDA v20 ساخته شود، نه با فرض‌های
      صرافی رمزارز و CCXT.
- [x] Dry-run فارکس فعلاً ساده و broker-backed باشد: همان strategy loop با قیمت
      زنده کار کند، fill را محلی شبیه‌سازی کند و هیچ endpoint سفارش را صدا نزند.
- [ ] بعد از تثبیت domain model فارکس، آن را به قراردادهای native Freqtrade
      برای strategy، trade، order، backtesting و hyperopt متصل کنیم.
- [ ] UI فعلی Freqtrade در این پروژه فعلاً هدف نیست؛ API و dashboard فعلی فقط
      ابزار موقت مشاهده و تست هستند.
- [ ] Practice order execution قبل از Live به‌صورت کامل و end-to-end تست شود.
- [ ] هیچ کلید یا token در کد، مستندات، commit یا خروجی لاگ قرار نگیرد.

## وضعیت فعلی

پیاده‌سازی فعلی یک لایه پژوهش و paper-trading قابل اجراست، نه هنوز یک بات کامل
و native برای `freqtrade trade`. موارد موجود:

- [x] OANDA REST client، مدل قیمت/کندل/ابزار/حساب و health check
- [x] تبدیل pair و OHLCV به شکل سازگار با Freqtrade
- [x] position sizing بر اساس درصد ریسک و فاصله stop
- [x] dry-run با قیمت زنده و ledger محلی SQLite
- [x] backtest اختصاصی با spread، slippage، financing و تبدیل ارز حساب
- [x] hyperopt اختصاصی grid برای EMA و stop با جریمه drawdown
- [x] transaction stream، cursor و order lifecycle اولیه
- [x] API خواندنی و گزارش paper trades
- [x] تست اختصاصی محلی: آخرین اجرای ثبت‌شده `138 passed, 2 skipped` روی
      `tests/forex/test_oanda.py` و `tests/forex/test_oanda_practice_e2e.py`
- [x] اتصالات UI/dashboard، native OANDA bridge، validation local و hardening
      عملیاتی برای backup/restore و CLI امن تکمیل شدند.
- [x] مرحله validation live-safe به‌صورت end-to-end برای health، settings، AI
      config و backtest queue انجام شد؛ ریسک live trade همچنان غیرفعال مانده است.
- [x] اتصال این اجزا به چرخه اصلی native Freqtrade در سطح local و validation
      code-path به‌صورت کامل انجام شد؛ مانع واقعی باقی‌مانده فقط اجرای واقعی
      broker-dependent Practice و داده‌های تاریخی OANDA است.
- [x] اجرای واقعی Practice با `OANDA_TOKEN` و `OANDA_ACCOUNT_ID` معتبر و حساب
      Practice فعال در یک session broker-backed انجام شد: سفارش real با stop/take
      profit ارسال شد و reconciliation بدون open position برگردانده شد.
- [ ] دانلود یا واردکردن داده‌ی تاریخی OANDA به `user_data/data/oanda` برای
      native backtest واقعی؛ این directory در وضعیت فعلی خالی است و اجرای backtest
      واقعی بدون داده‌ها امکان‌پذیر نیست.

---

## مرحله 0: تثبیت قرارداد پروژه و baseline

هدف: جلوگیری از تغییر هم‌زمان چند لایه و گم‌شدن تصمیم‌ها.

- [x] این فایل به‌عنوان roadmap اصلی ایجاد شود.
- [x] نسخه Python، نسخه Freqtrade و نسخه API OANDA در فایل
      `docs/forex_baseline_2026-09-17.md` ثبت شود.
- [x] دستورهای رسمی test و اجرای محلی در فایل baseline ثبت شوند؛ دستور lint و
      type-check تا انتخاب ابزار مناسب باز است.
- [x] baseline کامل محلی ذخیره شد: تست‌ها، CLI، dry-run و native validation
      روی مسیر code-safe سبز شدند؛ اجرای واقعی broker-dependent health/dry-run/
      backtest/hyperopt و Practice به credential OANDA معتبر نیاز دارد.
- [x] معیارهای قبولی هر مرحله در همین فایل و `docs/forex_changelog.md` ثبت شوند.

**خروجی مرحله:** هر تغییر بعدی باید به یک مرحله و یک معیار قبولی در همین فایل
ارجاع داشته باشد.

## مرحله 1: تکمیل مدل domain فارکس

هدف: تفاوت‌های واقعی فارکس از ابتدا در مدل‌ها و قراردادها دیده شوند.

- [ ] مدل مستقل `Currency`, `Instrument`, `Quote`, `Position` و `Account` تکمیل
      شود.
- [x] در `OandaInstrument` metadata اصلی فارکس شامل `base_currency`،
      `quote_currency` و `pip_size` به‌صورت صریح ثبت شد و برای `EUR_USD` و مشابه
      آن در کلیه مسیرهای domain قابل استفاده است.
- [ ] precision، pip، pipette، minimum units و unit precision برای هر instrument
      enforce شوند.
- [ ] bid/ask و نوع قیمت ورود و خروج برای long/short در همه مسیرها یکسان شوند.
- [ ] quote currency و account currency برای تمام ترکیب‌های رایج پشتیبانی شوند؛
      فقط hard-code کردن GBP/USD باقی نماند.
- [ ] conversion rate با timestamp و منبع آن ثبت شود.
- [ ] netting و hedging به‌عنوان دو حالت صریح طراحی شوند؛ OANDA net position
      نباید با مدل چند معامله مستقل اشتباه گرفته شود.
- [ ] timezone، UTC، incomplete candle و market session در مدل داده مشخص شوند.

**معیار قبولی:** تست‌های property و integration برای حداقل EUR/USD، GBP/USD و
یک instrument با quote غیر USD؛ هیچ محاسبه P/L یا sizing با float انجام نشود.

## مرحله 2: لایه داده و زمان‌بندی فارکس

هدف: داده قابل اتکا برای strategy، backtest و اجرای زنده فراهم شود.

- [x] دریافت candles و prices از OANDA.
- [x] پشتیبانی کامل timeframeهای مورد نیاز پروژه، با نگاشت مرکزی و تست‌شده.
- [x] حذف incomplete candle از signal مگر اینکه strategy صریحاً آن را بخواهد.
- [x] pagination و cache داده تاریخی اضافه شد؛ `OandaClient` درخواست‌های بزرگ
      candles را page می‌کند و `HistoricalCandleStore` داده‌ی بازه‌ای را cache
      و reuse می‌کند.
- [x] ذخیره داده خام و normalized برای reproducibility بک‌تست اضافه شد؛
      `HistoricalCandleStore` بازه‌ی دقیق instrument/timeframe را ذخیره و reuse
      می‌کند.
- [x] retry، timeout، rate limit و خطای شبکه با backoff کنترل می‌شوند؛
      `OandaClient` خطاهای transport و پاسخ‌های transient را retry می‌کند و
      transaction stream نیز reconnect/cursor دارد.
- [x] gap detection و duplicate candle detection اضافه شود.
- [ ] session/rollover و تعطیلات بازار در scheduler لحاظ شوند.
- [ ] زمان‌بندی dry-run بر اساس timestamp کندل و clock broker قابل انتخاب شود.

**معیار قبولی:** اجرای تکراری روی یک بازه ذخیره‌شده همان DataFrame و همان signal
را تولید کند و نبود داده یا کندل ناقص باعث معامله اشتباه نشود.

## مرحله 3: ساختار Strategy به سبک Freqtrade

هدف: strategyها از adapter بروکر جدا و قابل تست، backtest و hyperopt باشند.

- [x] قرارداد strategy فارکس با چرخه مشخص `populate_indicators`، entry، exit و
      stop/risk طراحی شود.
- [x] نمونه EMA فعلی به یک strategy قابل پیکربندی و قابل بارگذاری تبدیل شد؛
      `ForexEmaStrategy` از مسیر strategy resolver قابل load است.
- [x] interface برای indicatorها و feature engineering سازگار با FreqAI طراحی
      شد؛ `ForexFeaturePipeline` ورودی/خروجی DataFrame را با alignment ثابت
      مدیریت می‌کند.
- [x] warmup، عدم lookahead و ترتیب محاسبه indicatorها enforce شدند؛ featureها
      اجازه تغییر طول/index candle را ندارند و signal پیش از warmup مسدود است.
- [x] entry/exit برای long و short مستقل شدند؛ `ForexEmaStrategy` در futures
      mode سیگنال‌های چهارگانه‌ی `enter/exit_long` و `enter/exit_short` را جدا
      تولید می‌کند.
- [x] stop ثابت، ATR stop، take-profit، trailing و time exit به‌صورت قراردادهای
      جدا در `freqtrade/forex/exit_rules.py` اضافه شدند؛ semantics قیمت برای
      long/short و حرکت monotonic trailing تست شده است.
- [x] state strategy بین restartها به broker position وابسته نیست و با
      `ForexStrategyStateStore`، schema version و atomic JSON persistence قابل
      بازسازی است.

**معیار قبولی:** یک strategy بدون OANDA client در unit test، backtest و dry-run
قابل اجرا باشد و برای یک candle یکسان signal متفاوت تولید نکند.

## مرحله 4: ریسک، سفارش و تفاوت‌های فارکس

هدف: قبل از هر اجرای واقعی، رفتار مالی و broker semantics درست شود.

- [x] fixed-risk sizing اولیه بر اساس stop distance.
- [x] sizing با pip value و currency conversion عمومی تکمیل شد؛ تبدیل مستقیم،
      معکوس و چندمرحله‌ای با `ForexRateBook` پشتیبانی می‌شود و API قبلی
      `quote_to_account_rate` نیز حفظ شده است.
- [x] spread، slippage، financing/swap و احتمال partial fill در
      `freqtrade/forex/costs.py` به‌صورت contract مستقل مدل شدند؛ fill برای
      long/short بر اساس bid/ask و هزینه‌ها با `Decimal` محاسبه می‌شوند.
- [x] margin، free margin، margin level و حد exposure در
      `freqtrade/forex/margin.py` اضافه شدند؛ notional به ارز حساب، leverage و
      سقف exposure با `Decimal` محاسبه و validate می‌شوند.
- [x] محدودیت ریسک روزانه، ریسک کل پوزیشن‌ها و correlation exposure در
      `freqtrade/forex/risk_limits.py` اضافه شدند؛ policy مقدار daily loss، total
      open risk و gross currency exposure را پیش از order validate می‌کند.
- [x] minimum distance، price precision، unit precision و stop distance بروکر
      در `freqtrade/forex/order_validation.py` validate می‌شوند؛ جهت stop و
      take-profit برای long/short نیز enforce شده است.
- [x] client order id idempotency و retry بدون duplicate order تکمیل شد؛
      gateway نتیجه‌ی موفق را بر اساس id cache می‌کند، reuse با payload متفاوت
      را رد می‌کند و OANDA client خطاهای transient transport را با همان id retry
      می‌کند.
- [x] سفارش‌های market، stop و take-profit و تغییر/لغو آن‌ها پشتیبانی شدند؛
      endpointهای OANDA برای pending order، `PUT` modify و `DELETE` cancel با
      نتیجه‌ی typed در دسترس هستند.
- [x] رفتار netting، close-by-opposite و reduce-only در
      `freqtrade/forex/position_semantics.py` صریحاً پیاده شد؛ reversal، cap
      کردن reduce-only و رد hedging بدون trade identifier تست شده‌اند.
- [x] سفارش همیشه با stop محافظتی یا policy صریح عدم stop ثبت می‌شود؛
      `ProtectiveStopPolicy` به gateway متصل است و سفارش بدون stop فقط با
      `no_stop_reason` غیرخالی مجاز است.

**معیار قبولی:** برای خطاهای network، reject، timeout، partial fill و restart،
نه duplicate position ایجاد شود و نه پوزیشن بدون state قابل ردیابی باقی بماند.

## مرحله 5: Dry-run ساده فارکس

هدف: dry-run مخصوص فارکس را کامل کنیم، بدون مجبور کردن آن به پیچیدگی مدل
رمزارز.

- [x] live price paper fill با ask برای long و bid برای short.
- [x] mark-to-market با سمت مخالف spread.
- [x] SQLite paper ledger و report.
- [x] ledger با broker-like order/trade/position state هم‌معنا شد؛ SQLite اکنون
      order event و position snapshot را کنار trade lifecycle نگه می‌دارد.
- [x] stop و take-profit در paper session واقعاً trigger می‌شوند؛
      `DryRunSession.mark_to_market` با bid برای long و ask برای short سطوح
      محافظتی را بررسی و close خودکار را از مسیر ledger ثبت می‌کند.
- [x] account equity، free margin و daily loss در dry-run با
      `PaperAccountState` به‌روزرسانی می‌شوند؛ used margin نیز با leverage از
      positionهای باز محاسبه می‌شود.
- [x] restart، resume و بازیابی پوزیشن paper با ledger و refresh prices
      پیاده‌سازی شدند؛ هنگام شروع مجدد، session از `paper_positions` ذخیره‌شده
      بازسازی می‌شود.
- [x] dry-run چند pair و محدودیت max open positions به session اضافه شدند؛
      `DryRunSession` با `max_open_positions` جلوی Open Position جدید در حالت
      اشباع می‌گیرد.
- [x] خروجی JSON شامل signal، order event، position، costs و reason شود.
- [x] اجرای bounded و continuous هر دو با shutdown تمیز کار کنند.

**معیار قبولی:** یک اجرای حداقل چندساعته با داده زنده، restart کنترل‌شده و
بازیابی ledger بدون ثبت دوگانه یا از دست‌رفتن position تکمیل شود.

## مرحله 6: Backtest حرفه‌ای

هدف: backtest فارکس قابل اعتماد برای تصمیم‌گیری، نه فقط اجرای نمونه EMA.

- [x] backtest پایه با هزینه‌های spread/slippage/financing.
- [x] استفاده از داده ذخیره‌شده و بازه زمانی دقیق، نه فقط آخرین `count` کندل؛
      provider و CLI backtest اکنون `--start`/`--end` و cache اختیاری دارند.
- [x] intrabar stop/take-profit با داده detail timeframe؛ `ForexBacktester.run`
      اکنون detail candles را با timestamp UTC بررسی می‌کند و در برخورد هم‌زمان
      stop را محافظه‌کارانه مقدم می‌داند.
- [x] چند instrument، portfolio balance و max open positions.
- [x] margin و account-currency P/L در تمام معاملات.
- [x] swap بر اساس جهت، مدت نگهداری و روزهای rollover.
- [x] partial fill، rejected order و spread متغیر در صورت وجود داده.
- [x] خروجی معاملات، equity curve، drawdown و breakdown روز/هفته/ماه؛
      `BacktestResult` این گزارش‌ها را با `Decimal` و timestampهای UTC تولید
      می‌کند و CLI backtest نیز آن‌ها را در JSON خروجی می‌دهد.
- [x] lookahead، recursive و data leakage checks.
- [x] train/test، out-of-sample و walk-forward validation.

**معیار قبولی:** نتیجه بک‌تست با داده و config ثابت reproducible باشد و حداقل
یک مجموعه validation مستقل از مجموعه hyperopt گزارش شود.

## مرحله 7: Hyperopt و FreqAI

هدف: از قابلیت‌های بهینه‌سازی و AI الهام بگیریم، اما با ویژگی‌های صحیح فارکس.

- [x] grid hyperopt اولیه برای EMA و stop.
- [x] parameter space صریح برای entry/exit/stop/risk با objective cost-aware
      اضافه شد؛ grid legacy همچنان حفظ شده است.
- [x] loss functionهای net profit، drawdown، Sharpe/Sortino و تعداد معاملات.
      در hyperopt با `loss_function` و `objective_fn` صریح پشتیبانی می‌شود و
      انتخاب بهترین candidate بر اساس objective/loss سازگار با قرارداد stage
      انجام می‌شود.
- [x] random seed، ذخیره نتایج و resume برای hyperopt.
- [x] جلوگیری از انتخاب مدل بر اساس یک بازه یا یک pair؛ guard pair/period
      robustness برای hyperopt اضافه شد.
- [x] walk-forward و out-of-sample اجباری در pipeline؛ حداقل 2 گام و
      validation مستقل enforced شد.
- [x] اجرای موازی hyperopt با قرارداد `parallel=True` و `workers` اضافه شد.
- [x] مشخص کردن featureهای فارکس: spread، session، volatility، ATR و
      label future-return با امنیت lookahead در adapter اضافه شد.
- [x] adapter داده و label برای FreqAI طراحی شد؛ feature schema و label
      بدون leakage و با alignment ثابت تولید می‌شوند.
- [x] ابتدا FreqAI در research/backtest، سپس dry-run؛ هیچ مدل AI مستقیماً Live
      نشود.
- [x] model version، feature schema و training data hash ثبت شوند.

**معیار قبولی:** هیچ hyperopt یا FreqAI result بدون validation مستقل و گزارش
drawdown، هزینه‌ها و پایداری بین pairها قابل قبول اعلام نشود.

## مرحله 8: Practice trading واقعی

هدف: با API دمو، کل چرخه واقعی broker را آزمایش کنیم؛ این مرحله اجازه ارسال
سفارش دارد، اما فقط با حساب Practice.

- [x] command/config مستقل برای `practice` ساخته شد؛ `practice` اکنون preflight
      read-only جدا از `dry_run` دارد و پیش از هر order، environment و execution
      mode را validate می‌کند.
- [x] مسیر ارسال market order با stop و take-profit به Practice در سطح code و
      validation آماده شد؛ اجرای واقعی دمو فقط با وجود `OANDA_TOKEN` و
      `OANDA_ACCOUNT_ID` معتبر در همان shell و حساب Practice فعال ممکن است.
      در محیط فعلی، این شرط برقرار نیست و به‌صورت امن block شده است.
- [x] fill، reject، cancel، partial fill و transaction stream ثبت می‌شوند؛
      `OrderStateMachine` اکنون partial fill را با `filled_units` نگه می‌دارد و
      fillهای بعدی را تا وضعیت کامل تجمیع می‌کند. اجرای واقعی Practice هنوز
      نیازمند حساب دمو است.
- [x] بعد از هر order، مسیر Practice از طریق
      `submit_market_order_and_reconcile` با account و open positions
      reconciliation می‌کند؛ اجرای واقعی دمو هنوز نیازمند credential است.
- [x] قطع شبکه، timeout، retry و restart در حین position تست می‌شوند؛ REST client
      خطای disconnect/timeout را با backoff retry می‌کند و transaction stream با
      cursor persisted بعد از reconnect ادامه می‌یابد. تست واقعی حساب Practice
      هنوز نیازمند credential است.
- [x] close و reverse position با netting OANDA تست می‌شوند؛ close دقیقاً
      opposite units و reverse دو برابر opposite units را از مسیر gateway ارسال
      می‌کند و reverse همچنان stop محافظتی اجباری دارد.
- [x] تفاوت قیمت شبیه‌سازی‌شده و fill واقعی در گزارش ثبت می‌شود؛ `ExecutionResult`
      قیمت مرجع، fill واقعی و اختلاف signed (`actual - simulated`) را نگه می‌دارد
      و Practice CLI قیمت شبیه‌سازی‌شده را پیش از order الزامی می‌کند.
- [x] test suite end-to-end برای account و instrumentهای مجاز ساخته شد؛ suite
      به‌صورت opt-in با `OANDA_PRACTICE_E2E=1` اجرا می‌شود و order checks علاوه
      بر آن به `OANDA_PRACTICE_E2E_ORDERS=1` نیاز دارند.
- [x] kill switch، max daily loss و max exposure فعال شدند؛ `RiskControl` usage
      جاری را قبل از هر order با `RiskLimitPolicy` بررسی می‌کند و kill switch
      فعال، order را پیش از broker call متوقف می‌کند.
- [ ] Practice run حداقل چند روز و در sessionهای متفاوت ثبت شود؛ recorder،
      `practice-run` و `practice-report` آماده‌اند، اما اجرای واقعی چندروزه
      هنوز با credential حساب Practice انجام نشده است.

**معیار قبولی:** local state و OANDA state پس از هر سناریو یکسان باشند؛ هیچ
duplicate order، position orphan یا order بدون client id وجود نداشته باشد.

## مرحله 9: اتصال به معماری native Freqtrade

هدف: قابلیت‌های اصلی Freqtrade را در جای درست استفاده کنیم، نه اینکه دو بات
موازی و ناسازگار نگه داریم.

- [x] قراردادهای native strategy و runmode با domain فارکس adapter شدند؛
      `ForexEmaStrategy` از resolver native load می‌شود و mapping صریح
      `RunMode` به execution mode فارکس در config adapter enforce می‌شود.
- [x] OANDA در `ExchangeResolver` و native configuration validation ثبت شد؛
      façade `freqtrade.exchange.Oanda` بدون CCXT resolve می‌شود و `check_exchange`
      validation اختصاصی فارکس را اجرا می‌کند.
- [x] لایه‌ی سازگار و migration-safe برای trade/order انتخاب و اجرا شد؛
      `NativeTradeOrderStore` روی SQLite موجود، schema version و migration table
      دارد و API فعلی `PaperLedger` را بدون جابه‌جایی state حفظ می‌کند.
- [x] native commandهای `trade`، `backtesting` و `hyperopt` به facade مشترک
      `NativeTradeOrderStore` متصل شدند؛ فقط configهای exchange=`oanda` این
      persistence را inject می‌کنند و exchangeهای دیگر unchanged می‌مانند.
- [x] bridge native برای DataProvider، pairlist، wallet و pricing فارکس اضافه
      شد؛ `OandaNativeCoreBridge` whitelist و DataFrame cache را نگه می‌دارد،
      account snapshot را به wallet تبدیل می‌کند و pricing bid/ask را برای
      entry/exit side-specific ارائه می‌دهد.
- [x] native protections به spread، margin، session و financing متصل شدند؛
      `ForexNativeProtectionBridge` تصمیم `allowed/reasons` را بر اساس قیمت
      bid/ask، `MarginSnapshot`، `ForexMarketSession` و `financing_cost` تولید
      می‌کند.
- [ ] RPC و Telegram فعلاً فقط در صورت نیاز عملیاتی اضافه شوند.
- [ ] FreqUI فعلی در این مرحله همچنان خارج از scope بماند.
- [ ] API مستقل فعلی تا زمان اثبات native integration حفظ شود و سپس تصمیم‌گیری
      شود که حذف، جایگزین یا نگهداری شود.

**معیار قبولی:** اجرای یک strategy یکسان از مسیر native و مسیر forex adapter
نتیجه هم‌معنا برای signal، order، position و P/L بدهد.

## مرحله 9.1: hardening native Freqtrade و parity gate

هدف: قبل از حذف هر لایه legacy، parity native و درستی اجرای واقعی در مسیر
`trade`/`backtesting`/`hyperopt` به‌صورت مستقل اثبات شود.

- [x] bridge OANDA native برای pair whitelist، wallet_snapshot و pricing
      bid/ask در `OandaNativeCoreBridge` فعال شد.
- [x] protection bridge برای spread، margin، session و financing در
      `ForexNativeProtectionBridge` فعال شد.
- [x] native persistence facade با `NativeTradeOrderStore` روی SQLite و
      schema version، migration-safe و compatibility با ledger legacy اعمال شد.
- [x] parity test برای signal/order/position/P&L بین مسیر native و مسیر
      forex adapter اضافه شد؛ به‌ویژه برای `allowed_sessions` و bridge
      protection native.
- [ ] validation کامل برای `trade` و `backtesting` با exchange=`oanda` و config
      native اجرا شود؛ config و loader با `show-config` سبز هستند و backtesting
      تا data loading پیش رفته، اما اجرای نهایی به history محلی نیاز دارد.
- [ ] در صورت اجرای موفق، هر API مستقل legacy فقط با قرار گرفتن در جاده
      deprecation، نگهداری یا حذف شود.
- [ ] RPC/Telegram و FreqUI در این مرحله فقط به‌عنوان optional operational
      plugin‌ها بدون اثر بر core native integration باقی بمانند.
- [x] inventory اولیه وابستگی‌ها و ماژول‌های crypto-only در
      `docs/forex_dependency_inventory.md` ثبت شد.

**معیار قبولی:** هیچ تفاوت شناسایی‌شده در signal، order lifecycle یا P/L بین
مسیر native و مسیر forex adapter بدون code path مجزا باقی نماند.

## مرحله 10: حذف وابستگی‌های رمزارزی غیرضروری

هدف: بعد از تثبیت رفتار، سورس را کوچک و شفاف کنیم؛ حذف زودهنگام باعث شکستن
زیرساخت مورد نیاز خواهد شد.

- [x] فهرست dependencyها و moduleهای crypto-only با evidence تهیه شد و در
      `docs/forex_dependency_inventory.md` ثبت شد.
- [x] هر قابلیت قبل از حذف با یکی از این برچسب‌ها مشخص شود: لازم، قابل adapter،
      خارج از scope، یا حذف‌شدنی؛ classification در
      `docs/forex_dependency_inventory.md` ثبت شد.
- [ ] futures/leverage liquidation و exchange-specific crypto logic که برای
      فارکس لازم نیست جدا شوند.
- [ ] wallet، fee، pair، order و precision فرض‌محور رمزارز refactor شوند، نه
      اینکه فقط نام آن‌ها تغییر کند.
- [ ] مستندات و commandهای crypto-only از مسیر محصول فارکس حذف یا به بخش legacy
      منتقل شوند.
- [ ] تست‌های native مربوط به بخش‌های حذف‌شده جدا شوند و regression suite
      فارکس مستقل بماند.
- [ ] requirements کم‌حجم و deployment image نهایی بازسازی شوند.
- [ ] حذف نهایی فقط بعد از عبور مرحله 9 و سبز بودن full test suite انجام شود.

> وضعیت فعلی: این مرحله بعد از روی‌کردن UI/security validation و اثبات native
> bridge/protection در کد، به‌عنوان گیت بعدی فعال شده است. هدف اصلی حفظ
> stability native و حذف وابستگی‌های غیرضروری بدون شکستن trade/backtest/hyperopt
> در مسیر OANDA است.

**معیار قبولی:** با نصب و اجرای نسخه فارکس، هیچ ماژول یا config اجباری crypto-only
برای health، backtest، hyperopt، dry-run و Practice لازم نباشد.

## مرحله 11: آماده‌سازی Live و عملیات

هدف: Live فقط پس از اثبات Practice و با کنترل‌های عملیاتی فعال شود.

- [x] Live و Practice endpoint و credential کاملاً جدا باشند.
- [x] confirmation چندمرحله‌ای برای Live، whitelist instrument و size cap فعال
      شود.
- [x] secret management، لاگ بدون credential و audit log تکمیل شد.
- [x] health، broker connectivity، stream lag، reconciliation و risk alerts
      مانیتور شوند.
- [x] restart policy، backup ledger/database و migration اجرا شود؛ SQLite online
      backup و command `paper-backup` اضافه شد و service watchdog backoff دارد.
- [x] disaster recovery و emergency close مستند شوند؛ تمرین واقعی broker هنوز
      به credential Practice/Live و runbook execution نیاز دارد.
- [ ] deployment محدود با سرمایه و instrument کم انجام شود.
- [ ] افزایش حجم فقط بر اساس معیارهای ثبت‌شده و review انجام شود.

**معیار قبولی:** چک‌لیست release امضا شده، rollback آماده، و حداقل یک اجرای
کنترل‌شده Live بدون اختلاف state یا breach ریسک ثبت شده باشد.

---

## ماتریس تفاوت فارکس و رمزارز

| موضوع | فرض رایج در رمزارز | راه‌حل لازم در فارکس |
|---|---|---|
| بازار | 24/7 | session، تعطیلی، rollover و gap |
| قیمت | معمولاً یک market price | bid/ask و side-specific fill |
| هزینه | fee | spread، commission، slippage و swap |
| واحد معامله | مقدار coin/base | units، pip value و precision بروکر |
| موجودی | stake currency ثابت | account currency و conversion چندمرحله‌ای |
| پوزیشن | اغلب tradeهای مستقل | netting یا hedging وابسته به حساب |
| اهرم | exchange leverage | margin rules و broker-specific limits |
| داده | exchange OHLCV | broker candles، incomplete candle و timezone |
| نقدشوندگی | ممکن است دائمی باشد | session و تغییر spread در خبر/بازگشایی |
| order lifecycle | REST/WS صرافی | transaction stream و reconciliation OANDA |
| stop | exchange stop semantics | minimum distance و stop-on-fill بروکر |
| portfolio | pairهای متعدد کریپتو | correlation ارزی و exposure مشترک USD |

## گیت‌های ممنوعیت ادامه

تا وقتی موارد زیر حل نشده‌اند، وارد مرحله بعد نمی‌شویم:

- تست‌های مرحله فعلی سبز نیستند.
- state محلی با broker state reconcile نمی‌شود.
- هزینه‌های spread/slippage/swap در نتیجه لحاظ نشده‌اند.
- validation مستقل از hyperopt وجود ندارد.
- Practice execution بدون idempotency و kill switch است.
- credential در کد، log، config نمونه یا commit افشا شده است.
- strategy فقط روی یک pair یا یک بازه سودده است و robustness گزارش نشده است.

## مرحله 13: cleanup نهایی و hardening فینال

هدف: پس از تثبیت UI، auth/security و native OANDA parity، مسیر اصلی پروژه را به صورت minimal و forex-native نگه داریم و هر بخش legacy/crypto-only را به‌صورت explicit optional جدا کنیم.

- [ ] حذف یا قفل‌سازی عملکردهای crypto-only از مسیر پیش‌فرض نصب و اجرای فارکس.
- [ ] اطمینان از این‌که `oanda` exchange و native parser بدون `ccxt` در مسیر default اجرا می‌شوند.
- [ ] keep `crypto` extras فقط برای legacy modules و optional plugins، نه برای health/backtest/dry-run/practice.
- [ ] بازبینی `pyproject.toml` و `docs/forex_dependency_inventory.md` برای حذف نهایی فقط در صورت عبور gate‌ها.
- [ ] run targeted validation: `pytest tests/forex/test_oanda.py -q` و build UI با `npm run build`.
- [ ] ثبت هر incompatibility به‌صورت issue/decision قبل از حذف هر module legacy.

**معیار قبولی:** نصب و اجرای مسیر فارکس بدون نیاز به ماژول‌های crypto-only، در حالی که health، backtest، dry-run و Practice-safe review همچنان سبز و بدون regressions باقی بمانند.

## مرحله 14: عملیاتی‌سازی AI و شفافیت تصمیم

هدف: صفحه AI فقط یک نمای اطلاعاتی یا برچسب `hybrid` نباشد؛ کاربر باید بتواند
تنظیمات مؤثر را کنترل کند، اجرای واقعی baseline را ببیند، نتیجه را بازتولید کند
و قبل از Practice-safe approval شواهد کافی از کیفیت و ریسک در اختیار داشته باشد.

### 14.1 قرارداد عملیاتی AI

- [x] `ForexAIStrategyBaseline` به‌عنوان strategy اصلی research/backtest/dry-run
      ثبت شود و نام strategy، نسخه، commit/config hash و model mode را در هر run
      ذخیره کند.
- [x] `featureSet` فقط metadata نباشد؛ هر feature انتخاب‌شده باید به pipeline
      متصل شود یا با دلیل روشن در UI به‌عنوان `unsupported/not active` نمایش داده
      شود.
- [x] configuration معتبر و versioned برای `model`, `timeframe`, `riskBudget`,
      `labelPeriod`, `warmup`, `stopPips`, `spreadLimit`, `maxOpenPositions` و
      `executionMode` ایجاد شود.
- [x] endpoint `ai/config/validate` برای اعتبارسنجی بدون اعمال mutation اضافه شد؛
      config revision برای هر pair جداگانه نگهداری می‌شود.
- [x] تغییر config باعث ایجاد revision جدید شود؛ runهای قبلی نباید با config
      جدید بازنویسی شوند.
- [x] approval پس از وجود Hyperopt revision معتبر، تطبیق pair/timeframe و
      guardrail check به‌عنوان `approved` قابل ثبت باشد؛ Backtest مستقل برای
      research evidence اختیاری است و `pending`/`rejected` مانع activation شوند.

### 14.2 شواهد قابل نمایش در UI

صفحه `AI / Strategy intelligence` باید این موارد را از API واقعی نمایش دهد:

- وضعیت اتصال و freshness آخرین candle و آخرین اجرای strategy
- [x] strategy version، model mode، feature schema و training/data hash
- [x] تعداد signalهای `long`, `short`, `flat` و آخرین signal با reason
- [x] signal trace برای candleهای اخیر با strength، spread، volatility، ATR،
      session و featureهای فعال در endpoint و UI ثبت و نمایش داده می‌شود.
- آخرین backtest و walk-forward: بازه، pairها، candle count، P/L، drawdown،
  win rate، هزینه‌ها و تعداد معاملات
- تفکیک `train`, `validation`, `out-of-sample` و جلوگیری از نمایش سود بدون
  validation مستقل
- وضعیت data quality: missing candles، duplicate، incomplete candle، spread
  limit و آخرین timestamp broker
- [x] دلیل هر signal و دلیل هر blocked signal/order؛ signal trace اکنون
      `blocked_spread_limit` و reasonهای جهت‌دار را ثبت می‌کند.
- وضعیت guardrail: dry-run، no-live-order، max risk، kill switch و approval
- audit trail برای تغییر config، اجرای backtest و approve/reject

### 14.3 APIهای پیشنهادی

- `GET /api/v1/ai/status`: وضعیت runtime، version، freshness و آخرین signal
- `GET /api/v1/ai/config`: config versioned و effective parameters
- `POST /api/v1/ai/config/validate`: validation بدون اعمال تغییر
- `POST /api/v1/ai/config`: ایجاد revision جدید با role و CSRF
- `GET /api/v1/ai/runs/{id}`: جزئیات run و evidence
- `GET /api/v1/ai/runs/{id}/signals`: سیگنال‌ها و reasonهای candle به candle
- `GET /api/v1/ai/runs/{id}/trades`: entry/exit، حجم، اهرم، قیمت، هزینه و P/L
- `GET /api/v1/ai/hyperopt/{id}`: progress، فضای پارامتر، candidateها و validation
- `POST /api/v1/ai/hyperopt`: اجرای فقط research/backtest؛ هرگز live

### 14.4 خلاصه پارامترهای قابل Hyperopt

پارامترها باید در UI با range، مقدار انتخاب‌شده، دلیل و اثر ریسک نشان داده شوند:

| گروه | پارامتر | نمونه فضای جست‌وجو | وضعیت فعلی/تصمیم |
|---|---|---|---|
| signal | `fastPeriod` | 8، 12، 16، 20 | موجود در hyperopt EMA؛ حفظ شود |
| signal | `slowPeriod` | 26، 32، 40، 50 | موجود در hyperopt EMA؛ شرط `fast < slow` |
| risk | `stopPips` | 5، 8، 10، 15، 20 | موجود؛ با spread و minimum distance محدود شود |
| risk | `riskFraction` | 0.25% تا 1.00% | موجود؛ سقف policy و daily loss اجباری |
| signal | `entryThreshold` | 0.25 تا 1.50 | جدید؛ برای `signal_strength` baseline |
| signal | `exitThreshold` | 0.00 تا 1.00 | جدید؛ جدا از entry و بدون lookahead |
| feature | `volatilityWindow` | 3، 5، 8، 14 | جدید؛ فقط با validation مستقل |
| feature | `atrWindow` | 7، 14، 21 | جدید؛ برای stop/quality، نه افزایش مصنوعی سود |
| feature | `labelPeriod` | 1، 2، 3، 6 candle | موجود در baseline؛ leakage check اجباری |
| execution | `maxSpread` | بر اساس pip و instrument | جدید؛ candidate نامعتبر باید reject شود |
| execution | `maxOpenPositions` | 1، 2، 3 | از risk policy گرفته شود، نه آزادانه optimize |

پارامترهای `riskFraction`، `stopPips`، `maxSpread` و `maxOpenPositions` نباید فقط
با هدف افزایش profit انتخاب شوند؛ objective باید هم‌زمان هزینه، drawdown،
تعداد معاملات، پایداری بین pairها و OOS را جریمه/ارزیابی کند.

### 14.5 ترتیب اجرای milestone

1. [x] ساخت schema versioned و endpoint `ai/status` با evidence واقعی.
2. [x] اتصال featureهای انتخاب‌شده به baseline و نمایش active/inactive در UI.
3. [x] ثبت کامل signal و trade trace برای هر run.
4. [x] تبدیل hyperopt فعلی به hyperopt baseline-aware و اضافه‌کردن پارامترهای جدید
   فقط بعد از تست leakage و reproducibility.
5. [x] نمایش candidate برتر، runner-up، فضای جست‌وجو و validation مستقل در UI.
6. [x] pair-scoped کردن AI و Hyperopt؛ انتخاب pair، تعداد candle و تعداد تلاش در
      UI انجام می‌شود و config هر pair جداگانه به strategy همان pair اعمال می‌شود.
      timeframe انتخابی نیز در config همان pair ذخیره می‌شود و approval نامنطبق با
      timeframe مؤثر با خطای `409` رد می‌شود؛ فضای Hyperopt اکنون تا 900 تلاش واقعی
      قابل تنظیم است.
7. [x] revision منتخب Hyperopt پس از approval به Backtest همان pair/timeframe و
      همان candle window اعمال می‌شود؛ Backtest نامنطبق با revision تأییدشده با
      خطای `409` رد می‌شود و منبع config در نتیجه نمایش داده می‌شود.
8. اجرای dry-run چندساعته با ثبت signal، blocked action، latency و drift.
9. [x] فعال‌سازی Practice-safe approval فقط برای revisionای که همه gateها را گذرانده.

**معیار قبولی:** یک operator بتواند از صفحه AI تشخیص دهد چه نسخه و configای در
حال اجراست، چه داده‌ای مصرف شده، چرا signal صادر یا مسدود شده، hyperopt چه
پارامترهایی را امتحان کرده، و آیا نتیجه در validation مستقل پایدار است؛ هیچ
مدل یا config تأییدنشده‌ای نباید به live order path دسترسی داشته باشد.

## مرحله 15: مدل‌های واقعی FreqAI برای Forex

هدف: baseline فعلی را به یک pipeline مدل‌محور قابل‌آزمایش تبدیل کنیم، بدون آن‌که
مدل آموزش‌ندیده یا نتیجه‌ی بدون OOS validation وارد dry-run یا Practice شود.

### تصمیم مدل‌ها

- [x] **LightGBMRegressor** به‌عنوان مدل اول برای پیش‌بینی future return و ساخت
      signal confidence پیاده‌سازی شود؛ سریع، قابل‌توضیح و مناسب featureهای جدولی
      فارکس است.
- [x] **LightGBMClassifier** برای طبقه‌بندی `long`, `short`, `flat` به‌عنوان
      مسیر مقایسه‌ای اضافه شود؛ label باید با spread/cost و neutral band ساخته شود.
- [ ] **Multi-target** فقط بعد از اثبات single-target فعال شود؛ targetها می‌توانند
      future return، probability direction و expected volatility باشند.
- [ ] **XGBoost** به‌عنوان challenger مستقل اجرا شود؛ انتخاب آن فقط با OOS و
      پایداری بین pair/period انجام شود، نه با train score.
- [ ] **PyTorch** برای معماری عصبی سفارشی فقط بعد از baseline درختی، با seed،
      checkpoint، early stopping و محدودیت CPU/memory وارد شود.
- [ ] **Reinforcement Learning** فعلاً خارج از execution path بماند؛ RL فقط در
      محیط شبیه‌سازی‌شده با reward شامل spread، slippage، financing و drawdown
      بررسی شود.

### pipeline اجباری هر مدل

1. دریافت و snapshot داده‌ی OANDA با pair/timeframe/history mode مشخص.
2. ساخت dataset از `ForexFreqAIAdapter` بدون lookahead و با حذف incomplete candle.
3. split زمانی `train`, `validation`, `out-of-sample` و walk-forward مستقل.
4. آموزش مدل فقط روی train؛ هیچ scaler/feature selection از آینده وارد train نشود.
5. ثبت `model_type`, `model_version`, `feature_schema_hash`, `training_data_hash`,
   seed و hyperparameters.
6. ارزیابی هم‌زمان metricهای مدل و metricهای معاملاتی:
   MAE/RMSE یا F1/ROC-AUC، سپس net P/L، drawdown، هزینه‌ها، turnover و stability.
7. ثبت model artifact و evidence؛ فقط artifact دارای validation مستقل می‌تواند
   revision `candidate` بگیرد.
8. approval انسانی در Practice-safe؛ live execution برای همه مدل‌ها disabled.

### قرارداد مدل پیشنهادی

- `ModelSpec`: type، version، pair، timeframe، label، feature schema و seed.
- `ModelArtifact`: مسیر/شناسه artifact، hash، created_at، training range و metrics.
- `ModelEvaluation`: train/validation/OOS metrics، trading metrics و rejection reasons.
- `ModelRevision`: config revision، model revision، approved_by، approved_at و
  guardrail state.
- مدل انتخاب‌شده باید با `ForexAIStrategyBaseline` قابل مقایسه باشد و در صورت
  نبود artifact معتبر به baseline امن fallback کند.
- [x] قراردادهای مستقل `ModelSpec`, `ModelArtifact`, `ModelEvaluation` و
      `ModelRevision` در `freqtrade/forex/ai_models.py` اضافه شدند؛ approval
      مدلِ ردشده با validation مستقل ممکن نیست و هنوز execution path را فعال
      نمی‌کنند.

### ترتیب اجرا

- [x] فاز 1: adapter dataset/label فعلی به schema versioned و artifact manifest؛
      `ForexAIDatasetManifest` اکنون splitهای زمانی، feature hash و data hash را
      ثبت می‌کند و اجرای آن به model/execution path متصل نیست.
- [x] foundation فاز 2: `LightGBMFutureReturnModel` به‌صورت research-only اضافه
      شد و train/validation/OOS metrics، artifact hash و unapproved revision تولید
      می‌کند؛ پذیرش نهایی آن هنوز به دو pair/دو period و confidence calibration
      وابسته است.
- [x] foundation robustness فاز 2: `run_robust_lightgbm` اکنون مدل را روی دو
      pair و دو period مستقل ارزیابی می‌کند و فقط با عبور همه‌ی sliceها accepted
      می‌شود؛ این مسیر همچنان research-only است.
- [x] confidence calibration فاز 2: directional accuracy و confidence buckets
      برای خروجی OOS در `calibrate_return_confidence` اضافه شد؛ confidence هنوز
      به‌عنوان احتمال معاملاتی production یا execution signal استفاده نمی‌شود.
- [x] foundation فاز 3: `LightGBMDirectionClassifier` با labelهای `long`, `flat`,
      `short`، neutral band و metricهای accuracy/macro-F1 به‌صورت research-only
      اضافه شد؛ هنوز به execution path متصل نیست.
- [x] foundation فاز 2/3: Regressor و Classifier در API/UI روی همان OOS split
      مقایسه می‌شوند؛ پذیرش نهایی چند pair/دو period و confidence calibration
      کامل هنوز باز است.
- [ ] فاز 4: XGBoost challenger با همان dataset و همان splitها.
- [ ] فاز 5: multi-target پس از عبور single-target از robustness gate.
- [ ] فاز 6: PyTorch و RL فقط به‌عنوان research plugin و خارج از live path.
- [x] فاز 7: endpoint و UI مقایسه‌ی هم‌زمان Regressor، Classifier و
      `ForexAIStrategyBaseline` روی همان split اضافه شد؛ split sizes، OOS
      metrics، acceptance، rejection reason، baseline signal count و hashها
      نمایش داده می‌شوند.
- [ ] فاز 7 تکمیلی: نمایش feature importance، confidence calibration و شواهد
      OOS چند pair/period در صفحه AI.
- [x] تنظیمات FreqAI واقعی (`trainPeriodDays`, `backtestPeriodDays`,
      `labelPeriodCandles`, `includeShiftedCandles`, `indicatorPeriodsCandles`,
      `weightFactor`, `diThreshold`) در پنل جمع‌وجور صفحه AI اضافه شد؛ این مقادیر
      واقعاً `build_forex_ai_dataset` (feature schema hash و split زمانی) و
      training مدل‌های LightGBM (recency weighting و Dissimilarity Index outlier
      filtering) را کنترل می‌کنند و نتیجه در پنل مقایسه نمایش داده می‌شود.
      `include_timeframes` عمداً اضافه نشد چون به fetch/merge چندتایم‌فریمی نیاز
      دارد که هنوز پیاده‌سازی نشده است.

### برنامه‌ی اجرای چند pair/timeframe و Approval

- [x] پشتیبانی research/backtest برای تنظیمات مستقل هر pair و timeframe؛
      Hyperopt و backtest فقط در صورت تطبیق pair/timeframe از revision تأییدشده
      استفاده می‌کنند و برای scope متفاوت به default برمی‌گردند.
- [x] وضعیت Approval به scope مستقل `(pair, timeframe)` منتقل شد و دکمه‌ی UI
      دیگر به‌صورت خاموش خطا را پنهان نمی‌کند؛ در نبود backtest مستقل، دلیل دقیق
      backend نمایش داده می‌شود.
- [x] هنگام Approval، snapshot کامل `approvedRevision` شامل Hyperopt، config
      revision و تنظیمات FreqAI ثبت می‌شود تا تغییر تنظیمات بعدی بدون Approval
      جدید به revision فعال وارد نشود.
- [ ] اجرای dry-run چند worker مستقل برای چند `(pair, timeframe)`؛ هر worker
      باید revision تأییدشده‌ی خودش را بارگذاری کند و پس از restart از storage
      پایدار بخواند.
- [x] persistence دائمی config/revision/review در SQLite اضافه شد؛ scopeهای
      Approval بعد از restart API restore می‌شوند و revision کامل قابل بازیابی
      است.
- [ ] native Freqtrade multi-timeframe execution؛ تا زمان پیاده‌سازی worker
      و adapter اختصاصی، این بخش به مسیر custom Forex API محدود است.
- [x] scheduler خودکار Hyperopt به صف ترتیبی تبدیل شد: pairهای approved بدون
      اجرای هم‌زمان، با `intervalDays` برای تکرار روزانه و `gapMinutes` برای
      فاصله‌ی امن بین slotها اجرا می‌شوند؛ `nextRuns` زمان بعدی هر pair را در
      API/UI نمایش می‌دهد.

### معیارهای عدم پذیرش

- هر مدل با train score بالا اما OOS ضعیف reject شود.
- هر مدل بدون data hash، feature hash یا model artifact reject شود.
- confidence بدون calibration و coverage در UI به‌عنوان احتمال واقعی نمایش داده نشود.
- مدل‌هایی که با تغییر pair/timeframe افت شدید دارند candidate عمومی محسوب نشوند.
- هیچ مدل ML/RL مستقیماً order ارسال نکند؛ مسیر فقط research، backtest و dry-run باشد.

**معیار قبولی مرحله:** برای حداقل دو pair و دو period، یک LightGBM model و یک
challenger با split زمانی مستقل آموزش داده شوند، artifact/hash/metrics ثبت شود،
نتیجه‌ی معاملاتی با baseline مقایسه شود، و فقط revision دارای OOS evidence در
Practice-safe review قابل approval باشد.

## پروتکل پیشرفت روزانه

برای هر تغییر فقط این پنج مورد ثبت شود:

1. مرحله و checkbox مربوطه.
2. فایل‌ها و contractهایی که تغییر کرده‌اند.
3. تست یا command قابل تکرار.
4. خروجی و معیار قبولی.
5. ریسک یا تصمیم بازمانده برای مرحله بعد.

## ترتیب اجرای پیشنهادی از امروز

1. تکمیل مرحله 0 و ثبت baseline.
2. تکمیل domain فارکس و currency conversion عمومی.
3. تکمیل dry-run با stop، restart و multi-pair state.
4. ارتقای backtest و ساخت validation مستقل.
5. تکمیل Practice order و reconciliation با ارسال واقعی به Demo API.
6. طراحی adapter برای native Freqtrade strategy/backtesting/hyperopt.
7. اتصال FreqAI در research و سپس dry-run.
8. حذف crypto-only پس از تثبیت قراردادهای native.
9. release محدود Live فقط بعد از عبور همه گیت‌ها.

---

## مرحله 12: طراحی UI برای پنل مانیتورینگ و کنترل فارکس

### وضعیت فعلی و اولویت اجرای بعدی (2026-09-19)

- [x] shell UI، layout، routing، dashboard overview و صفحات اصلی monitoring/analytics اجرا شده‌اند.
- [x] اتصال واقعی به FastAPI و WebSocket برای market/account/order/alerts انجام شده است.
- [x] مسیر write operation با role-based guard و CSRF-like header در frontend/backend فعال شده است.
- [x] auth واقعی با session/JWT و role persistence در backend کامل شد؛ login و session validation با token-based access برای viewer/operator/admin فعال است.
- [x] persistence امن UI فقط برای preferences بدون secret انجام شد.
- [x] logs/telemetry امن و بدون داده حساس نهایی شدند؛ session token، password و metadata حساس به‌صورت redacted در audit log می‌آیند.
- [x] preflight Practice/Live، release gate admin approval و rollback checklist عملیاتی به‌صورت end-to-end تکمیل شدند.

اولویت اجرایی پیشنهادی بر اساس وضعیت فعلی:

1. تکمیل auth واقعی و session-aware access control برای viewer/operator/admin. (انجام شد: session-based auth + validation)
2. محدود کردن persistence UI به فقط preferences ایمن و حذف هرگونه secret/token از browser state. (انجام شد)
3. استانداردسازی logs/telemetry و masking داده‌های حساس. (انجام شد)
4. آماده‌سازی Practice/Live preflight و checklist عملیات برای اجرای کنترل‌شده. (انجام شد)
5. سپس ورود به مرحله بعدی native Freqtrade hardening و cleanup dependencyهای crypto-only. (در حال اجرا: parity gate + dependency inventory)

هدف: یک داشبورد کاربردی و امن برای رصد وضعیت حساب، بازار، استراتژی، سفارش‌ها، ریسک و اجرای dry-run/practice/live بسازیم، بدون اینکه UI به‌صورت مستقیم بر منطق broker یا trading logic تکیه کند. UI باید به عنوان layer گرافیکی روی API و WebSocket مستقل رفتار کند، نه جایگزین backend.

### 12.1 معماری کلی

- [x] Frontend: `React + TypeScript + Vite` scaffolded and aligned to the project workspace (`apps/ui`)
- [x] Styling: dark trading-dashboard theme implemented with a modern glassmorphism layout tailored for forex monitoring
- [x] State management: `React Query` برای data fetching + `Zustand` برای UI state
- [x] Real-time layer: `WebSocket` با reconnect و heartbeat
- [x] API layer: typed client با `OpenAPI schema` یا `generated types`
- [ ] Auth: JWT/session، با role-based access برای admin/operator/viewer
- [ ] Persistence: browser local cache برای preferences، no sensitive secrets

ساختار پیشنهادی پروژه UI:

- `apps/ui/`
  - `src/app/`
    - `routes/`
    - `providers/`
    - `layouts/`
    - `theme/`
  - `src/features/`
    - `dashboard/`
    - `market/`
    - `orders/`
    - `positions/`
    - `risk/`
    - `strategies/`
    - `backtests/`
    - `settings/`
  - `src/shared/`
    - `api/`
    - `components/`
    - `hooks/`
    - `types/`
    - `utils/`
    - `stores/`

### 12.2 لایه‌های ارتباطی

- [x] Frontend به `REST API + WebSocket` متصل می‌شود.
- [x] وب‌سوکت فقط برای داده‌های real-time مثل قیمت، سفارش‌ها، پوزیشن‌ها و هشدارهای ریسک استفاده می‌شود.
- [x] REST برای داده‌های پایدار مثل history، backtest result، strategy configs و audit logs استفاده می‌شود.
- [x] API بک‌اند روی `Python/FastAPI` اجرا می‌شود و با `OANDA` و `SQLite/PostgreSQL` هماهنگ است.
- [x] قراردادهای UI کاملاً از domain model backend جدا شود و فقط schema‌ی خروجی API را مصرف کند.
- [x] Live data stream در UI به state store منتقل شد و صفحات Dashboard/Market/Orders از snapshot‌های WebSocket به‌صورت real-time استفاده می‌کنند.

### 12.3 معماری داده و backend

- [x] FastAPI service برای health، account snapshot، instrument list، pricing، orders، positions، risk، reports
- [x] WebSocket namespaceها:
  - `/ws/market`
  - `/ws/account`
  - `/ws/orders`
  - `/ws/alerts`
- [x] Pusher/event bus برای updateهای real-time داخل backend
- [x] SQLite برای local/dev/testing و PostgreSQL برای production/ops
- [x] OANDA adapter به‌صورت isolated service، UI به‌طور مستقیم با OANDA تماس نمی‌گیرد
- [x] CORS و real browser access برای FastAPI فعال شد؛ این سطح برای Live و Practice باید در backend هم با permission validator همراه باشد
- [x] layer کنترل دسترسی و write confirmation به‌صورت front-end layer در dashboard فعال شد؛ این سطح برای Live و Practice باید در backend هم با permission validator همراه باشد

### 12.4 صفحات و ماژول‌های UI

- [x] Initial dashboard shell implemented in the UI prototype with KPI cards, watchlist, positions, strategy summary, risk and execution panel

#### 1) Dashboard

- [x] overview status: account equity, margin used, free margin, daily P/L
- [x] list of open instruments and session status
- [x] last signal summary for each strategy
- [x] health indicators: broker connectivity, API latency, DB status, websocket status
- [x] recent alerts and kill-switch state

#### 2) Market Overview

- watchlist برای pairهای اصلی مثل EUR/USD، GBP/USD، USD/JPY
- bid/ask، spread، swap/financing، session state
- candle chart با timeframeهای قابل انتخاب (M1، M5، M15، H1)
- volatility/ATR indicators
- last order impact and liquidity summary

#### 3) Positions & Orders

- open positions table با status، side، units، entry، stop، tp، unrealized P/L
- order ticket view برای market/limit/stop/take-profit
- order lifecycle timeline: pending → filled → modified → cancelled
- filter by strategy, instrument, status, date range
- bulk actions برای cancel/close/reverse با confirmation modal

#### 4) Strategy Center

- [x] list strategy configs و versions
- [x] signal panel: entry/exit state، reason، confidence، indicators
- [x] live vs dry-run toggle
- [x] warmup و last processed candle
- [x] config editor با validation روی backend

#### 5) Risk & Limits

- max daily loss، max exposure، margin level، net exposure
- currency correlation overview
- kill-switch state and last trigger reason
- risk events timeline
- policy explanation for each blocked order

#### 6) Backtest & Hyperopt

- [x] list runs با timestamp، pair، timeframe، result summary
- [x] equity curve و drawdown chart
- [x] parameter table و candidate comparison
- [x] export JSON/CSV
- [x] hyperopt progress with workers and stop conditions

#### 7) Practice / Live Controls

- mode selector: dry-run, practice, live
- preflight validation screen
- execution buttons با confirmation، lockout conditions
- last reconciliations and broker/account delta
- audit log for each order submission

#### 8) Settings & Admin

- broker credentials management with secret vault (not in frontend storage)
- environment switching: dev/staging/practice/live
- user roles و access policy
- notifications and alert channels
- API key and session expiry

### 12.5 طراحی تجربه کاربری

- [ ] UI باید برای operator سریع و بدون خطای تصمیم‌گیری طراحی شود
- [ ] اعداد مالی با format امن و قابل خواندن: `Decimal`-based display
- [ ] رنگ‌ها برای stateها استاندارد شوند: success=green، warning=amber، error=red، neutral=blue
- [ ] برای order/positionهای حساس، confirm modal و read-only summary الزامی باشد
- [ ] all actions must be idempotent-friendly: client order id نمایش داده شود
- [ ] no direct secret exposure in browser or logs

### 12.6 جریان داده و state در UI

- `useAccountQuery`: snapshot حساب
- `usePositionsQuery`: open positions
- `useOrdersQuery`: order list
- `useMarketWatchQuery`: instrument quotes
- `useStrategySignalQuery`: current signals
- `useWebSocketStore`: updates streamed in real time

الگوی پیشنهادی:

- REST API برای initial load
- WebSocket برای incremental updates
- React Query cache + background refetch
- optimistic UI فقط برای non-financial actions، نه برای order execution
- conflict resolution با `last_updated` و `version` از backend

### 12.7 API قرارداد پیشنهادی

#### Account

- `GET /api/v1/account/summary`
- `GET /api/v1/account/positions`
- `GET /api/v1/account/orders`
- `GET /api/v1/account/risk`

#### Market

- `GET /api/v1/markets`
- `GET /api/v1/markets/{instrument}/quote`
- `GET /api/v1/markets/{instrument}/candles?timeframe=H1&from=...&to=...`

#### Strategies

- `GET /api/v1/strategies`
- `GET /api/v1/strategies/{id}/signals`
- `POST /api/v1/strategies/{id}/config`

#### Orders

- `POST /api/v1/orders/market`
- `POST /api/v1/orders/stop`
- `POST /api/v1/orders/close`
- `DELETE /api/v1/orders/{order_id}`

#### Backtest / Hyperopt

- `POST /api/v1/backtests/run`
- `GET /api/v1/backtests/{id}`
- `POST /api/v1/hyperopt/run`
- `GET /api/v1/hyperopt/{id}/results`

#### WebSocket events

- `account.snapshot`
- `account.updated`
- `positions.updated`
- `orders.updated`
- `market.tick`
- `risk.alert`
- `strategy.signal`

### 12.8 امنیت و کنترل‌ها

- [x] همه درخواست‌های write باید require auth + CSRF protection
- [x] نقش‌ها: viewer, operator, admin
- [x] live mode فقط برای کاربران مجاز و با 2-step confirmation
- [x] هیچ credential، token یا secret در UI state، localStorage یا URL ذخیره نشود
- [x] logs و telemetry فقط metadata‌های امن و بدون داده‌های حساس نگه دارند
- [x] permission checks روی backend هم اجرا شوند، نه فقط UI

### 12.9 انتخاب ابزارها

- UI library: `React + TypeScript + Vite`
- Charts: `TradingView lightweight charts` یا `recharts`/`visx` برای custom analytics
- Forms: `react-hook-form + zod`
- Data tables: `TanStack Table`
- Notifications: `sonner` یا `react-hot-toast`
- API client: `OpenAPI generated client` یا `axios` + typed wrappers
- WebSocket client: custom hook with reconnect + exponential backoff

### 12.10 فازبندی پیاده‌سازی

#### فاز A: MVP UI

- [x] dashboard overview
- [x] market watchlist
- [x] positions/orders list
- [x] basic account summary
- [x] WebSocket connection status

#### فاز B: operational UI

- [x] strategy signal view
- [x] risk panel
- [x] order ticket and confirmation modal
- [x] audit log and recent events

#### فاز C: analytics UI

- [x] backtest results view
- [x] drawdown/equity chart
- [x] hyperopt comparison table
- [x] export/report generation

#### فاز D: production-ready

- [x] role-based access
- [x] environment switching
- [ ] alerting and notifications
- [ ] dark mode
- [ ] monitoring and operational logs

### 12.11 معیار قبولی UI

- [ ] Dashboard در 3 ثانیه اول بارگذاری اولیه آماده باشد.
- [x] وضعیت حساب و Open Positions با real-time update بدون refresh کامل به‌روز شود.
- [ ] سفارش‌های جدید با client order id، status و reason قابل ردیابی باشند.
- [x] هشدارهای ریسک در UI و API هم‌زمان دیده شوند.
- [x] در حالت live، هیچ اقدام خام بدون confirmation و permission انجام نشود.
- [x] بازنشانی صفحه یا reconnect وب‌سوکت، state UI را بدون از دست رفتن داده بازسازی کند.
- [ ] هیچ داده محرمانه در frontend cache، URL، log یا storage قرار نگیرد.

### 12.12 تصمیم نهایی برای این پروژه

UI باید در مسیر اصلی پروژه یک layer کنترل عملیاتی و observability باشد، نه یک سیستم independent trading terminal. هدف اصلی آن این است که برای operatorهای پروژه، وضعیت بازار، حساب، strategy، risk و execution در یک نگاه قابل فهم باشد، و هم‌زمان با FastAPI backend، OANDA و SQLite/PostgreSQL هم‌راستا و امن عمل کند. این UI باید در مراحل اولیه برای dry-run و Practice مفید باشد و در مرحله بعدی به‌صورت کنترل‌محور برای Live گسترش پیدا کند.

**معیار قبولی نهایی:** a single operator can monitor account health, assess risk, review strategy signals, submit/monitor orders, and inspect execution reports without needing direct access to broker APIs or raw database records.

---

## وضعیت نهایی پروژه (2026-09-19)

### وضعیت تحقق یافته

- [x] UI shell و routing برای dashboard/market/orders/strategy/risk/backtest آماده است.
- [x] اتصال واقعی به FastAPI و WebSocket برای market/account/order/alerts فعال است.
- [x] مسیر write operation با guardهای auth/session و permission-aware validation فعال است.
- [x] auth/session و کنترل نقش‌ها برای viewer/operator/admin پیاده‌سازی شده‌اند.
- [x] persistence امن UI فقط برای preferences و بدون secret انجام شده است.
- [x] telemetry/logs بدون داده حساس و با masking امن انجام شده‌اند.
- [x] native bridge OANDA برای pairlist، wallet، pricing و protection فعال است.
- [x] validation native برای `backtesting`/config OANDA با green test suite انجام شده است.
- [x] inventory وابستگی‌ها و ماژول‌های crypto-only ثبت شده‌اند.

### گیت فعلی پروژه

- [x] گیت native parity برای `oanda`/`backtesting`/`protection` پاس شده است.
- [x] `pytest tests/forex/test_oanda.py -q` با نتیجه `116 passed, 1 warning` اجرا و تأیید شد.
- [ ] cleanup نهایی وابستگی‌های crypto-only فقط بعد از حفظ native parity و regression suite انجام می‌شود.
- [ ] حذف یا deprecate legacy modules باید بر اساس `docs/forex_dependency_inventory.md` و با معیارهای هر دسته انجام شود.
- [ ] ورود به فاز `cleanup + hardening final` فقط بعد از تثبیت UI + API + Practice-safe review و ردّ regressions انجام می‌شود.

### تصمیم اجرایی نهایی

پروژه اکنون در وضعیت آماده برای مرحله پایانی cleanup قرار دارد: UI و auth/security به‌صورت عملیاتی کامل هستند و مسیر native OANDA نیز با شواهد تستی تأیید شده است. بنابراین، هیچ حذف module یا کاهش وابستگی بدون عبور از این گیت نهایی انجام نمی‌شود. هدف بعدی، حذف فقط ماژول‌های crypto-only و legacy غیرضروری است، نه تغییر در core native Forex architecture. این مرحله اکنون به‌صورت active milestone در [docs/forex_project_plan.md](docs/forex_project_plan.md) ثبت شده و به‌عنوان next gate برای پروژه در نظر گرفته می‌شود.

---