"""Unit tests for the pure comparison core of scripts/check_drift.py — the
three-way (expected / deployed / running) drift verdict. No network: the readers
(git ls-remote, Render API, HTTP /version) are exercised separately/by the
script's own --self-test; here we pin the logic that decides IN SYNC vs DRIFT."""

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check_drift.py"
_spec = importlib.util.spec_from_file_location("check_drift", _PATH)
check_drift = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_drift)

_A = "a" * 40
_B = "b" * 40


def test_all_three_match_is_in_sync():
    ok, _ = check_drift.compare(_A, _A, _A)
    assert ok is True


def test_expected_ne_deployed_is_drift():
    ok, lines = check_drift.compare(_A, _B, _B)  # pushed main, not deployed
    assert ok is False
    assert any("DRIFT" in ln for ln in lines)


def test_deployed_ne_running_is_drift():
    ok, _ = check_drift.compare(_A, _A, _B)  # stale/failed running instance
    assert ok is False


def test_unresolved_value_is_drift():
    ok, _ = check_drift.compare(_A, None, _A)  # couldn't read a layer -> not confirmed
    assert ok is False


def test_skipped_layers_are_excluded():
    ok, _ = check_drift.compare(_A, None, None, skip_deployed=True, skip_running=True)
    assert ok is True  # nothing to contradict


def test_abbreviated_sha_matches():
    ok, _ = check_drift.compare(_A, _A[:12], _A)
    assert ok is True


def test_self_test_passes():
    assert check_drift._self_test() == 0
