from prometheus_client import CollectorRegistry

from data import metrics


def test_grafana_push_can_be_disabled_for_a_pipeline_run(monkeypatch):
    monkeypatch.setenv("GRAFANA_PUSH_ENABLED", "false")
    monkeypatch.setenv(
        "GRAFANA_REMOTE_WRITE_URL", "https://example.invalid/api/prom/push"
    )
    monkeypatch.setenv("GRAFANA_METRICS_ID", "123456")
    monkeypatch.setenv("GRAFANA_TOKEN", "expired-token")

    def unexpected_build(*args, **kwargs):
        raise AssertionError("disabled Grafana push should not build a request")

    monkeypatch.setattr(metrics, "_build_write_request", unexpected_build)

    metrics.push_to_grafana(CollectorRegistry(), job="test")
