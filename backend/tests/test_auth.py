"""Multi-tenant auth: signup, login (user + admin), session, per-user isolation."""


def test_signup_login_me_logout(client):
    r = client.post("/api/auth/signup", json={"email": "a@example.com", "password": "pw-abcdefgh"})
    assert r.status_code == 200
    body = r.json()
    assert body["authenticated"] is True and body["user"]["email"] == "a@example.com"
    assert body["user"]["is_admin"] is False
    assert client.get("/api/auth/me").json()["authenticated"] is True

    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").json() == {"authenticated": False, "user": None}

    # log back in
    r = client.post("/api/auth/login", json={"email": "A@Example.com", "password": "pw-abcdefgh"})
    assert r.status_code == 200 and r.json()["user"]["email"] == "a@example.com"


def test_signup_rejects_bad_input(client):
    assert client.post("/api/auth/signup", json={"email": "not-an-email", "password": "pw-abcdefgh"}).status_code == 422
    assert client.post("/api/auth/signup", json={"email": "b@example.com", "password": "short"}).status_code == 422


def test_duplicate_email_409(client):
    client.post("/api/auth/signup", json={"email": "dup@example.com", "password": "pw-abcdefgh"})
    r = client.post("/api/auth/signup", json={"email": "DUP@example.com", "password": "pw-abcdefgh"})
    assert r.status_code == 409


def test_login_bad_password_401(client):
    client.post("/api/auth/signup", json={"email": "c@example.com", "password": "pw-abcdefgh"})
    assert client.post("/api/auth/login", json={"email": "c@example.com", "password": "wrong"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "x"}).status_code == 401


def test_admin_login_via_username_and_token(client, admin):
    r = client.post("/api/auth/login", json={"email": "tester", "password": "test-password-1234"})
    assert r.status_code == 200 and r.json()["user"]["is_admin"] is True
    # header token resolves to the admin user
    assert client.get("/api/auth/me", headers=admin).json()["user"]["is_admin"] is True


def test_keys_require_sign_in(client):
    assert client.get("/api/keys").status_code == 401
    assert client.post("/api/keys", json={"label": "x", "allowed_providers": ["openai"]}).status_code == 401


def test_users_are_isolated(signup):
    ca, ua = signup("alice@example.com")
    cb, ub = signup("bob@example.com")
    assert ua["id"] != ub["id"]
    ca.delete("/api/account/data")  # drop the seeded sample -> clean slate
    cb.delete("/api/account/data")

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


def test_signup_seeds_a_sample_dataset(signup):
    c, _ = signup()
    summary = c.get("/api/usage/summary").json()
    assert summary["total_requests"] > 50  # sample data is there
    assert summary["active_keys"] == 3
    keys = c.get("/api/keys").json()
    assert {k["label"] for k in keys} == {"My app", "Batch jobs", "Chat feature"}


def test_admin_sees_all_keys_with_owner(client, admin, signup):
    ca, _ = signup("owner@example.com")
    ca.post("/api/keys", json={"label": "owned", "allowed_providers": ["openrouter"]})
    rows = client.get("/api/keys", headers=admin).json()
    owned = next(k for k in rows if k["label"] == "owned")
    assert owned["owner_email"] == "owner@example.com"


def test_signup_ip_throttle(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "SIGNUPS_PER_IP_PER_HOUR", 2)
    assert client.post("/api/auth/signup", json={"email": "t1@example.com", "password": "pw-abcdefgh"}).status_code == 200
    client.post("/api/auth/logout")
    assert client.post("/api/auth/signup", json={"email": "t2@example.com", "password": "pw-abcdefgh"}).status_code == 200
    client.post("/api/auth/logout")
    assert client.post("/api/auth/signup", json={"email": "t3@example.com", "password": "pw-abcdefgh"}).status_code == 429


def test_max_users_cap(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "MAX_USERS", 1)
    assert client.post("/api/auth/signup", json={"email": "cap1@example.com", "password": "pw-abcdefgh"}).status_code == 200
    client.post("/api/auth/logout")
    assert client.post("/api/auth/signup", json={"email": "cap2@example.com", "password": "pw-abcdefgh"}).status_code == 503


def test_account_data_controls(signup):
    c, _ = signup()
    assert c.get("/api/usage/summary").json()["total_requests"] > 50
    assert c.delete("/api/account/data").status_code == 200
    assert c.get("/api/usage/summary").json()["total_requests"] == 0
    assert c.post("/api/account/sample").json()["usage_rows"] > 50
    assert c.get("/api/usage/summary").json()["total_requests"] > 50
    assert c.delete("/api/account").status_code == 200
    assert c.get("/api/auth/me").json()["authenticated"] is False
