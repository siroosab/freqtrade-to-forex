# Forex Setup

The Ubuntu installation path is non-interactive. It installs the mandatory
Python dependencies, creates the virtual environment, creates the `user_data`
directories, and writes a credential-free config skeleton.

## Ubuntu

Clone the project into `~/forex_bot` and run the commands from that directory:

```bash
git clone https://github.com/siroosab/freqtrade-to-forex.git ~/forex_bot
cd ~/forex_bot
chmod +x setup.sh
./setup.sh --install-forex
```

For this private repository, HTTPS requires your GitHub username and a
Personal Access Token. Do not use your GitHub account password. Alternatively,
create an SSH key on the VPS, add its public key to GitHub, verify it with
`ssh -T git@github.com`, and clone with:

```bash
git clone git@github.com:siroosab/freqtrade-to-forex.git ~/forex_bot
```

The command does not ask for broker credentials. It leaves the config at
`user_data/config.json` and starts no network service.

## Initial installation or failed installation

Use these commands after the first clone, or when an earlier installation
stopped before `.venv` was created completely:

```bash
cd ~/forex_bot
git pull --ff-only origin stable
chmod +x setup.sh
./setup.sh --install-forex
```

This creates `.venv`, installs the dependencies, and creates the initial
configuration. Do not use this path for routine updates because it recreates
the virtual environment.

Start the local API after installation:

```bash
source .venv/bin/activate
python -m uvicorn freqtrade.forex.api:app --host 0.0.0.0 --port 8090
```

The API serves the production React build from the same port, so a separate
Vite process is not required on the VPS.

Open `http://SERVER_IP:8090/setup` in a browser. Choose Practice or Live and
enter the token for that environment. Account discovery uses GET requests only;
for Live it lists supported CFD (`003`) and Spread Betting (`002`) accounts.
Practice accounts whose API response omits type tags are listed as
`Practice / V20` only when both the account summary and tradable instruments
are readable. Select an account and confirm it before saving. Other accounts
cannot be selected. The token is not saved until the confirmed setup is
submitted and is never placed in the browser URL.

Live requires both the explicit confirmation in the page and the server
environment variable `OANDA_LIVE_CONFIRM=1`. In this project, the only setup
modes are Practice and Live; there is no third execution selector.

## Pair strategies and research runs

In Setup, assign each configured pair its own candle timeframe and strategy
class. Python strategy uploads accept multiple `.py` files; each is syntax
checked and must define a unique class inheriting from `IStrategy`. Files are
saved separately under `user_data/strategies`. Upload validation does not
execute arbitrary strategy code; imports and constructors are checked when the
strategy is first selected for a run.

Backtests and strategy Hyperopt can select a strategy class independently for each
pair and timeframe. Freqtrade `@informative` callbacks for the same pair are
loaded from OANDA at their declared higher timeframe and merged using
Freqtrade's causal timeframe merge. Strategy Hyperopt samples declared
Freqtrade parameters; custom strategies without optimizable parameters are
rejected. Approval, reports, and scheduled Hyperopt retain their pair, class,
and timeframe scope.

Only the exact approved pair/strategy/timeframe revision may produce runtime
signals. The Review approval stores an active revision snapshot in
`pair_approved_revisions`, including its Hyperopt parameters. The standalone
`dry-run` command refuses to start a pair with no snapshot or if its configured
strategy/timeframe no longer matches that snapshot. Changing a pair's strategy
or timeframe in Setup invalidates its approval; approve that configuration
again before running it. `--strategy` and `--timeframe` on `dry-run` can check
the approved values but cannot override them. Hyperopt and Backtest remain
available for unapproved research.

The Hyperopt Scope panel accepts history in candles, calendar days, or an
inclusive date range. Downloading a date range selects it as the input for
Hyperopt and Backtest; their reports show the actual number of candles loaded
from that range rather than the separate candle-count default. Cached ranges
can be inspected by their covered UTC dates and cleared per pair/timeframe.
Downloaded ranges are reused when the requested interval matches the cached
coverage. Requests ending today are capped at one minute before the current UTC
time so OANDA does not reject an end timestamp that is still in the future; the
effective end is shown after the download.

Manual and scheduled Hyperopt expose a CPU worker-process setting. Its default
uses all CPUs available to the server (including container CPU limits), and
each process evaluates separate candidates. Reduce the worker count if Hyperopt
competes with live trading for CPU or memory. This parallelism applies to
strategy Hyperopt only; the live trading loop remains event-driven and is not
made multi-process by this setting.

Inspect the currently selected pair settings and approval state without broker
credentials:

```bash
python -m freqtrade.forex show-timeframes
python -m freqtrade.forex show-timeframes --pair EUR/USD
```

`ForexEmaStrategy` demonstrates a four-hour helper EMA. Its `@informative("4h")`
callback computes and merges the closed-candle EMA for Hyperopt and Backtest.
During live-price `dry-run` only, it also requests the forming four-hour candle
and recalculates the EMA for the latest base-timeframe row as
`alpha * current_close + (1 - alpha) * previous_ema`, with
`alpha = 2 / (period + 1)`. Historical candles and prior rows are unchanged;
the 4h EMA is used as a trend filter for entries. This strategy's declared
four-hour informative timeframe must be equal to or higher than its selected
base timeframe, so selecting a base timeframe above 4h is rejected by the
strategy loader.

For a user-level systemd service, enable the Live setup gate only when needed:

```bash
mkdir -p ~/.config/systemd/user/freqtrade-forex.service.d
printf '[Service]\nEnvironment=OANDA_LIVE_CONFIRM=1\n' > ~/.config/systemd/user/freqtrade-forex.service.d/live-confirm.conf
systemctl --user daemon-reload
systemctl --user restart freqtrade-forex
```

This gate permits saving a Live account configuration; the only setup modes
remain Practice and Live.

## Forex CLI backtest and Hyperopt

The standalone Forex CLI uses OANDA's read-only market-data endpoints for
downloads, strategy backtests, and strategy-parameter Hyperopt. It reads
credentials from the saved Forex config or the `OANDA_TOKEN` and
`OANDA_ACCOUNT_ID` environment variables. Data is cached by pair, timeframe,
and requested range/count in `user_data/data/oanda/candles.json` by default.
A cache miss downloads data as part of the backtest or Hyperopt command;
`--refresh-data` replaces a cache entry with newly fetched candles.
The API and Hyperopt page use the same default cache path; set
`OANDA_CANDLE_CACHE_PATH` to override it.

```bash
python -m freqtrade.forex download-data --pair EUR/USD --timeframe 5m --count 5000
python -m freqtrade.forex hyperopt --pair EUR/USD --timeframe 5m --count 5000 --epochs 90 --strategy ForexEmaStrategy
python -m freqtrade.forex backtest --pair EUR/USD --timeframe 5m --count 5000 --strategy ForexEmaStrategy
python -m freqtrade.forex cache-clear --pair EUR/USD --timeframe 5m
```

Hyperopt prints progress for each candidate and a ranked final table. It
optimizes the declared Freqtrade strategy parameters and its `minimal_roi`
schedule using a held-out validation segment. Reports are saved under
`user_data/hyperopt_results/` by default; use `--results-dir` to choose another
directory. Backtest runs the selected strategy against the requested candle
window and reports trade, drawdown, and cost summaries.

Use `--strategy <ClassName>` to load a custom class from
`user_data/strategies`; classes in another directory can be discovered by
setting `FOREX_STRATEGIES_DIR` to that folder. The built-in EMA strategy can
also be selected with `--strategy ema`. To bypass and replace cached candles,
add `--refresh-data`. `cache-clear` accepts optional `--pair` and `--timeframe`
filters; without them, it clears the entire candle cache.
The non-interactive config-only step can also be rerun safely:

```bash
./setup.sh --config
```

If `user_data/config.json` already exists, it is preserved. To inspect the
available commands:

```bash
./setup.sh --help
```

## Updating an existing installation

When the installation is already complete and you only need the latest project
changes, run this from `~/forex_bot` while the virtual environment is not
active:

```bash
cd ~/forex_bot
./setup.sh --update-forex
```

The command fast-forwards the `stable` branch, updates Python dependencies, and
refreshes the editable installation. It does not recreate `.venv` and it
preserves `user_data/config.json`. Commit or stash local changes before running
the update. Restart the user systemd service after the update if it is running:

```bash
systemctl --user restart freqtrade-forex
```
