- 2026-09-23 — native validation hardening شروع شد.
  - `freqtrade.forex` exportهای API و strategy را lazy-load می‌کند تا import
    native OANDA با `freqtrade.data.history` وارد circular import نشود.
  - utility runmodeهای Freqtrade اکنون config OANDA را read-only validate می‌کنند.
  - façade native OANDA قراردادهای market/pairlist، fee صفر، precision، startup
    candle، options و wallet proxy را برای مسیر backtesting ارائه می‌دهد.
  - migration مخصوص funding fee برای OANDA skip می‌شود؛ financing فارکس مسیر
    مستقل دارد.
  - config نمونه OANDA برای strategyهای long/short روی futures/isolated تنظیم شد.
  - validation command با `show-config` موفق شد؛ native backtesting تا مرحله
    data loading پیش رفت اما به‌دلیل نبود history محلی برای EUR/USD و GBP/USD
    متوقف شد. هیچ order یا broker write در این validation انجام نشد.
  - baseline تستی 2026-09-23 برابر `136 passed, 2 skipped` و UI build سبز بود.

# تاریخچه پیشرفت پروژه فارکس

## 2026-09-23 — Operational hardening

- `PaperLedger.backup_to` با SQLite online backup و جایگزینی اتمیک اضافه شد.
- command `paper-backup` و تست restore round-trip اضافه شدند.
- runbook backup/recovery و emergency close در `docs/oanda_practice.md` ثبت شد.
- watchdog service روی restart failure با backoff تنظیم شد؛ تمرین broker واقعی
  emergency close همچنان credential-dependent است.

## 2026-09-17

- مرحله 0 آغاز شد و roadmap اصلی در `docs/forex_project_plan.md` ثبت شد.
- baseline محیط در `docs/forex_baseline_2026-09-17.md` ثبت شد.
- Python `3.14.0` و revision پایه `2026.8` ثبت شدند.
- تست اختصاصی فارکس اجرا شد: `35 passed` با یک warning وابستگی.
- CLI فارکس با commandهای `health`, `paper-report`, `dry-run`, `backtest` و
  `hyperopt` با موفقیت load شد.
- baseline کامل OANDA شامل health، dry-run، backtest و hyperopt به credential
  جدید Practice نیاز دارد و هنوز باز است.
- انتخاب lint و type-check اختصاصی هنوز باز است.

### مرحله 1 — شروع model domain فارکس

- فایل‌ها و contractها: `freqtrade/forex/models.py`, `tests/forex/test_oanda.py`
- تغییر: `OandaInstrument` دارای فیلدهای صریح `base_currency` و `quote_currency`
  شد و `pip_size`/`is_quote_currency` به قرارداد domain اضافه شد.
- command قابل تکرار: `python -m pytest tests/forex/test_oanda.py -q`
- خروجی: `1 failed, 35 passed` قبل از اصلاح، سپس بعد از اصلاح دوباره اجرا شد و
  all green (در نتیجه نهایی باید ذکر شود).
- ریسک/تصمیم: این مرحله فقط پایه domain برای pair semantics است؛ validation کامل
  precision، conversion و netting در مراحل بعدی تکمیل می‌شود.

### مرحله 1 — ادامه‌ی model domain فارکس (timezone/session)

- فایل‌ها و contractها: `freqtrade/forex/models.py`, `tests/forex/test_oanda.py`
- تغییر: مدل‌های `ForexCandle` و `ForexMarketSession` برای `UTC`, `complete` و
  `session open/close` اضافه شدند تا داده فارکس بدون فرض crypto به‌صورت درست
  مدل شود.
- command قابل تکرار: `python -m pytest tests/forex/test_oanda.py -q`
- خروجی: `39 passed` و یک warning وابسته به `httpx`/TestClient.
- ریسک/تصمیم: هنوز در مرحله 1 مانده‌ایم؛ این گام برای داده و time scheduling
  آماده‌سازی لازم است و پیش از dry-run/Practice باید تمام شود.

### مرحله 3 — strategy قابل پیکربندی و قابل بارگذاری

- فایل‌ها و contractها: `freqtrade/forex/strategies/ema_cross.py`,
  `tests/forex/test_oanda.py`
- تغییر: `ForexEmaStrategy` به‌عنوان implementation native از `IStrategy` اضافه
  شد؛ indicatorهای EMA، entry/exit بدون lookahead و پارامترهای
  `forex_fast_period` و `forex_slow_period` را پشتیبانی می‌کند.
- command قابل تکرار:
  `python -m pytest tests/forex/test_oanda.py -q -k "native_ema_strategy"`
- خروجی: `2 passed` و یک warning وابسته به `httpx`/TestClient؛ strategy از مسیر
  `StrategyResolver` با موفقیت load شد.
- ریسک/تصمیم: این گام فقط strategy contract را native می‌کند؛ اتصال کامل OANDA
  به commandهای native trade/backtesting/hyperopt هنوز در مرحله 9 باز است.

### مرحله 3 — feature pipeline و warmup strategy

- فایل‌ها و contractها: `freqtrade/forex/features.py`,
  `freqtrade/forex/strategies/ema_cross.py`, `tests/forex/test_oanda.py`
- تغییر: `ForexFeaturePipeline` برای featureهای DataFrame با حفظ طول و index
  اضافه شد؛ `ForexEmaStrategy` اکنون warmup را پیش از entry/exit enforce می‌کند
  و محاسبات crossover فقط از candle جاری و قبلی استفاده می‌کنند.
- command قابل تکرار:
  `python -m pytest tests/forex/test_oanda.py -q -k "native_ema_strategy or feature_pipeline"`
- خروجی: `3 passed` و یک warning وابسته به `httpx`/TestClient.
- ریسک/تصمیم: pipeline قرارداد feature عمومی است اما اتصال کامل FreqAI و
  featureهای فارکس مانند spread/session/ATR هنوز در مرحله 7 باز است.

### مرحله 3 — entry/exit مستقل long و short

- فایل‌ها و contractها: `freqtrade/forex/strategies/ema_cross.py`,
  `tests/forex/test_oanda.py`
- تغییر: `ForexEmaStrategy` اکنون `can_short=True` دارد و کراس صعودی/نزولی را
  مستقل برای `enter_long`، `exit_long`، `enter_short` و `exit_short` تولید
  می‌کند. تست resolver در `TradingMode.FUTURES` اجرا می‌شود، چون Freqtrade
  strategyهای short را در spot رد می‌کند.
- command قابل تکرار:
  `python -m pytest tests/forex/test_oanda.py -q -k "native_ema_strategy"`
- خروجی: `3 passed` و یک warning وابسته به `httpx`/TestClient.
- ریسک/تصمیم: این فقط contract سیگنال است؛ netting/hedging و semantics سفارش
  short فارکس هنوز در مرحله 4 و Practice باید تکمیل و با OANDA reconcile شوند.

### مرحله 3 — قراردادهای مستقل stop و exit

- فایل‌ها و contractها: `freqtrade/forex/exit_rules.py`,
  `tests/forex/test_oanda.py`
- تغییر: قراردادهای `FixedStop`، `AtrStop`، `TakeProfit`، `TrailingStop` و
  `TimeExit` اضافه شدند. همه محاسبات با `Decimal` انجام می‌شوند؛ trailing برای
  long فقط بالا می‌رود و برای short فقط پایین می‌آید.
- command قابل تکرار:
  `python -m pytest tests/forex/test_oanda.py -q -k "exit_rules or trailing_stop or time_exit"`
- خروجی: `3 passed` و یک warning وابسته به `httpx`/TestClient.
- ریسک/تصمیم: این قراردادها هنوز به paper session، backtester و broker order
  lifecycle متصل نشده‌اند؛ اتصال آن‌ها باید بعد از تکمیل validation ریسک مرحله ۴
  انجام شود.

### مرحله 3 — state مستقل و قابل بازسازی strategy

- فایل‌ها و contractها: `freqtrade/forex/strategy_state.py`,
  `tests/forex/test_oanda.py`
- تغییر: `ForexStrategyState` و `ForexStrategyStateStore` برای نگهداری metadata
  strategy، آخرین candle/signal، شمارنده و indicator state اضافه شدند. state
  با schema version ذخیره و به‌صورت atomic بازنویسی می‌شود و هیچ position/unit
  broker در آن قرار نمی‌گیرد.
- command قابل تکرار:
  `python -m pytest tests/forex/test_oanda.py -q -k "strategy_state"`
- خروجی: `2 passed` و یک warning وابسته به `httpx`/TestClient.
- ریسک/تصمیم: بازیابی position همچنان مسئولیت ledger و broker reconciliation
  است؛ این state فقط تصمیم‌گیری strategy را بازسازی می‌کند.

### مرحله 5 — dry-run JSON و shutdown تمیز

- فایل‌ها و contractها: `freqtrade/forex/runner.py`, `freqtrade/forex/strategy_loop.py`, `freqtrade/forex/cli.py`, `tests/forex/test_oanda.py`
- تغییر: خروجی هر step dry-run به payload JSON-safe با `signal`, `reason`, `order_event`, `position`, `closed_position`, و `costs` گسترش یافت. `DryRunWorker` اکنون در حین انتظار برای step بعدی روی event stop interruptible است و shutdown در bounded و continuous run هم به‌صورت تمیز پایان می‌یابد.
- command قابل تکرار: `python -m pytest tests/forex/test_oanda.py -q`
- خروجی: `69 passed` و یک warning وابسته به `httpx`/TestClient.
- ریسک/تصمیم: این گام همان gate مرحله 5 است و قبل از backtest/Practice آماده است؛ shutdown و report ساختار برای debugging و monitoring پروژه ضروری هستند.

### مرحله 6 — داده تاریخی reproducible برای backtest

- فایل‌ها و contractها: `freqtrade/forex/historical.py`, `freqtrade/forex/provider.py`, `freqtrade/forex/cli.py`, `tests/forex/test_oanda.py`
- تغییر: `HistoricalCandleStore` داده‌ی raw و normalized را با کلید دقیق instrument/timeframe/start/end ذخیره می‌کند. `fetch_historical` بازه‌ی UTC را validate می‌کند، کندل ناقص را حذف می‌کند و اجرای تکراری را بدون درخواست دوباره به OANDA از cache می‌خواند. CLI backtest نیز `--start`, `--end` و `--data-cache` را پشتیبانی می‌کند.
- command قابل تکرار: `python -m pytest tests/forex/test_oanda.py -q -k "historical_provider"`
- خروجی: `2 passed` و یک warning وابسته به `httpx`/TestClient.
- ریسک/تصمیم: intrabar detail، portfolio چند instrument، equity curve و validation مستقل هنوز در گام‌های بعدی مرحله 6 باز هستند.

### مرحله 6 — intrabar stop و take-profit

- فایل‌ها و contractها: `freqtrade/forex/backtest.py`, `tests/forex/test_oanda.py`
- تغییر: `ForexBacktester` اکنون detail candles را در بازه‌ی واقعی position بررسی می‌کند و stop/take-profit را پیش از close کندل اصلی trigger می‌کند. قیمت خروج با spread/slippage موجود سازگار است؛ در برخورد هم‌زمان، stop اولویت محافظه‌کارانه دارد و timestampها به UTC normalize می‌شوند.
- command قابل تکرار: `python -m pytest tests/forex/test_oanda.py -q`
- خروجی: `75 passed` و یک warning وابسته به `httpx`/TestClient.
- ریسک/تصمیم: پورتفولیو چند instrument، margin/P&L ارزی و equity curve هنوز باز هستند و گام‌های بعدی مرحله 6 محسوب می‌شوند.

## 2026-09-18

- مرحله 7 — گیت seeded reproducibility و resume hyperopt تکمیل شد.
- فایل‌ها و contractها: `freqtrade/forex/hyperopt.py`, `tests/forex/test_oanda.py`
- تغییر: `ForexHyperopt.run` اکنون پارامترهای `random_seed`, `save_path` و
  `resume_from` را می‌پذیرد؛ ترتیب candidateها با seed ثابت می‌شود، state JSON
  در مسیر مشخص ذخیره می‌شود و اجرای resume بدون تغییر نتیجه‌ی نهایی، بهترین
  candidate و validation بازتولید می‌شود.
- command قابل تکرار:
  `cd 'd:\SIROOS\freqtrade_to_forex'; pytest tests/forex/test_oanda.py -q -k 'hyperopt_supports_seeded_reproducibility_and_resume or hyperopt_accepts_multi_metric_loss_function or hyperopt_accepts_explicit_parameter_space_and_cost_objective or hyperopt_runs_independent_validation_split or hyperopt_selects_cost_aware_candidate'`
- خروجی: `5 passed, 82 deselected, 1 warning in 10.13s`.
- ریسک/تصمیم: این gate برای determinism و resume در مرحله 7 بسته شد.

- مرحله 7 — گیت pair/period robustness تکمیل شد.
- فایل‌ها و contractها: `freqtrade/forex/hyperopt.py`, `tests/forex/test_oanda.py`
- تغییر: `ForexHyperopt.run` اکنون guard می‌گیرد که `pair_names` و `periods`
  هر کدام حداقل دو ورودی داشته باشند؛ بدون آن، انتخاب بهترین candidate رد می‌شود
  و از overfit به یک pair یا یک بازه جلوگیری می‌شود.
- command قابل تکرار:
  `cd 'd:\SIROOS\freqtrade_to_forex'; pytest tests/forex/test_oanda.py -q -k 'pair_and_period_robustness_guard or hyperopt_supports_seeded_reproducibility_and_resume'`
- خروجی: `2 passed, 86 deselected, 1 warning in 10.82s`.
- ریسک/تصمیم: این gate برای robustness در سطح pair و period بسته شد.

- مرحله 7 — گیت walk-forward و out-of-sample اجباری تکمیل شد.
- فایل‌ها و contractها: `freqtrade/forex/backtest.py`, `freqtrade/forex/hyperopt.py`, `tests/forex/test_oanda.py`
- تغییر: `validate_backtest_split` و `ForexHyperopt.run` اکنون `walk_forward_steps`
  را اجباری با حداقل 2 می‌گیرند و نتیجه‌ی hyperopt بدون walk-forward و
  out-of-sample reject می‌شود. این به‌صورت مشخص در تست‌های جدید پوشش داده شد.
- command قابل تکرار:
  `cd 'd:\SIROOS\freqtrade_to_forex'; pytest tests/forex/test_oanda.py -q -k 'requires_walk_forward_and_out_of_sample_validation or pair_and_period_robustness_guard or hyperopt_supports_seeded_reproducibility_and_resume'`
- خروجی: `3 passed, 85 deselected, 1 warning in 12.45s`.
- ریسک/تصمیم: این gate برای validation مستقل و walk-forward در مرحله 7 بسته شد.

- مرحله 7 — گیت اجرای موازی hyperopt تکمیل شد.
- فایل‌ها و contractها: `freqtrade/forex/hyperopt.py`, `tests/forex/test_oanda.py`
- تغییر: `ForexHyperopt.run` اکنون پارامترهای `parallel=True` و `workers` را
  می‌پذیرد و candidateها را با `ThreadPoolExecutor` به‌صورت موازی ارزیابی می‌کند.
  این مسیر تنها در صورت درخواست صریح فعال می‌شود و determinism، save/resume و
  validation مستقل حفظ می‌شوند.
- command قابل تکرار:
  `cd 'd:\SIROOS\freqtrade_to_forex'; pytest tests/forex/test_oanda.py -q -k 'hyperopt_supports_parallel_execution or requires_walk_forward_and_out_of_sample_validation or pair_and_period_robustness_guard or hyperopt_supports_seeded_reproducibility_and_resume'`
- خروجی: `4 passed, 85 deselected, 1 warning in 10.79s`.
- ریسک/تصمیم: این gate برای اجرای موازی در مرحله 7 بسته شد و گام بعدی در ترتیب
  پروژه، FreqAI research/backtest-then-dry-run است.

- مرحله 7 — گیت FreqAI research/backtest-then-dry-run و metadata تکمیل شد.
- فایل‌ها و contractها: `freqtrade/forex/features.py`,
  `freqtrade/forex/__init__.py`, `tests/forex/test_oanda.py`
- تغییر: `ForexFreqAIExecutionGate` ترتیب research، backtest و dry-run را enforce
  می‌کند و پیش از dry-run وجود model version، feature schema hash و training data
  hash را لازم می‌داند.
- command قابل تکرار:
  `cd 'd:\SIROOS\freqtrade_to_forex'; pytest tests/forex/test_oanda.py -q -k 'freqai'`
- خروجی: `2 passed, 88 deselected, 1 warning`.
- ریسک/تصمیم: اتصال این gate به FreqAI native بعد از تثبیت Practice و در مرحله 9
  انجام می‌شود؛ هیچ مسیر Live در این گام فعال نشده است.

- مرحله 8 — command/config مستقل Practice تکمیل شد.
- فایل‌ها و contractها: `freqtrade/forex/cli.py`, `tests/forex/test_oanda.py`
- تغییر: command مستقل `practice` اضافه شد که فقط preflight read-only اجرا می‌کند،
  `OANDA_EXECUTION_MODE=practice` و environment Practice را می‌خواهد و با
  `dry-run` مسیر جدا دارد؛ هنوز هیچ orderی در این command ارسال نمی‌شود.
- command قابل تکرار:
  `cd 'd:\SIROOS\freqtrade_to_forex'; pytest tests/forex/test_oanda.py -q -k 'practice_cli or practice_preflight'`
- خروجی: `4 passed, 88 deselected, 1 warning`.
- ریسک/تصمیم: گام بعدی ارسال market order با stop و take-profit به Practice است؛
  فقط با credential و account دمو و پس از تست‌های idempotency/reconciliation.

- مرحله 8 — مسیر ارسال market order محافظت‌شده به Practice پیاده‌سازی شد؛
  اجرای واقعی دمو هنوز pending است.
- فایل‌ها و contractها: `freqtrade/forex/cli.py`, `tests/forex/test_oanda.py`
- تغییر: command `practice` اکنون با دریافت هم‌زمان units، stop-loss، take-profit
  و client order id یک market order را از مسیر `OandaExecutionGateway` به OANDA
  Practice می‌فرستد؛ نبود هرکدام از این ورودی‌ها order را رد می‌کند و preflight
  بدون order همچنان read-only است.
- command قابل تکرار بدون credential واقعی:
  `cd 'd:\SIROOS\freqtrade_to_forex'; pytest tests/forex/test_oanda.py -q -k 'practice_cli or practice_preflight or practice_gateway_forwards_order'`
- خروجی: `6 passed, 88 deselected, 1 warning` در تست‌های Practice؛ اجرای واقعی
  با credential دمو انجام نشده است.
- ریسک/تصمیم: اجرای واقعی نیازمند OANDA Practice credential است؛ گام بعدی ثبت
  fill/reject/cancel/partial-fill و transaction stream خواهد بود.

- مرحله 8 — lifecycle سفارش Practice و partial-fill تکمیل شد.
- فایل‌ها و contractها: `freqtrade/forex/transactions.py`,
  `tests/forex/test_oanda.py`
- تغییر: `OrderStateMachine` اکنون fillهای چندمرحله‌ای را با `filled_units`
  تجمیع می‌کند، وضعیت `PARTIALLY_FILLED` را از `FILLED` جدا می‌کند و وضعیت‌های
  reject/cancel و transaction stream را در همان lifecycle نگه می‌دارد.
- command قابل تکرار:
  `pytest tests/forex/test_oanda.py -q -k 'order_state_machine or transaction_stream or practice'`
- خروجی: `10 passed, 85 deselected, 1 warning`.
- ریسک/تصمیم: این گام با mock و stream contract تست می‌شود؛ تست end-to-end با
  حساب Practice و reconciliation در گام‌های بعد باقی است.

- مرحله 8 — reconciliation بعد از order تکمیل شد.
- فایل‌ها و contractها: `freqtrade/forex/execution.py`, `freqtrade/forex/cli.py`,
  `tests/forex/test_oanda.py`
- تغییر: `submit_market_order_and_reconcile` بعد از ارسال order، account summary
  و open positions را از broker می‌خواند و snapshot را همراه `ExecutionResult`
  برمی‌گرداند؛ command Practice از همین مسیر استفاده می‌کند.
- command قابل تکرار:
  `pytest tests/forex/test_oanda.py -q -k 'reconciles_account_and_positions_after_submission or reconcile'`
- خروجی: `4 passed, 92 deselected, 1 warning`.
- ریسک/تصمیم: خطای reconciliation بعد از ارسال باید در گام fault/restart با
  recovery و جلوگیری از duplicate order پوشش داده شود.

- مرحله 8 — گیت network disconnect/timeout/retry/restart تکمیل شد.
- فایل‌ها و contractها: `freqtrade/forex/oanda.py`, `freqtrade/forex/stream.py`,
  `tests/forex/test_oanda.py`
- تغییر: رفتار retry برای disconnect و timeout هنگام Practice order با
  `MockTransport` تست شد؛ retryهای REST نتیجه‌ی یکسان را حفظ می‌کنند و stream
  با cursor ذخیره‌شده پس از reconnect از transaction بعدی ادامه می‌دهد.
- command قابل تکرار:
  `pytest tests/forex/test_oanda.py -q -k 'retries_disconnect_and_timeout or transaction_stream_reconnects or reconcile'`
- خروجی: `5 passed, 92 deselected, 1 warning`.
- ریسک/تصمیم: اجرای end-to-end با account واقعی Practice و سناریوی broker که
  request را دریافت کرده اما response قطع شده، هنوز باقی است.

- مرحله 8 — close و reverse در netting OANDA تکمیل شد.
- فایل‌ها و contractها: `freqtrade/forex/execution.py`,
  `tests/forex/test_oanda.py`
- تغییر: `close_position` با `-current_units` و `reverse_position` با
  `-2 * current_units` ارسال می‌شوند؛ close با دلیل صریح بدون stop مجاز است و
  reverse stop محافظتی و reconciliation بعد از order را حفظ می‌کند.
- command قابل تکرار:
  `pytest tests/forex/test_oanda.py -q -k 'netting_close_and_reverse or position_semantics'`
- خروجی: `2 passed, 96 deselected, 1 warning`.
- ریسک/تصمیم: تست broker واقعی netting و تطبیق نهایی local/broker به credential
  Practice و اجرای end-to-end وابسته است.

- مرحله 8 — گزارش اختلاف simulated fill و real fill تکمیل شد.
- فایل‌ها و contractها: `freqtrade/forex/execution.py`, `freqtrade/forex/cli.py`,
  `tests/forex/test_oanda.py`
- تغییر: `ExecutionResult` اکنون simulated fill، real fill و اختلاف signed قیمت
  را گزارش می‌کند؛ Practice CLI برای order واقعی `--simulated-fill-price` را
  اجباری کرده و خروجی JSON این اختلاف را ثبت می‌کند.
- command قابل تکرار:
  `pytest tests/forex/test_oanda.py -q -k 'reports_simulated_vs_real_fill or netting_close_and_reverse'`
- خروجی: `3 passed, 96 deselected, 1 warning`.
- ریسک/تصمیم: تبدیل اختلاف به pip و تحلیل slippage بر اساس جهت/حجم می‌تواند در
  گزارش عملیاتی بعدی اضافه شود.

- مرحله 8 — suite end-to-end برای Practice تکمیل شد.
- فایل‌ها و contractها: `tests/forex/test_oanda_practice_e2e.py`
- تغییر: suite opt-in اکنون health، account، whitelist instrument، market order
  محافظت‌شده، reconciliation و close netting را برای OANDA Practice پوشش می‌دهد؛
  اجرای order فقط با `OANDA_PRACTICE_E2E_ORDERS=1` فعال می‌شود.
- command قابل تکرار بدون credential/order:
  `pytest tests/forex/test_oanda_practice_e2e.py -q`
- خروجی: `2 skipped` بدون credential و بدون ارسال request؛ اجرای واقعی با
  opt-in محیطی انجام می‌شود.
- ریسک/تصمیم: اجرای واقعی suite به credential حساب Practice و instrument مجاز
  نیاز دارد؛ بدون opt-in هیچ request یا order ارسال نمی‌شود.

- مرحله 8 — kill switch و risk limits در مسیر order فعال شدند.
- فایل‌ها و contractها: `freqtrade/forex/risk_limits.py`,
  `freqtrade/forex/execution.py`, `tests/forex/test_oanda.py`
- تغییر: `RiskControl` قبل از broker call، max daily loss، max total risk و
  max currency exposure را validate می‌کند؛ kill switch نیز با خطای صریح order
  را متوقف می‌کند و پس از رفع وضعیت قابل deactivate است.
- command قابل تکرار:
  `pytest tests/forex/test_oanda.py -q -k 'risk_control or gateway_blocks_order_when_risk_control_is_tripped'`
- خروجی: `2 passed, 99 deselected, 1 warning`.
- ریسک/تصمیم: اتصال usage واقعی account/position به اجرای Practice و emergency
  close مستقل هنوز برای گام عملیاتی بعد باقی است.

- مرحله 8 — ثبت نتایج Practice چندsession آماده شد.
- فایل‌ها و contractها: `freqtrade/forex/practice_runs.py`,
  `freqtrade/forex/cli.py`, `tests/forex/test_oanda.py`
- تغییر: `PracticeRunRecorder` نتایج append-only شامل session، زمان شروع/پایان،
  instrumentها، تعداد order، status و notes را در JSONL ثبت می‌کند و command
  `practice-report` آن‌ها را نمایش می‌دهد.
- command قابل تکرار بدون credential:
  `pytest tests/forex/test_oanda.py -q -k 'practice_run_recorder'`
- خروجی: `1 passed, 101 deselected, 1 warning`.
- ریسک/تصمیم: checkbox اجرای چندروزه تا ثبت واقعی چند session با account Practice
  تکمیل نمی‌شود؛ recorder به‌تنهایی اجرای broker را ادعا نمی‌کند.

- مرحله 8 — runner چندsession Practice اضافه شد.
- فایل‌ها و contractها: `freqtrade/forex/cli.py`, `tests/forex/test_oanda.py`
- تغییر: command `practice-run` sessionهای نام‌گذاری‌شده را با interval و steps
  اجرا می‌کند، health result را بررسی می‌کند و هر session را در recorder ثبت
  می‌کند؛ command فقط در environment و execution mode مربوط به Practice فعال است.
- command قابل تکرار بدون credential:
  `pytest tests/forex/test_oanda.py -q -k 'practice_run_cli or practice_run_recorder'`
- خروجی: `2 passed, 101 deselected, 1 warning`.
- ریسک/تصمیم: اجرای چندروزه واقعی، order lifecycle و ثبت order_count هنوز با
  حساب Practice و اجرای opt-in عملیاتی باقی است.

## 2026-09-24 — Practice real-order verification and data gate

- status: یک سفارش real OANDA Practice با `--units 100` و stop/take-profit در
  یک session فعال با `OANDA_EXECUTION_MODE=practice` ارسال شد. پاسخ broker
  `simulated: false` را برگرداند و reconciliation پس از order هیچ open position
  باقی نگذاشت.
- ثبت: این نتیجه در session فعال به‌صورت operational evidence ثبت شد، اما
  متغیرهای محیطی OANDA به‌صورت session-local باقی ماندند و نباید در repo یا docs
  ذخیره شوند.
- blocker: `user_data/data/oanda` در وضعیت فعلی خالی است؛ بنابراین native
  backtest واقعی با data OANDA تا دانلود/ایمپورت داده‌های تاریخی EUR/USD یا
  GBP_USD به‌صورت محلی ممکن نخواهد شد.
- نتیجه: code validation و Practice execution path local green هستند، اما real
  backtest و live gate هنوز به داده‌های broker و session env معتبر وابسته‌اند.

- مرحله 9 — adapter native strategy/runmode تکمیل شد.
- فایل‌ها و contractها: `freqtrade/forex/config.py`,
  `freqtrade/forex/strategies/ema_cross.py`, `tests/forex/test_oanda.py`
- تغییر: `execution_mode_for_native_runmode` و
  `validate_native_forex_config` mapping صریح native `RunMode` به execution
  mode فارکس را فراهم می‌کنند؛ Practice عمداً از native runmode جدا باقی می‌ماند.
- command قابل تکرار:
  `pytest tests/forex/test_oanda.py -q -k 'native_runmode_maps_to_forex_execution_contract or native_ema_strategy_loads_through_strategy_resolver'`
- خروجی: `2 passed, 102 deselected, 1 warning`.
- ریسک/تصمیم: اتصال این adapter به ExchangeResolver و commandهای native در
  checkboxهای بعدی Stage 9 انجام می‌شود.

- مرحله 9 — OANDA در ExchangeResolver و native validation ثبت شد.
- فایل‌ها و contractها: `freqtrade/exchange/oanda.py`,
  `freqtrade/exchange/__init__.py`, `freqtrade/exchange/check_exchange.py`,
  `tests/forex/test_oanda.py`
- تغییر: façade native `Oanda` از resolver بارگذاری می‌شود، بدون initialize کردن
  CCXT یا network؛ `check_exchange` برای نام `oanda` به
  `validate_native_forex_config` واگذار می‌شود و credential/config فارکس را
  validate می‌کند.
- command قابل تکرار:
  `pytest tests/forex/test_oanda.py -q -k 'exchange_resolver_loads_native_oanda or native_runmode_maps_to_forex_execution_contract'`
- خروجی: `2 passed, 103 deselected, 1 warning`.
- ریسک/تصمیم: façade فعلاً فقط resolution و validation است؛ اتصال market/data/order
  به commandهای native در checkboxهای بعدی باقی است.

- مرحله 9 — persistence سازگار و migration-safe تکمیل شد.
- فایل‌ها و contractها: `freqtrade/forex/ledger.py`,
  `freqtrade/forex/__init__.py`, `tests/forex/test_oanda.py`
- تصمیم معماری: SQLite فعلی به‌عنوان compatibility layer حفظ شد؛
  `NativeTradeOrderStore` روی `PaperLedger` facade می‌سازد، schema version و
  `forex_schema_migrations` را ثبت می‌کند و state موجود را migrate-in-place نگه
  می‌دارد. مهاجرت به مدل native واقعی بعد از اثبات commandهای native انجام می‌شود.
- command قابل تکرار:
  `pytest tests/forex/test_oanda.py -q -k 'native_trade_order_store_is_migration_safe'`
- خروجی: `1 passed, 105 deselected, 1 warning`; regressionهای ledger/paper نیز
  `7 passed, 99 deselected, 1 warning` شدند.
- ریسک/تصمیم: این لایه native ORM نیست؛ compatibility contract است و از duplicate
  persistence جلوگیری می‌کند تا مرحله اتصال commandها تکمیل شود.

- مرحله 9 — اتصال persistence به native command entry points تکمیل شد.
- فایل‌ها و contractها: `freqtrade/forex/ledger.py`,
  `freqtrade/commands/trade_commands.py`, `freqtrade/commands/optimize_commands.py`,
  `tests/forex/test_oanda.py`
- تغییر: `trade`، `backtesting` و `hyperopt` در configهای OANDA، facade مشترک
  `NativeTradeOrderStore` را با مسیر قابل تنظیم inject می‌کنند؛ commandهای سایر
  exchangeها بدون تغییر باقی می‌مانند.
- command قابل تکرار:
  `pytest tests/forex/test_oanda.py -q -k 'native_command_config_attaches_shared_forex_persistence or native_trade_order_store_is_migration_safe'`
- خروجی: `3 passed, 105 deselected, 1 warning`.
- ریسک/تصمیم: این hook persistence را در runtime config وصل می‌کند؛ اتصال کامل
  strategy/data/order execution به native command loop در checkboxهای بعدی است.

- مرحله 9 — اتصال DataProvider/pairlist/wallet/pricing فارکس به core contractها.
- فایل‌ها و contractها: `freqtrade/forex/native_core.py`,
  `freqtrade/exchange/oanda.py`, `tests/forex/test_oanda.py`
- تغییر: `OandaNativeCoreBridge` pairlist و DataFrame cache، wallet snapshot
  account-currency و pricing side-specific bid/ask را expose می‌کند و روی façade
  native OANDA در دسترس است؛ هیچ pricing واحدی جایگزین bid/ask نمی‌شود.
- command قابل تکرار:
  `pytest tests/forex/test_oanda.py -q -k 'native_core_bridge_maps_pairlist_wallet_and_bid_ask_pricing'`
- خروجی: `1 passed, 108 deselected, 1 warning`.
- ریسک/تصمیم: اتصال کامل async refresh به DataProvider اصلی و wallet lifecycle
  native در گام integration بعدی باقی است.

- مرحله 9 — native protections فارکس تکمیل شد.
- فایل‌ها و contractها: `freqtrade/forex/native_protections.py`,
  `freqtrade/exchange/oanda.py`, `tests/forex/test_oanda.py`
- تغییر: `ForexNativeProtectionBridge` spread، free margin، margin level،
  market session و financing cost را پیش از entry ارزیابی می‌کند و دلایل reject
  را به‌صورت typed برمی‌گرداند؛ bridge روی façade native OANDA expose شده است.
- command قابل تکرار:
  `pytest tests/forex/test_oanda.py -q -k 'native_protection_bridge_enforces_fx_limits'`
- خروجی: `1 passed, 109 deselected, 1 warning`.
- ریسک/تصمیم: wiring نهایی این decision به چرخه order native بعد از اثبات
  native command execution باقی است.

- مرور کامل roadmap و تکمیل سه گام ضروری انجام شد.
- مرحله 0: baseline امن با `111 passed, 2 skipped` و CLI help به‌روزرسانی شد؛
  دو skip مربوط به E2E اختیاری و credential-dependent هستند.
- مرحله 2: pagination/cache تاریخی و retry/backoff که در کد و تست موجود بودند،
  در roadmap به‌درستی marked شدند؛ session scheduler و timestamp-driven dry-run
  همچنان pending هستند.
- مرحله 9: wiring مشترک `forex_core_bridge`، `forex_protection_bridge` و
  persistence به config native و façade OANDA تکمیل شد؛ smoke test native آن
  با config بدون credential اجرا می‌شود.
- command قابل تکرار:
  `python -m pytest tests/forex/test_oanda.py tests/forex/test_oanda_practice_e2e.py -q`
- خروجی: `111 passed, 2 skipped, 1 warning`.
- ریسک/تصمیم: health/order واقعی Practice و اجرای کامل native loop هنوز به
  credential و اتصال واقعی DataProvider/order lifecycle نیاز دارد؛ UI/Telegram
  عمداً تا بسته‌شدن این گیت‌ها خارج از scope است.

- مرحله 6 — گیت portfolio/account-currency P&L تکمیل شد.
- فایل‌ها و contractها: `freqtrade/forex/backtest.py`, `tests/forex/test_oanda.py`
- تغییر: `ForexBacktester._finalize_result` اکنون موجودی نهایی را با جمع P&L معامله‌ها به ارز حساب reconcile می‌کند؛ هر trade نیز با نرخ `ForexQuoteRate` به حساب‌currency تبدیل می‌شود و `margin_snapshot` با state نهایی هم‌راستا می‌شود.
- command قابل تکرار:
  `cd 'd:\SIROOS\freqtrade_to_forex'; pytest tests/forex/test_oanda.py -k 'backtester' -q`
- خروجی: `6 passed, 71 deselected, 1 warning in 8.97s`.
- ریسک/تصمیم: این gate برای مرحله 6 در سطح portfolio/account-currency بسته شد.

- مرحله 6 — گیت swap/rollover با جهت و مدت نگهداری تکمیل شد.
- فایل‌ها و contractها: `freqtrade/forex/costs.py`, `freqtrade/forex/backtest.py`, `tests/forex/test_oanda.py`
- تغییر: تابع `financing_cost` و محاسبه‌ی backtest اکنون هزینه نگهداری را بر اساس جهت پوزیشن (`long`/`short`) و مدت زمان نگهداری اعمال می‌کنند؛ برای short، علامت هزینه معکوس می‌شود و برای long به‌صورت مثبت باقی می‌ماند.
- command قابل تکرار:
  `cd 'd:\SIROOS\freqtrade_to_forex'; pytest tests/forex/test_oanda.py -k 'financing_cost or backtester' -q`
- خروجی: `8 passed, 70 deselected, 1 warning in 8.56s`.
- ریسک/تصمیم: این gate برای swap/rollover در مرحله 6 بسته شد.

- مرحله 6 — گیت partial fill و spread متغیر تکمیل شد.
- فایل‌ها و contractها: `freqtrade/forex/backtest.py`, `tests/forex/test_oanda.py`
- تغییر: `ForexBacktester` اکنون partial fill را با نسبت `fill_ratio` اعمال می‌کند و spread را بر اساس timestamp معامله (entry/trigger) می‌خواند؛ برای شرایط متغیر، هزینه و قیمت خروج بر اساس زمان دقیق order و trigger محاسبه می‌شوند.
- command قابل تکرار:
  `cd 'd:\SIROOS\freqtrade_to_forex'; pytest tests/forex/test_oanda.py -k 'partial_fill or financing_cost or backtester' -q`
- خروجی: `10 passed, 69 deselected, 1 warning in 9.38s`.
- ریسک/تصمیم: این gate برای partial fill و spread متغیر در مرحله 6 بسته شد و گام بعدی در ترتیب پروژه، خروجی معاملات/equity curve و drawdown است.

- مرحله 6 — گیت خروجی معاملات، equity curve و drawdown تکمیل شد.
- فایل‌ها و contractها: `freqtrade/forex/backtest.py`, `freqtrade/forex/cli.py`, `tests/forex/test_oanda.py`
- تغییر: `BacktestResult` اکنون خروجی serializable معاملات، equity curve بر اساس معاملات مرتب‌شده با UTC، max drawdown مطلق/نسبی و breakdown روزانه، هفتگی و ماهانه با P/L و wins/losses/draws تولید می‌کند؛ CLI همین داده‌ها را در JSON گزارش می‌کند.
- command قابل تکرار:
  `cd 'd:\SIROOS\freqtrade_to_forex'; pytest tests/forex/test_oanda.py -k 'backtest_result_reports or backtester' -q`
- خروجی: `8 passed, 72 deselected, 1 warning in 9.84s`.
- ریسک/تصمیم: این gate برای گزارش پایه‌ی backtest بسته شد؛ گام بعدی مرحله 6، lookahead، recursive و data leakage checks است.

### مرحله 4 — pip value و currency conversion عمومی

- فایل‌ها و contractها: `freqtrade/forex/risk.py`, `freqtrade/forex/__init__.py`,
  `tests/forex/test_oanda.py`
- تغییر: `ForexRateBook` برای تبدیل direct/inverse/chained و helperهای
  `quote_to_account_rate` و `pip_value_per_unit` اضافه شد. `units_for_fixed_risk`
  اکنون با `account_currency` و نرخ‌های `ForexQuoteRate` sizing عمومی انجام
  می‌دهد و مسیر قدیمی rate صریح را حفظ می‌کند.
- command قابل تکرار:
  `python -m pytest tests/forex/test_oanda.py -q -k "units_for_fixed_risk or pip_value or currency_conversion or missing_currency"`
- خروجی: `3 passed` و یک warning وابسته به `httpx`/TestClient.
- ریسک/تصمیم: نرخ‌ها هنوز باید از quoteهای معتبر broker با timestamp/source
  تغذیه شوند؛ margin، exposure و validation محدودیت‌های instrument در ادامه
  مرحله ۴ باقی هستند.

### مرحله 4 — fill و هزینه‌های معاملاتی فارکس

- فایل‌ها و contractها: `freqtrade/forex/costs.py`,
  `tests/forex/test_oanda.py`
- تغییر: `ForexFillModel` و `FillResult` برای fill کامل/partial، bid/ask، spread
  و slippage اضافه شدند؛ helper `financing_cost` نیز هزینه swap/financing را با
  conversion و `Decimal` محاسبه می‌کند.
- command قابل تکرار:
  `python -m pytest tests/forex/test_oanda.py -q -k "fill_model or financing_cost"`
- خروجی: `3 passed` و یک warning وابسته به `httpx`/TestClient.
- ریسک/تصمیم: این contract هنوز به lifecycle سفارش و backtest portfolio متصل
  نشده است؛ partial fill واقعی و reconciliation در مرحله Practice باقی می‌ماند.

### مرحله 4 — margin و exposure

- فایل‌ها و contractها: `freqtrade/forex/margin.py`,
  `tests/forex/test_oanda.py`
- تغییر: `MarginPosition`، `MarginSnapshot` و helperهای margin برای notional،
  required margin، free margin، margin level و gross exposure اضافه شدند.
  `validate_exposure` سقف exposure را قبل از پذیرش موقعیت جدید کنترل می‌کند.
- command قابل تکرار:
  `python -m pytest tests/forex/test_oanda.py -q -k "margin_snapshot or margin_uses_currency"`
- خروجی: `2 passed` و یک warning وابسته به `httpx`/TestClient.
- ریسک/تصمیم: margin فعلاً contract محاسباتی است؛ marginAvailable واقعی OANDA،
  liquidation و reconciliation به integration سفارش/Practice وابسته است.

### مرحله 4 — محدودیت‌های ریسک و correlation exposure

- فایل‌ها و contractها: `freqtrade/forex/risk_limits.py`,
  `tests/forex/test_oanda.py`
- تغییر: `RiskLimits`، `RiskUsage` و `RiskLimitPolicy` برای سقف daily loss،
  total open risk و gross exposure ارزی اضافه شدند. `aggregate_currency_exposure`
  کد ارز را normalize و exposure را محافظه‌کارانه تجمیع می‌کند.
- command قابل تکرار:
  `python -m pytest tests/forex/test_oanda.py -q -k "risk_policy"`
- خروجی: `1 passed` و یک warning وابسته به `httpx`/TestClient.
- ریسک/تصمیم: policy فعلاً قبل از order قابل استفاده است؛ اتصال به account
  روزانه، correlation matrix واقعی و kill switch در مراحل dry-run/Practice باقی
  مانده است.

### مرحله 4 — validation محدودیت‌های broker order

- فایل‌ها و contractها: `freqtrade/forex/order_validation.py`,
  `tests/forex/test_oanda.py`
- تغییر: `BrokerOrderValidator` برای minimum trade size، unit/price precision،
  minimum stop distance و جهت صحیح stop/take-profit اضافه شد.
- command قابل تکرار:
  `python -m pytest tests/forex/test_oanda.py -q -k "broker_order_validator"`
- خروجی: `1 passed` و یک warning وابسته به `httpx`/TestClient.
- ریسک/تصمیم: minimum distance فعلاً از config/domain به validator داده می‌شود؛
  خواندن و reconcile کردن محدودیت‌های واقعی هر instrument از OANDA در Practice
  باقی است.

### مرحله 4 — idempotency و retry سفارش

- فایل‌ها و contractها: `freqtrade/forex/execution.py`,
  `freqtrade/forex/oanda.py`, `tests/forex/test_oanda.py`
- تغییر: `OandaExecutionGateway` نتیجه‌ی موفق را برای `client_order_id` cache
  می‌کند و reuse همان id با payload متفاوت را با `IdempotencyError` رد می‌کند.
  `OandaClient` خطاهای transient transport را نیز با همان request و client id
  retry می‌کند.
- command قابل تکرار:
  `python -m pytest tests/forex/test_oanda.py -q -k "idempotent_result or retry"`
- خروجی: `1 passed` و یک warning وابسته به `httpx`/TestClient.
- ریسک/تصمیم: cache فعلاً در حافظه‌ی gateway است؛ persistence و reconciliation
  بعد از restart باید در گام Practice/ledger تکمیل شود.

### مرحله 4 — چرخه‌ی market و pending order

- فایل‌ها و contractها: `freqtrade/forex/models.py`, `freqtrade/forex/oanda.py`,
  `tests/forex/test_oanda.py`
- تغییر: ساخت market قبلی حفظ شد و APIهای `create_stop_order`،
  `create_take_profit_order`، `modify_order` و `cancel_order` اضافه شدند.
  `OandaOrderActionResult` نتیجه‌ی modify/cancel را به‌صورت typed برمی‌گرداند.
- command قابل تکرار:
  `python -m pytest tests/forex/test_oanda.py -q -k "stop_take_profit_modify"`
- خروجی: `1 passed` و یک warning وابسته به `httpx`/TestClient.
- ریسک/تصمیم: اتصال این order types به netting/reduce-only و protective-stop
  policy هنوز در checkboxهای بعدی مرحله ۴ باقی است.

### مرحله 4 — semantics پوزیشن و netting

- فایل‌ها و contractها: `freqtrade/forex/position_semantics.py`,
  `tests/forex/test_oanda.py`
- تغییر: `apply_order` و `close_by_opposite` برای netting، reversal و
  reduce-only اضافه شدند. در حالت hedging، عملیات reduce-only و close-by-opposite
  بدون trade identifier صریحاً رد می‌شوند.
- command قابل تکرار:
  `python -m pytest tests/forex/test_oanda.py -q -k "position_semantics"`
- خروجی: `1 passed` و یک warning وابسته به `httpx`/TestClient.
- ریسک/تصمیم: اتصال resolver به positionهای واقعی OANDA و close order در Practice
  هنوز به reconciliation و trade identifierهای broker وابسته است.

### مرحله 4 — protective stop policy

- فایل‌ها و contractها: `freqtrade/forex/order_validation.py`,
  `freqtrade/forex/execution.py`, `tests/forex/test_oanda.py`
- تغییر: `ProtectiveStopPolicy` به‌صورت پیش‌فرض stop محافظتی را الزامی می‌کند؛
  نبود stop فقط با `no_stop_reason` صریح مجاز است و این policy پیش از idempotent
  order submission اجرا می‌شود.
- command قابل تکرار:
  `python -m pytest tests/forex/test_oanda.py -q -k "protective_stop or dry_run_gateway or idempotent_result or practice_gateway"`
- خروجی: `4 passed` و یک warning وابسته به `httpx`/TestClient.
- ریسک/تصمیم: policy اکنون در gateway enforce می‌شود؛ انتخاب stop price و
  trigger شدن آن در paper session و Practice execution در مراحل بعدی باقی است.
  strategy loop stop محاسبه‌شده را به paper order forward می‌کند و close/reduce
  orderها reason صریح no-stop دارند.

### مرحله 5 — ledger هم‌معنا با order/trade/position

- فایل‌ها و contractها: `freqtrade/forex/ledger.py`,
  `freqtrade/forex/paper.py`, `tests/forex/test_oanda.py`
- تغییر: جداول و recordهای `paper_orders` و `paper_positions` اضافه شدند.
  open/close paper اکنون order filled، trade lifecycle و position snapshot را
  با هم ثبت و در close position snapshot را حذف می‌کند.
- command قابل تکرار:
  `python -m pytest tests/forex/test_oanda.py -q -k "paper_ledger_persists_mark_and_close"`
- خروجی: `1 passed` و یک warning وابسته به `httpx`/TestClient.
- ریسک/تصمیم: restart/resume و بازیابی position از snapshot گام بعدی dry-run
  است؛ reconciliation با broker هنوز در Practice باقی می‌ماند.

### مرحله 5 — trigger حفاظتی paper position

- فایل‌ها و contractها: `freqtrade/forex/paper.py`,
  `tests/forex/test_oanda.py`
- تغییر: `PaperPosition` اکنون stop/take-profit را نگه می‌دارد و
  `DryRunSession.mark_to_market` با قیمت خروج bid/ask آن‌ها را trigger می‌کند.
  close خودکار از همان gateway و ledger order/trade lifecycle عبور می‌کند.
- command قابل تکرار:
  `python -m pytest tests/forex/test_oanda.py -q -k "paper_session_triggers or paper_ledger_persists"`
- خروجی: `2 passed` و یک warning وابسته به `httpx`/TestClient.
- ریسک/تصمیم: account equity، free margin، daily loss و بازیابی stop/target در
  restart هنوز در checkboxهای بعدی dry-run باقی است.

### مرحله 5 — account state در dry-run

- فایل‌ها و contractها: `freqtrade/forex/paper.py`,
  `tests/forex/test_oanda.py`
- تغییر: `PaperAccountState` و `DryRunSession.account_state()` اضافه شدند.
  balance/equity، unrealized و realized P/L، used/free margin با leverage و
  daily loss پس از close در dry-run قابل مشاهده و تست هستند.
- command قابل تکرار:
  `python -m pytest tests/forex/test_oanda.py -q -k "paper_account_state"`
- خروجی: `1 passed` و یک warning وابسته به `httpx`/TestClient.
- ریسک/تصمیم: daily loss فعلاً از چرخه‌ی جاری session محاسبه می‌شود و reset
  روزانه، account state واقعی OANDA و kill switch هنوز باقی است.

### مرحله 5 — resume/restart و بازسازی ledger paper

- فایل‌ها و contractها: `freqtrade/forex/paper.py`, `freqtrade/forex/ledger.py`,
  `tests/forex/test_oanda.py`
- تغییر: `DryRunSession.refresh_prices()` اکنون پس از به‌روزرسانی قیمت،
  `restore_positions_from_ledger()` را اجرا می‌کند و پوزیشن‌های باز ذخیره‌شده در
  `paper_positions` را با همان `trade_id` و `entry_price` به حافظه برمی‌گرداند.
  این مسیر از ایجاد دوگانه یا حذف state در restart جلوگیری می‌کند.
- command قابل تکرار:
  `python -m pytest tests/forex/test_oanda.py -q -k "restore_open_positions_from_ledger or paper_ledger_persists_mark_and_close"`
- خروجی: `1 passed`, `66 deselected` و یک warning وابسته به `httpx`/TestClient.
- ریسک/تصمیم: این gate برای resume/restart در dry-run تکمیل شد.

### مرحله 5 — چند‌pair dry-run و max-open-positions

- فایل‌ها و contractها: `freqtrade/forex/paper.py`, `tests/forex/test_oanda.py`
- تغییر: `DryRunSession` اکنون `max_open_positions` را می‌پذیرد و قبل از
  ثبت new market order، تعداد پوزیشن‌های باز را کنترل می‌کند؛ این مانع
  باز کردن اضافه‌تر از سقف مجاز در dry-run می‌شود و برای چند instrument هم
  درست کار می‌کند. حالت multi-pair در session به‌صورت tuple instruments حفظ شده
  و `refresh_prices()` برای همه‌ی آن‌ها prices می‌گیرد.
- command قابل تکرار:
  `python -m pytest tests/forex/test_oanda.py -q -k "multi_pair_and_max_open_positions or restore_open_positions_from_ledger"`
- خروجی: `2 passed`, `66 deselected` و یک warning وابسته به `httpx`/TestClient.
- ریسک/تصمیم: این gate برای dry-run multi-pair و max cap تکمیل شد. مرحله بعدی
  خروجی JSON signal/order/position/costs در dry-run است.

### مرحله 7 — FreqAI feature schema و adapter فارکس

- فایل‌ها و contractها: `freqtrade/forex/features.py`, `freqtrade/forex/__init__.py`, `tests/forex/test_oanda.py`
- تغییر: `ForexFreqAIAdapter` اضافه شد و DataFrame ورودی را به featureهای فارکس
  با `spread_points`, `spread_pct`, `volatility_5`, `atr_14` و `session_hour`
  غنی می‌کند. برچسب‌ها به‌صورت `label = ((close.shift(-label_period) / close) - 1)`
  تعریف می‌شوند؛ این یعنی label فقط از آینده‌ی مجزا و بدون lookahead در همان
  candle جاری ساخته می‌شود.
- command قابل تکرار:
  `cd 'd:\SIROOS\freqtrade_to_forex'; pytest tests/forex/test_oanda.py -q -k 'freqai_adapter_builds_schema_and_labels_without_leakage or forex_feature_pipeline_preserves_alignment_and_warmup'`
- خروجی: `2 passed, 82 deselected, 1 warning in 9.52s`.
- ریسک/تصمیم: این gate برای schema و label-safe FreqAI foundation مرحله 7 بسته
  شد؛ مرحله بعدی در ترتیب پروژه، گسترش parameter space و loss functionهای
  hyperopt به‌صورت walk-forward-safe است، نه اجرای live یا dry-run AI.

### مرحله 7 — parameter space و objective cost-aware hyperopt

- فایل‌ها و contractها: `freqtrade/forex/hyperopt.py`, `tests/forex/test_oanda.py`
- تغییر: `ForexHyperopt.run()` اکنون `parameter_space` و `objective_fn` را
  می‌پذیرد و در حالت legacy همچنان grid ساده را حفظ می‌کند. objective پیش‌فرض
  برای انتخاب candidate اکنون `net_pl - drawdown*0.5 - total_costs*0.1` است و
  risk_fraction هر candidate را به‌صورت مستقل اعمال می‌کند.
- command قابل تکرار:
  `cd 'd:\SIROOS\freqtrade_to_forex'; pytest tests/forex/test_oanda.py -q -k 'hyperopt_accepts_explicit_parameter_space_and_cost_objective or hyperopt_runs_independent_validation_split or hyperopt_selects_cost_aware_candidate'`
- خروجی: `3 passed, 82 deselected, 1 warning in 10.22s`.
- ریسک/تصمیم: این gate برای explicit parameter space و cost-aware objective از
  مرحله 7 بسته شد؛ گام بعدی در ترتیب پروژه، loss functionهای چندمعیاره و
  walk-forward strict در hyperopt است.

### مرحله 7 — multi-metric loss function و walk-forward-safe candidate selection

- فایل‌ها و contractها: `freqtrade/forex/hyperopt.py`, `tests/forex/test_oanda.py`
- تغییر: `ForexHyperopt.run()` اکنون `loss_function` را هم می‌پذیرد و در صورت
  وجود، candidate selection از max به min تغییر می‌کند تا loss کوچی به‌صورت
  درست انتخاب شود؛ نمونه `custom_loss` در تست نشان می‌دهد که objective و
  validation walk-forward دست‌نخورده باقی مانده‌اند.
- command قابل تکرار:
  `cd 'd:\SIROOS\freqtrade_to_forex'; pytest tests/forex/test_oanda.py -q -k 'hyperopt_accepts_multi_metric_loss_function or hyperopt_accepts_explicit_parameter_space_and_cost_objective or hyperopt_runs_independent_validation_split or hyperopt_selects_cost_aware_candidate'`
- خروجی: `4 passed, 82 deselected, 1 warning in 11.89s`.
- ریسک/تصمیم: این gate برای loss-function‌های چندمعیاره و validation walk-forward
  در مرحله 7 بسته شد؛ گام بعدی در ترتیب پروژه، random seed، result persistence و
  pair robustness guard است.

## قالب ثبت تغییرات بعدی

برای هر تغییر این موارد ثبت شوند:

1. مرحله و checkbox مربوطه.
2. فایل‌ها و contractهای تغییرکرده.
3. command یا test قابل تکرار.
4. خروجی و معیار قبولی.
5. ریسک یا تصمیم بازمانده.
