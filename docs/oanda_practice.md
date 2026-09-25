# OANDA Practice Health Check

This check performs read-only requests. It does not create, modify, or cancel orders.

## PowerShell

Set the credentials in the current terminal session:

```powershell
$env:OANDA_TOKEN = "<your-oanda-practice-token>"
$env:OANDA_ACCOUNT_ID = "<your-practice-account-id>"
$env:OANDA_ENVIRONMENT = "practice"
$env:OANDA_INSTRUMENTS = "EUR_USD,GBP_USD"
```

Run the health check:

```powershell
python -m freqtrade.forex health
```

Use a smaller instrument set when needed:

```powershell
python -m freqtrade.forex health --instruments EUR_USD
```

The command reports account balance/NAV, margin available, instrument metadata, bid/ask prices, and spread. It does not print the API token.

## Environment variables

- `OANDA_TOKEN`: personal v20 API token.
- `OANDA_ACCOUNT_ID`: Practice account ID.
- `OANDA_ENVIRONMENT`: `practice` by default; do not set to `live` for this check unless explicitly intended.
- `OANDA_INSTRUMENTS`: comma-separated instrument names.
- `OANDA_TRANSACTION_CURSOR_PATH`: cursor file used by transaction streaming.

The Practice config template is `config_examples/config_oanda_practice.example.json`. The current execution mode defaults to `dry_run`; the health command remains read-only regardless of that mode.

## Live-price dry-run

The `DryRunSession` reads current OANDA prices and simulates fills in memory. Long
entries use the ask price, short entries use the bid price, and mark-to-market
uses the opposite side of the spread. No order endpoint is called in this mode.

This is the next execution layer before connecting a strategy loop. Keep
`OANDA_EXECUTION_MODE=dry_run` while validating signals and risk sizing.

## Strategy worker

The scheduled worker runs one strategy step per interval and is guarded to
`dry_run` mode. Its default first pair/timeframe target is `EUR/USD` on `5m`.
The worker can be bounded with `max_steps` in tests or controlled experiments;
it does not wait after the final bounded step.

## Paper report

Show persisted paper trades and P/L without OANDA credentials:

```powershell
python -m freqtrade.forex paper-report --ledger user_data/oanda/paper.sqlite
```

The JSON report separates realized and unrealized P/L and lists each trade's
instrument, units, entry/exit prices, and status.

## Read-only API

Start the local API on loopback:

```powershell
uvicorn freqtrade.forex.api:app --host 127.0.0.1 --port 8090
```

Available endpoints:

- `GET http://127.0.0.1:8090/api/v1/health`
- `GET http://127.0.0.1:8090/api/v1/paper/report`
- `GET http://127.0.0.1:8090/api/v1/paper/trades`

The API is read-only. It has no order, cancel, or account-mutation endpoint.

## One-step dry-run

After setting the Demo credentials in the same PowerShell session, run one
strategy step:

```powershell
$env:OANDA_TOKEN = "<your-oanda-practice-token>"
$env:OANDA_ACCOUNT_ID = "<your-practice-account-id>"
$env:OANDA_ENVIRONMENT = "practice"
$env:OANDA_EXECUTION_MODE = "dry_run"
python -m freqtrade.forex dry-run --pair EUR/USD --timeframe 5m --steps 1
```

The command reads candles and live prices, calculates the GBP account
conversion when needed, and simulates any signal into the local SQLite ledger.
It never calls the OANDA order endpoint. Keep the token in the terminal only;
do not paste it into chat or commit it to the repository.

## Historical backtest

Run a read-only EMA backtest using OANDA candles:

```powershell
python -m freqtrade.forex backtest --pair EUR/USD --timeframe 5m --count 500 --stop-pips 10
```

The result includes candle count, starting/ending balance, net P/L, trade
count, win rate, observed spread, quote conversion, and total costs. Add
explicit assumptions for execution costs when doing a conservative run:

```powershell
python -m freqtrade.forex backtest --pair EUR/USD --timeframe 5m --count 500 `
	--stop-pips 10 --slippage 0.00001 --financing-rate-per-day 0.00001
```

The backtest uses the account currency balance and does not place orders.

## Hyperopt

Optimize the EMA and stop parameters with the same execution-cost assumptions:

```powershell
python -m freqtrade.forex hyperopt --pair EUR/USD --timeframe 5m --count 500 `
	--slippage 0.00001 --financing-rate-per-day 0.00001
```

The optimizer ranks candidates by net P/L with a drawdown penalty. Treat the
best result as a research candidate only; validate it on a separate time range
before using it in dry-run.

## Ledger backup and recovery

Create a consistent SQLite backup while the process may still be writing:

```powershell
python -m freqtrade.forex paper-backup `
	--ledger user_data/oanda/paper.sqlite `
	--destination user_data/oanda/backups/paper.sqlite
```

The backup uses SQLite online backup and replaces the destination atomically.
For recovery, stop the worker, verify the backup with `paper-report`, restore it
to a new ledger path, and only then resume the worker. Keep one backup outside
the application data directory.

## Emergency close

During a Practice or Live incident, activate the risk kill switch and stop new
strategy iterations first. Reconcile account and open positions, then close
each instrument through the broker-backed close path with a unique client order
id. Verify zero net units at the broker before restarting. Do not delete the
ledger or issue an untracked second close after a timeout.
