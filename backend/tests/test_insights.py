import secrets
from datetime import UTC, datetime, timedelta

from app.db import SessionLocal
from app.models import AllowedProvider, UsageLog, VirtualKey


def _key(label: str = "K") -> int:
    with SessionLocal() as db:
        vk = VirtualKey(
            label=label,
            key_hash=secrets.token_hex(32),
            key_prefix="vk_" + secrets.token_hex(4),
            allowed_providers=[AllowedProvider(provider="openai")],
        )
        db.add(vk)
        db.commit()
        return vk.id


def _rows(key_id: int, when: datetime, n: int, cost: float, tokens: int = 400) -> None:
    with SessionLocal() as db:
        for _ in range(n):
            db.add(
                UsageLog(
                    key_id=key_id,
                    request_id=secrets.token_hex(16),
                    provider="openai",
                    model="gpt-4o",
                    prompt_tokens=tokens // 2,
                    completion_tokens=tokens // 2,
                    total_tokens=tokens,
                    cost=cost,
                    cost_source="configured",
                    simulated=True,
                    mode="simulated",
                    latency_ms=500,
                    status="success",
                    ts=when,
                )
            )
        db.commit()


def test_insights_admin_gated(client):
    assert client.get("/api/insights/alerts").status_code == 403
    assert client.post("/api/insights/demo-spike").status_code == 403


def test_no_alerts_when_flat(client, admin):
    kid = _key()
    now = datetime.now(UTC)
    for d in range(1, 9):
        _rows(kid, now - timedelta(days=d, hours=2), 3, 0.01)
    _rows(kid, now - timedelta(hours=2), 3, 0.01)  # same rate today
    assert client.get("/api/insights/alerts", headers=admin).json()["alerts"] == []


def test_per_key_cost_spike_detected(client, admin):
    kid = _key("Engineering")
    now = datetime.now(UTC)
    for d in range(1, 8):  # baseline: ~$0.03/day
        _rows(kid, now - timedelta(days=d, hours=2), 3, 0.01)
    _rows(kid, now - timedelta(hours=3), 10, 0.02)  # today: $0.20  -> ~6x

    alerts = client.get("/api/insights/alerts", headers=admin).json()["alerts"]
    spike = next(a for a in alerts if a["type"] == "cost_spike")
    assert spike["scope"]["kind"] == "key"
    assert spike["scope"]["key_id"] == kid
    assert spike["pct_change"] >= 100
    assert spike["severity"] == "critical"


def test_investigate_contributors_and_related_query(client, admin):
    kid = _key("Engineering")
    now = datetime.now(UTC)
    for d in range(1, 8):
        _rows(kid, now - timedelta(days=d, hours=2), 3, 0.01)
    _rows(kid, now - timedelta(hours=3), 12, 0.02)

    inv = client.get(f"/api/insights/alerts/cost_spike:key:{kid}", headers=admin).json()
    assert inv["headline"]["pct_change"] >= 100
    assert inv["contributors"]
    top = inv["contributors"][0]
    assert top["kind"] in {"key", "model"}
    assert "Engineering" in inv["summary"]
    assert inv["related_query"]["key_id"] == kid
    assert "start" in inv["related_query"] and "end" in inv["related_query"]


def test_demo_spike_endpoint_and_alerts(client, admin):
    r = client.post("/api/insights/demo-spike", headers=admin)
    assert r.status_code == 200 and r.json()["inserted"] > 0
    alerts = client.get("/api/insights/alerts", headers=admin).json()["alerts"]
    assert any(a["type"] == "cost_spike" for a in alerts)
    assert any(a["type"] == "token_spike" for a in alerts)


def test_requests_time_filter(client, admin, make_key):
    k = make_key(allowed_providers=["openrouter"])
    hdr = {"Authorization": f"Bearer {k['key']}"}
    client.post(
        "/v1/proxy/chat",
        json={"provider": "openrouter", "model": "gpt-4o", "prompt": "hi"},
        headers=hdr,
    )
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    empty = client.get("/api/requests", params={"start": future}, headers=admin).json()
    assert empty["items"] == []
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    some = client.get("/api/requests", params={"start": past}, headers=admin).json()
    assert len(some["items"]) >= 1
