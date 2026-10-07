from types import SimpleNamespace

from fastapi.testclient import TestClient

from freqtrade.forex import api as forex_api
from freqtrade.forex.api import create_app


def test_system_metrics_api_reports_cpu_and_memory(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(forex_api.psutil, "cpu_percent", lambda interval=None: 27.5)
    monkeypatch.setattr(
        forex_api.psutil,
        "virtual_memory",
        lambda: SimpleNamespace(percent=64.0, used=8 * 1024**3, total=16 * 1024**3),
    )

    with TestClient(create_app(tmp_path / "metrics.sqlite")) as client:
        response = client.get("/api/v1/system/metrics")

    assert response.status_code == 200
    assert response.json()["cpuPercent"] == 27.5
    assert response.json()["memoryPercent"] == 64.0
    assert response.json()["memoryUsed"] == 8 * 1024**3
    assert response.json()["memoryTotal"] == 16 * 1024**3
    assert response.json()["timestamp"]
