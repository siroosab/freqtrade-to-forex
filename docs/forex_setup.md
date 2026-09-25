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

Start the local API after installation:

```bash
source .venv/bin/activate
python -m uvicorn freqtrade.forex.api:app --host 0.0.0.0 --port 8090
```

Open `http://SERVER_IP:8090/setup` in a browser. Enter the OANDA Practice
token, account ID, execution mode, instruments, and risk fraction there. The
credentials are sent to the server-side API and are never placed in the
installation command or browser URL.

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

Run the update from `~/forex_bot` while the virtual environment is not active:

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

