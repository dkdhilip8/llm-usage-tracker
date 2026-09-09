"""True multi-tenant isolation: one workspace never reads another's data, on
every endpoint, enforced server-side."""

import pytest

OR_MODEL = "meta-llama/llama-3.3-70b-instruct"

READ_ENDPOINTS = [
    "/api/usage/summary",
    "/api/usage/timeseries",
    "/api/usage/by-key",
    "/api/usage/by-model",
    "/api/requests",
]


@pytest.fixture()
def two_workspaces(new_admin, mock_provider):
    a, _ = new_admin("Acme")
    b, _ = new_admin("Globex")
    # Acme configures a provider key, mints a live key, drives one real request
    a.put("/api/workspace/providers/openrouter/key", json={"api_key": "sk-fake-or-000000"})
    k = a.post(
        "/api/keys",
        json={"label": "acme-key", "allowed_providers": ["openrouter"], "allow_live": True},
    ).json()
    r = a.post(
        "/v1/proxy/chat",
        json={"provider": "openrouter", "model": OR_MODEL, "prompt": "acme only"},
        headers={"Authorization": f"Bearer {k['key']}"},
    )
    assert r.status_code == 200, r.text
    return a, b, k


def test_acme_sees_its_own_traffic(two_workspaces):
    a, _b, _k = two_workspaces
    assert a.get("/api/usage/summary").json()["total_requests"] == 1
    assert len(a.get("/api/requests").json()["items"]) == 1
    assert [row["label"] for row in a.get("/api/usage/by-key").json()] == ["acme-key"]
    assert [row["label"] for row in a.get("/api/keys").json()] == ["acme-key"]


def test_globex_sees_nothing_of_acme(two_workspaces):
    _a, b, k = two_workspaces
    assert b.get("/api/usage/summary").json()["total_requests"] == 0
    assert b.get("/api/requests").json()["items"] == []
    assert b.get("/api/usage/by-key").json() == []
    assert b.get("/api/usage/by-model").json() == []
    assert b.get("/api/keys").json() == []
    # can't reach Acme's key by id either
    assert b.patch(f"/api/keys/{k['id']}", json={"allow_live": False}).status_code == 404
    assert b.delete(f"/api/keys/{k['id']}").status_code == 404


@pytest.mark.parametrize("path", READ_ENDPOINTS)
def test_read_endpoints_are_workspace_scoped(two_workspaces, path):
    _a, b, _k = two_workspaces
    r = b.get(path)
    assert r.status_code == 200
    payload = r.json()
    rows = payload["items"] if isinstance(payload, dict) and "items" in payload else payload
    if path == "/api/usage/summary":
        assert payload["total_requests"] == 0
    else:
        assert rows == []


def test_workspace_payload_does_not_leak_members(two_workspaces, new_member):
    a, b, _k = two_workspaces
    new_member(a, username="acme-person")
    b_members = [m["username"] for m in b.get("/api/workspace").json().get("members", [])]
    assert "acme-person" not in b_members
    assert all(name.startswith("admin") for name in b_members)  # only Globex's own admin
