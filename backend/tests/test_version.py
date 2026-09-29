"""GET /version — deployment identity for drift monitoring (Phase 4).

`version` is the hand-maintained release string (unchanged from /healthz's).
`commit`/`branch` come from Render's runtime env (RENDER_GIT_COMMIT /
RENDER_GIT_BRANCH) and are null off-Render — a local SHA is never fabricated.
"""

from app.config import Settings, settings


def test_version_shape_and_release(client):
    r = client.get("/version")
    assert r.status_code == 200
    j = r.json()
    assert set(j) == {"version", "commit", "branch"}
    assert j["version"] == settings.VERSION  # release string, unchanged


def test_version_reports_render_commit_and_branch(client, monkeypatch):
    monkeypatch.setattr(settings, "RENDER_GIT_COMMIT", "a" * 40)
    monkeypatch.setattr(settings, "RENDER_GIT_BRANCH", "main")
    j = client.get("/version").json()
    assert j["commit"] == "a" * 40  # exact SHA, not truncated/derived
    assert j["branch"] == "main"


def test_version_null_off_render(client, monkeypatch):
    monkeypatch.setattr(settings, "RENDER_GIT_COMMIT", "")
    monkeypatch.setattr(settings, "RENDER_GIT_BRANCH", "")
    j = client.get("/version").json()
    assert j["commit"] is None
    assert j["branch"] is None


def test_settings_bind_render_env_vars_without_docker_changes(monkeypatch):
    """The crux of the no-Dockerfile / no-render.yaml claim: pydantic binds the
    Render-provided env vars straight to the Settings fields by name, so nothing
    in the build or the blueprint has to plumb them through."""
    monkeypatch.setenv("RENDER_GIT_COMMIT", "deadbeef" * 5)  # 40 chars
    monkeypatch.setenv("RENDER_GIT_BRANCH", "release/x")
    s = Settings()
    assert s.RENDER_GIT_COMMIT == "deadbeef" * 5
    assert s.RENDER_GIT_BRANCH == "release/x"
