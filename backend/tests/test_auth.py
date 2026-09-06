"""Admin login: username + password -> session cookie. Header token still works."""


def test_login_rejects_bad_credentials(client):
    assert client.post("/api/auth/login", json={"username": "tester", "password": "nope"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "nobody", "password": "test-password-1234"}).status_code == 401


def test_me_before_login_is_anonymous(client):
    j = client.get("/api/auth/me").json()
    assert j == {"authenticated": False, "username": None}


def test_login_sets_session_then_logout(client):
    r = client.post("/api/auth/login", json={"username": "tester", "password": "test-password-1234"})
    assert r.status_code == 200 and r.json()["authenticated"] is True
    assert "llmt_session" in r.cookies or any(c.name == "llmt_session" for c in client.cookies.jar)

    me = client.get("/api/auth/me").json()
    assert me["authenticated"] is True and me["username"] == "tester"

    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").json()["authenticated"] is False


def test_session_cookie_unlocks_admin_routes(client):
    # no credential -> 403
    assert client.get("/api/keys").status_code == 403
    client.post("/api/auth/login", json={"username": "tester", "password": "test-password-1234"})
    # cookie now carried by the test client -> 200, no X-Admin-Token needed
    assert client.get("/api/keys").status_code == 200
    assert client.get("/api/providers").status_code == 200
    client.post("/api/auth/logout")
    assert client.get("/api/keys").status_code == 403


def test_header_token_still_works(client, admin):
    assert client.get("/api/keys", headers=admin).status_code == 200
    assert client.get("/api/auth/me", headers=admin).json() == {
        "authenticated": True,
        "username": "token",
    }


def test_tampered_session_cookie_is_rejected(client):
    client.post("/api/auth/login", json={"username": "tester", "password": "test-password-1234"})
    client.cookies.set("llmt_session", "abc.def")
    assert client.get("/api/keys").status_code == 403
