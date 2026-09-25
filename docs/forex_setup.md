# Forex Setup

The Ubuntu installation path is non-interactive. It installs the mandatory
Python dependencies, creates the virtual environment, creates the `user_data`
directories, and writes a credential-free config skeleton.

## Ubuntu

From the repository root:

```bash
chmod +x setup.sh
./setup.sh --install-forex
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
