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
environment variable `OANDA_LIVE_CONFIRM=1`. Keep execution mode on Dry-run
until the Live configuration has been separately reviewed.

For a user-level systemd service, enable the Live setup gate only when needed:

```bash
mkdir -p ~/.config/systemd/user/freqtrade-forex.service.d
printf '[Service]\nEnvironment=OANDA_LIVE_CONFIRM=1\n' > ~/.config/systemd/user/freqtrade-forex.service.d/live-confirm.conf
systemctl --user daemon-reload
systemctl --user restart freqtrade-forex
```

This gate permits saving a Live account configuration; setup still restricts
execution to Dry-run.

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

