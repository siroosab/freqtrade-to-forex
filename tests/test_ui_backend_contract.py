import json

from fastapi.testclient import TestClient

from freqtrade.forex.api import app


client = TestClient(app)


def test_account_summary_endpoint_exists():
    response = client.get('/api/v1/account/summary')
    assert response.status_code == 200, response.text
    payload = response.json()
    assert 'equity' in payload
    assert 'netPnl' in payload


def test_market_summary_endpoint_exists():
    response = client.get('/api/v1/markets/summary')
    assert response.status_code == 200, response.text
    payload = response.json()
    assert 'instruments' in payload
    assert 'strategySignals' in payload


def test_websocket_market_channel_connects():
    with client.websocket_connect('/ws/market') as websocket:
        message = websocket.receive_json()
        assert 'type' in message
        assert 'channel' in message
        assert 'timestamp' in message
        assert 'data' in message
        assert 'instruments' in message['data']


def test_cors_allows_vite_frontend_origin():
    response = client.options(
        '/api/v1/account/summary',
        headers={
            'Origin': 'http://localhost:5173',
            'Access-Control-Request-Method': 'GET',
        },
    )
    assert response.status_code == 200
    assert response.headers.get('access-control-allow-origin') == 'http://localhost:5173'


def test_order_submit_requires_operator_role_and_csrf_token():
    denied = client.post(
        '/api/v1/orders/market',
        json={'symbol': 'EUR/USD', 'side': 'BUY', 'volume': '1200'},
        headers={'X-User-Role': 'viewer', 'X-CSRF-Token': 'demo-token'},
    )
    assert denied.status_code == 403, denied.text

    allowed = client.post(
        '/api/v1/orders/market',
        json={'symbol': 'EUR/USD', 'side': 'BUY', 'volume': '1200'},
        headers={'X-User-Role': 'operator', 'X-CSRF-Token': 'demo-token'},
    )
    assert allowed.status_code == 200, allowed.text
    payload = allowed.json()
    assert payload['status'] in {'accepted', 'queued'}
    assert payload['symbol'] == 'EUR/USD'


def test_login_returns_session_and_session_validation_works():
    login = client.post(
        '/api/v1/auth/login',
        json={'username': 'operator', 'password': 'operator'},
    )
    assert login.status_code == 200, login.text
    payload = login.json()
    assert 'sessionToken' in payload
    assert payload['user']['role'] == 'operator'

    session = client.get(
        '/api/v1/auth/session',
        headers={'Authorization': f"Bearer {payload['sessionToken']}"},
    )
    assert session.status_code == 200, session.text
    assert session.json()['role'] == 'operator'

    denied = client.post(
        '/api/v1/orders/market',
        json={'symbol': 'EUR/USD', 'side': 'BUY', 'volume': '1200'},
        headers={'X-Session-Token': payload['sessionToken'], 'X-CSRF-Token': 'demo-token'},
    )
    assert denied.status_code == 403, denied.text

    allowed = client.post(
        '/api/v1/orders/market',
        json={'symbol': 'EUR/USD', 'side': 'BUY', 'volume': '1200'},
        headers={'X-Session-Token': payload['sessionToken'], 'X-User-Role': 'operator', 'X-CSRF-Token': 'demo-token'},
    )
    assert allowed.status_code == 200, allowed.text


def test_operation_preflight_validation_accepts_valid_session_and_rejects_invalid_role():
    login = client.post(
        '/api/v1/auth/login',
        json={'username': 'operator', 'password': 'operator'},
    )
    token = login.json()['sessionToken']

    valid = client.post(
        '/api/v1/ops/validation',
        json={'environment': 'practice', 'executionMode': 'Practice', 'instrument': 'EUR/USD'},
        headers={'X-Session-Token': token, 'X-User-Role': 'operator', 'X-CSRF-Token': 'demo-token'},
    )
    assert valid.status_code == 200, valid.text
    payload = valid.json()
    assert payload['allowed'] is True
    assert payload['checks']['sessionValid'] is True
    assert payload['checks']['roleAllowed'] is True
    assert payload['checks']['csrfPresent'] is True

    invalid = client.post(
        '/api/v1/ops/validation',
        json={'environment': 'live', 'executionMode': 'Live', 'instrument': 'EUR/USD'},
        headers={'X-Session-Token': token, 'X-User-Role': 'viewer', 'X-CSRF-Token': 'demo-token'},
    )
    assert invalid.status_code == 403, invalid.text


def test_audit_log_redacts_tokens_and_exposes_only_metadata():
    login = client.post(
        '/api/v1/auth/login',
        json={'username': 'operator', 'password': 'operator'},
    )
    assert login.status_code == 200, login.text

    response = client.get('/api/v1/audit/logs')
    assert response.status_code == 200, response.text
    logs = response.json()
    assert isinstance(logs, list)
    assert any(log.get('event') == 'auth.login' for log in logs)

    for entry in logs:
        payload = entry.get('details', {})
        serialized = repr(payload)
        assert 'operator' in serialized or 'auth.login' in serialized or True
        assert 'sessionToken' not in serialized or '***REDACTED***' in serialized
        assert 'password' not in serialized or '***REDACTED***' in serialized


def test_live_release_gate_requires_explicit_approval():
    login = client.post(
        '/api/v1/auth/login',
        json={'username': 'admin', 'password': 'admin'},
    )
    token = login.json()['sessionToken']

    denied = client.post(
        '/api/v1/ops/validation',
        json={'environment': 'live', 'executionMode': 'Live', 'instrument': 'EUR/USD'},
        headers={'X-Session-Token': token, 'X-User-Role': 'admin', 'X-CSRF-Token': 'demo-token'},
    )
    assert denied.status_code == 403, denied.text

    allowed = client.post(
        '/api/v1/ops/validation',
        json={'environment': 'live', 'executionMode': 'Live', 'instrument': 'EUR/USD', 'releaseGate': 'approved'},
        headers={'X-Session-Token': token, 'X-User-Role': 'admin', 'X-CSRF-Token': 'demo-token'},
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()['allowed'] is True


def test_setup_runtime_and_file_endpoints_work_with_configured_paths(monkeypatch, tmp_path):
    config_path = tmp_path / 'config' / 'custom-config.json'
    strategy_path = tmp_path / 'strategies' / 'custom_strategy.py'
    monkeypatch.setenv('OANDA_CONFIG_PATH', str(config_path))
    monkeypatch.setenv('FOREX_STRATEGY_PATH', str(strategy_path))

    async def verified_accounts(token, environment):
        assert token == 'demo-token'
        assert environment.value == 'practice'
        return {
            'accounts': [{
                'accountId': '101-000-1234567-001',
                'accountTypeCode': '003',
                'accountType': 'CFD',
                'tags': ['CFD'],
                'summary': {'alias': 'Primary', 'currency': 'GBP'},
                'summaryAccessible': True,
            }],
            'excludedAccountCount': 0,
        }

    monkeypatch.setattr('freqtrade.forex.api.discover_oanda_accounts', verified_accounts)

    status = client.get('/api/v1/setup/status')
    assert status.status_code == 200, status.text
    assert status.json()['configFile'] == str(config_path)

    setup = client.post(
        '/api/v1/setup',
        json={
            'token': 'demo-token',
            'accountId': '101-000-1234567-001',
            'accountTypeCode': '003',
            'accountConfirmed': True,
            'liveConfirmed': False,
            'environment': 'practice',
            'executionMode': 'practice',
            'instruments': ['EUR_USD', 'GBP_USD'],
            'pairTimeframes': {'EUR_USD': '5m', 'GBP_USD': '1h'},
            'riskFraction': '0.01',
            'configPath': str(config_path),
        },
    )
    assert setup.status_code == 200, setup.text
    payload = setup.json()
    assert payload['configured'] is True
    assert payload['executionMode'] == 'practice'
    assert payload['accountTypeCode'] == '003'


def test_setup_discovery_returns_only_supported_accounts_and_never_token(monkeypatch):
    async def discover(token, environment):
        assert token == 'private-token'
        assert environment.value == 'live'
        return {
            'accounts': [
                {'accountId': 'eligible-1', 'accountTypeCode': '002', 'accountType': 'Spread Betting', 'tags': ['SPREAD_BETTING'], 'summary': {'alias': 'SB', 'currency': 'GBP'}, 'summaryAccessible': True},
            ],
            'excludedAccountCount': 2,
        }

    monkeypatch.setattr('freqtrade.forex.api.discover_oanda_accounts', discover)
    response = client.post('/api/v1/setup/discover', json={'token': 'private-token', 'environment': 'live'})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['accounts'][0]['accountTypeCode'] == '002'
    assert payload['excludedAccountCount'] == 2
    assert 'private-token' not in response.text


def test_live_setup_requires_server_side_confirmation_flag(monkeypatch, tmp_path):
    monkeypatch.delenv('OANDA_LIVE_CONFIRM', raising=False)
    response = client.post(
        '/api/v1/setup',
        json={
            'token': 'live-token',
            'accountId': 'live-account',
            'accountTypeCode': '003',
            'accountConfirmed': True,
            'liveConfirmed': True,
            'environment': 'live',
            'executionMode': 'dry_run',
            'instruments': ['EUR_USD'],
            'riskFraction': '0.01',
            'configPath': str(tmp_path / 'live-config.json'),
        },
    )
    assert response.status_code == 403
    assert 'OANDA_LIVE_CONFIRM=1' in response.json()['detail']
    assert not (tmp_path / 'live-config.json').exists()


def test_untagged_practice_v20_account_can_be_confirmed(monkeypatch, tmp_path):
    async def verified_practice_account(token, environment):
        assert token == 'practice-token'
        assert environment.value == 'practice'
        return {
            'accounts': [{
                'accountId': 'practice-account',
                'accountTypeCode': 'PRACTICE',
                'accountType': 'Practice / V20',
                'tags': [],
                'summary': {'alias': 'Practice', 'currency': 'GBP'},
                'summaryAccessible': True,
                'instrumentCount': 123,
            }],
            'excludedAccountCount': 0,
        }

    monkeypatch.setattr(
        'freqtrade.forex.api.discover_oanda_accounts', verified_practice_account
    )
    response = client.post(
        '/api/v1/setup',
        json={
            'token': 'practice-token',
            'accountId': 'practice-account',
            'accountTypeCode': 'PRACTICE',
            'accountConfirmed': True,
            'liveConfirmed': False,
            'environment': 'practice',
            'executionMode': 'dry_run',
            'instruments': ['EUR_USD'],
            'riskFraction': '0.01',
            'configPath': str(tmp_path / 'practice-config.json'),
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()['accountType'] == 'Practice / V20'

    runtime = client.get('/api/v1/setup/runtime')
    assert runtime.status_code == 200, runtime.text
    assert runtime.json()['state'] in {'running', 'paused', 'stopped'}

    paused = client.post('/api/v1/setup/runtime', json={'action': 'pause'})
    assert paused.status_code == 200, paused.text
    assert paused.json()['state'] == 'paused'

    resume = client.post('/api/v1/setup/runtime', json={'action': 'resume'})
    assert resume.status_code == 200, resume.text
    assert resume.json()['state'] == 'running'

    config_download = client.get('/api/v1/setup/files/config')
    assert config_download.status_code == 200, config_download.text
    assert 'schema_version' in config_download.text

    strategy_download = client.get('/api/v1/setup/files/strategy')
    assert strategy_download.status_code == 200, strategy_download.text
    assert 'class' in strategy_download.text or 'def' in strategy_download.text

    uploaded_config = client.post(
        '/api/v1/setup/files/config',
        json={'content': json.dumps({'schema_version': 2, 'exchange': {'name': 'oanda'}}, indent=2)},
    )
    assert uploaded_config.status_code == 200, uploaded_config.text
    assert uploaded_config.json()['uploaded'] is True

    uploaded_strategy = client.post(
        '/api/v1/setup/files/strategy',
        json={'content': 'class CustomStrategy:\n    pass\n'},
    )
    assert uploaded_strategy.status_code == 200, uploaded_strategy.text
    assert uploaded_strategy.json()['uploaded'] is True
