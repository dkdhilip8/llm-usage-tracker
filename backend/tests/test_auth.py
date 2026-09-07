"""Multi-tenant auth: signup, login (user + admin), session, per-user isolation."""


def test_signup_login_me_logout(client):
    r = client.post("/api/auth/signup", json={"username": "alice", "password": "pw-abcdefgh"})
    assert r.status_code == 200
    body = r.json()
    assert body["authenticated"] is True and body["user"]["username"] == "alice"
    assert body["user"]["is_admin"] is False
    assert client.get("/api/auth/me").json()["authenticated"] is True

    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").json() == {"authenticated": False, "user": None}

    # log back in (case-insensitive)
    r = client.post("/api/auth/login", json={"username": "Alice", "password": "pw-abcdefgh"})
    assert r.status_code == 200 and r.json()["user"]["username"] == "alice"


def test_signup_rejects_bad_input(client):
    assert client.post("/api/auth/signup", json={"username": "ab", "password": "pw-abcdefgh"}).status_code == 422
    assert client.post("/api/auth/signup", json={"username": "has space", "password": "pw-abcdefgh"}).status_code == 422
    assert client.post("/api/auth/signup", json={"username": "bob", "password": "short"}).status_code == 422


def test_duplicate_username_409(client):
    client.post("/api/auth/signup", json={"username": "dup", "password": "pw-abcdefgh"})
    r = client.post("/api/auth/signup", json={"username": "DUP", "password": "pw-abcdefgh"})
    assert r.status_code == 409


def test_login_bad_password_401(client):
    client.post("/api/auth/signup", json={"username": "carol", "password": "pw-abcdefgh"})
    assert client.post("/api/auth/login", json={"username": "carol", "password": "wrong"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "nobody", "password": "x"}).status_code == 401


def test_admin_login_via_username_and_token(client, admin):
    r = client.post("/api/auth/login", json={"username": "tester", "password": "test-password-1234"})
    assert r.status_code == 200 and r.json()["user"]["is_admin"] is True
    # header token resolves to the admin user
    assert client.get("/api/auth/me", headers=admin).json()["user"]["is_admin"] is True


def test_keys_require_sign_in(client):
    assert client.get("/api/keys").status_code == 401
    assert client.post("/api/keys", json={"label": "x", "allowed_providers": ["openai"]}).status_code == 401


def test_users_are_isolated(signup):
    ca, ua = signup("alice")
    cb, ub = signup("bob")
    assert ua["id"] != ub["id"]

    ca.post("/api/keys", json={"label": "alice key", "allowed_providers": ["openrouter"]})
    cb.post("/api/keys", json={"label": "bob key", "allowed_providers": ["openrouter"]})

    assert [k["label"] for k in ca.get("/api/keys").json()] == ["alice key"]
    b_keys = cb.get("/api/keys").json()
    assert [k["label"] for k in b_keys] == ["bob key"]

    # alice cannot see or touch bob's key
    bob_key_id = b_keys[0]["id"]
    assert bob_key_id not in [k["id"] for k in ca.get("/api/keys").json()]
    assert ca.delete(f"/api/keys/{bob_key_id}").status_code == 404
    assert ca.patch(f"/api/keys/{bob_key_id}", json={"allow_live": True}).status_code == 404
    # and their usage dashboards don't cross
    assert ca.get("/api/usage/summary").json()["total_requests"] == 0
    assert cb.get("/api/usage/summary").json()["total_requests"] == 0


def test_signup_starts_empty(signup):
    c, _ = signup()
    summary = c.get("/api/usage/summary").json()
    assert summary["total_requests"] == 0
    assert summary["active_keys"] == 0
    assert c.get("/api/keys").json() == []


def test_admin_sees_all_keys_with_owner(client, admin, signup):
    ca, _ = signup("owner")
    ca.post("/api/keys", json={"label": "owned", "allowed_providers": ["openrouter"]})
    rows = client.get("/api/keys", headers=admin).json()
    owned = next(k for k in rows if k["label"] == "owned")
    assert owned["owner_username"] == "owner"


def test_signup_ip_throttle(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "SIGNUPS_PER_IP_PER_HOUR", 2)
    assert client.post("/api/auth/signup", json={"username": "thr1", "password": "pw-abcdefgh"}).status_code == 200
    client.post("/api/auth/logout")
    assert client.post("/api/auth/signup", json={"username": "thr2", "password": "pw-abcdefgh"}).status_code == 200
    client.post("/api/auth/logout")
    assert client.post("/api/auth/signup", json={"username": "thr3", "password": "pw-abcdefgh"}).status_code == 429


def test_max_users_cap(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "MAX_USERS", 1)
    assert client.post("/api/auth/signup", json={"username": "cap1", "password": "pw-abcdefgh"}).status_code == 200
    client.post("/api/auth/logout")
    assert client.post("/api/auth/signup", json={"username": "cap2", "password": "pw-abcdefgh"}).status_code == 503


def test_account_data_controls(signup):
    c, _ = signup()
    k = c.post(
        "/api/keys", json={"label": "k", "allowed_providers": ["openrouter"]}
    ).json()
    c.post(
        "/v1/proxy/chat",
        json={"provider": "openrouter", "model": "m", "prompt": "hi"},
        headers={"Authorization": f"Bearer {k['key']}"},
    )
    assert c.get("/api/usage/summary").json()["total_requests"] == 1

    assert c.delete("/api/account/data").status_code == 200
    assert c.get("/api/usage/summary").json()["total_requests"] == 0
    assert c.get("/api/keys").json() == []

    assert c.delete("/api/account").status_code == 200
    assert c.get("/api/auth/me").json()["authenticated"] is False
