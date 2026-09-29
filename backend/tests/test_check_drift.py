"""Tests for scripts/check_drift.py — the three-way (expected / deployed /
running) drift verdict. The CLI cases drive check_drift.main() with the three
readers monkeypatched (no network, no git, no Render API), asserting the real
process exit code; a few extra cases pin the pure compare() edges."""

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check_drift.py"
_spec = importlib.util.spec_from_file_location("check_drift", _PATH)
check_drift = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_drift)

_A = "a" * 40
_B = "b" * 40


def _exit(monkeypatch, expected, deployed, running, args):
    """Run the real CLI with the three readers stubbed; return its exit code."""
    monkeypatch.setattr(check_drift, "expected_commit", lambda *a, **k: expected)
    monkeypatch.setattr(check_drift, "deployed_commit", lambda *a, **k: deployed)
    monkeypatch.setattr(check_drift, "running_commit", lambda *a, **k: running)
    return check_drift.main(args)


# ---- CLI exit-code cases (the approved matrix) ----
def test_cli_all_three_equal_exit_0(monkeypatch):
    assert _exit(monkeypatch, _A, _A, _A, []) == 0


def test_cli_all_three_present_mismatch_exit_1(monkeypatch):
    assert _exit(monkeypatch, _A, _A, _B, []) == 1


def test_cli_skip_deployed_expected_eq_running_exit_0(monkeypatch):
    assert _exit(monkeypatch, _A, None, _A, ["--skip-deployed"]) == 0


def test_cli_skip_deployed_expected_ne_running_exit_1(monkeypatch):
    assert _exit(monkeypatch, _A, None, _B, ["--skip-deployed"]) == 1


def test_cli_skip_running_expected_eq_deployed_exit_0(monkeypatch):
    assert _exit(monkeypatch, _A, _A, None, ["--skip-running"]) == 0


def test_cli_skip_running_expected_ne_deployed_exit_1(monkeypatch):
    assert _exit(monkeypatch, _A, _B, None, ["--skip-running"]) == 1


def test_cli_only_one_layer_is_insufficient_exit_1(monkeypatch):
    assert _exit(monkeypatch, _A, None, None, ["--skip-deployed", "--skip-running"]) == 1


# ---- pure compare() edges ----
def test_compare_abbreviated_sha_matches():
    ok, _ = check_drift.compare(_A, _A[:12], _A)
    assert ok is True


def test_compare_unresolved_present_layer_is_drift():
    ok, _ = check_drift.compare(_A, None, _A)  # all present, deployed unresolved
    assert ok is False


def test_compare_reports_all_three_pairs_when_present():
    _, lines = check_drift.compare(_A, _A, _A)
    text = "\n".join(lines)
    assert "expected vs deployed:" in text
    assert "expected vs running:" in text
    assert "deployed vs running:" in text


def test_compare_insufficient_when_only_one_layer():
    ok, lines = check_drift.compare(_A, None, None, skip_deployed=True, skip_running=True)
    assert ok is False
    assert any("insufficient information" in ln for ln in lines)


def test_self_test_passes():
    assert check_drift._self_test() == 0
