from fastapi.testclient import TestClient

from freqtrade.forex import api as forex_api
from freqtrade.forex.api import create_app


def test_execution_status_uses_saved_server_configuration(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("OANDA_ENVIRONMENT", raising=False)
    monkeypatch.delenv("OANDA_EXECUTION_MODE", raising=False)
    monkeypatch.setattr(
        forex_api,
        "load_forex_config",
        lambda: {
            "exchange": {
                "oanda_environment": "practice",
                "oanda_execution_mode": "dry_run",
            }
        },
    )

    with TestClient(create_app(tmp_path / "execution-status.sqlite")) as client:
        response = client.get("/api/v1/system/execution-status")

    assert response.status_code == 200
    assert response.json() == {
        "environment": "practice",
        "environmentSource": "config",
        "executionMode": "dry_run",
        "executionModeSource": "config",
    }


def test_execution_status_prefers_server_environment_over_config(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OANDA_ENVIRONMENT", "live")
    monkeypatch.setenv("OANDA_EXECUTION_MODE", "live")
    monkeypatch.setattr(
        forex_api,
        "load_forex_config",
        lambda: {
            "exchange": {
                "oanda_environment": "practice",
                "oanda_execution_mode": "dry_run",
            }
        },
    )

    with TestClient(create_app(tmp_path / "execution-status.sqlite")) as client:
        response = client.get("/api/v1/system/execution-status")

    assert response.status_code == 200
    assert response.json() == {
        "environment": "live",
        "environmentSource": "environment",
        "executionMode": "live",
        "executionModeSource": "environment",
    }


def test_execution_status_rejects_invalid_server_mode(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OANDA_EXECUTION_MODE", "production")
    monkeypatch.setattr(forex_api, "load_forex_config", lambda: {"exchange": {}})

    with TestClient(create_app(tmp_path / "execution-status.sqlite")) as client:
        response = client.get("/api/v1/system/execution-status")

    assert response.status_code == 500
    assert response.json()["detail"] == "Invalid server OANDA execution mode configuration"


def test_execution_status_rejects_invalid_server_environment(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OANDA_ENVIRONMENT", "staging")
    monkeypatch.delenv("OANDA_EXECUTION_MODE", raising=False)
    monkeypatch.setattr(forex_api, "load_forex_config", lambda: {"exchange": {}})

    with TestClient(create_app(tmp_path / "execution-status.sqlite")) as client:
        response = client.get("/api/v1/system/execution-status")

    assert response.status_code == 500
    assert response.json()["detail"] == "Invalid server OANDA environment configuration"
