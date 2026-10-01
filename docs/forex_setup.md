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

Backtests and AI Hyperopt can select a strategy class independently for each
pair and timeframe. Freqtrade `@informative` callbacks for the same pair are
loaded from OANDA at their declared higher timeframe and merged using
Freqtrade's causal timeframe merge. Generic AI Hyperopt samples declared
Freqtrade parameters; custom strategies without optimizable parameters are
rejected. Approval, reports, and scheduled Hyperopt retain their pair, class,
and timeframe scope. The standalone `dry-run` command uses the saved pair
strategy/timeframe mappings and runs each pair on its own cadence.

For a user-level systemd service, enable the Live setup gate only when needed:

```bash
mkdir -p ~/.config/systemd/user/freqtrade-forex.service.d
printf '[Service]\nEnvironment=OANDA_LIVE_CONFIRM=1\n' > ~/.config/systemd/user/freqtrade-forex.service.d/live-confirm.conf
systemctl --user daemon-reload
systemctl --user restart freqtrade-forex
```

This gate permits saving a Live account configuration; the only setup modes
remain Practice and Live.

## Forex CLI research

The standalone Forex CLI uses OANDA's read-only market-data endpoints for
downloads, backtests, and AI Hyperopt. It reads credentials from the saved
Forex config or the `OANDA_TOKEN` and `OANDA_ACCOUNT_ID` environment variables.
Data is cached by pair, timeframe, and requested range/count in
`user_data/data/oanda/candles.json` by default. A cache miss downloads the data
as part of the backtest or Hyperopt command; `--refresh-data` replaces a cache
entry with newly fetched candles.

```bash
python -m freqtrade.forex download-data --pair EUR/USD --timeframe 5m --count 5000
python -m freqtrade.forex backtest --pair EUR/USD --timeframe 5m --count 5000
python -m freqtrade.forex hyperopt --pair EUR/USD --timeframe 5m --count 5000 --epochs 90 --freqaimodel LightGBMRegressor
python -m freqtrade.forex hyperopt --pair EUR/USD --timeframe 5m --count 5000 --epochs 90 --strategy ForexEmaStrategy
python -m freqtrade.forex hyperopt --pair EUR/USD --timeframe 5m --count 5000 --epochs 90 --strategy ForexEmaStrategy --freqaimodel LightGBMRegressor
python -m freqtrade.forex backtest --pair EUR/USD --timeframe 5m --count 5000 --strategy ForexEmaStrategy --freqaimodel LightGBMRegressor
python -m freqtrade.forex backtest --pair EUR/USD --timeframe 5m --count 5000 --ai-model user_data/hyperopt_results/EUR_USD_5m_ai.json
python -m freqtrade.forex cache-clear --pair EUR/USD --timeframe 5m
```

Hyperopt prints a compact candidate table for every epoch and a ranked final
table. With `--strategy` and `--freqaimodel` together, the selected strategy's
FreqAI `%` feature hooks and `&` target are used to train the model once; its
`self.freqai.start()` receives cached model predictions during strategy
parameter Hyperopt. The class must declare optimizable Freqtrade parameters
and use its target in entry/exit logic. Without `--strategy`, the standalone
AI baseline workflow is used. `--freqaimodel` selects
`LightGBMRegressor`, `LightGBMClassifier`, or the deterministic
`ForexAIStrategyBaseline`.

FreqAI settings are read from the `freqai` section of `user_data/config.json`.
The `identifier`, training/backtest window, feature periods, shifted candles,
label horizon, chronological `data_split_parameters.test_size`, and supported
LightGBM `model_training_parameters` are applied. A saved model is reused only
when the identifier, pair/timeframe, data/feature hashes, training window, and
model parameters match; changing these retrains it. Hyperopt then optimizes
strategy parameters on persisted validation predictions, leaving out-of-sample
metrics as a separate check. Multi-timeframe/correlation features and PCA are
not yet supported by the native Forex CLI and are rejected rather than ignored.
The LightGBM booster, prediction cache, training metrics, feature schema,
optimized strategy parameters, and candidate report are saved under
`user_data/hyperopt_results/`. Backtest defaults to the AI baseline;
`--strategy ema` selects the original EMA strategy. To bypass and replace
cached candles on either research command, add `--refresh-data`.
`cache-clear` accepts optional `--pair` and `--timeframe` filters; without them,
it clears the entire candle cache.

Use `--strategy <ClassName>` to load a custom class from `user_data/strategies`;
the built-in `ForexEmaStrategy` also supports the combined FreqAI mode.
Without `--freqaimodel`, this performs ordinary declared-parameter strategy
Hyperopt. When combined with `--freqaimodel`, the class must implement the
FreqAI feature hooks and `set_freqai_targets()`, call `self.freqai.start()` in
`populate_indicators()`, and consume its `&` target in entry/exit logic.
Classes in another directory can be discovered by setting `FOREX_STRATEGIES_DIR`
to that folder.

The model-backed `backtest` command uses the same model identifier/cache and
the best strategy parameters from the matching Hyperopt report. It reports
only the out-of-sample window; use the same `--strategy`, `--freqaimodel`,
pair, timeframe, model directory, and FreqAI config as Hyperopt to reuse its
artifacts. If no trained model exists, the command trains one before testing.

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

