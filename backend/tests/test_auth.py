"""Auth: signup, login, session, and the workspace-less starting state."""


def test_signup_login_me_logout(client):
    r = client.post("/api/auth/signup", json={"username": "alice", "password": "pw-abcdefgh"})
    assert r.status_code == 200
    body = r.json()
    assert body["authenticated"] is True
    assert body["user"]["username"] == "alice"
    assert body["user"]["workspace"] is None  # fresh signup has no workspace

    assert client.get("/api/auth/me").json()["authenticated"] is True
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").json() == {"authenticated": False, "user": None}

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


def test_me_reflects_workspace_after_create(new_admin):
    c, ws = new_admin("Acme")
    me = c.get("/api/auth/me").json()["user"]
    assert me["workspace"] == {"id": ws["id"], "name": "Acme", "role": "admin"}


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


def test_delete_own_account(signup):
    c, _ = signup()
    assert c.delete("/api/account").status_code == 200
    assert c.get("/api/auth/me").json()["authenticated"] is False


def test_admin_cannot_delete_account_with_members(admin_client, new_member):
    new_member(admin_client)
    r = admin_client.delete("/api/account")
    assert r.status_code == 400
