# -*- coding: utf-8 -*-
"""공통 픽스처 — 네트워크 없음, .env 미열람.

- 스크립트는 단일 파일 CLI라 importlib로 로드해 내부 함수를 직접 검증한다.
- KIPRIS_KEY는 가짜 값을 환경변수로 주입한다(load_key가 env를 우선하므로 .env를 읽지 않음).
- 실제 네트워크가 절대 나가지 않도록 각 테스트에서 mod.fetch를 fixture XML로 교체하고,
  안전망으로 urllib.request.urlopen도 차단한다.
- bump_quota는 스킬 폴더의 실제 kipris_quota.json을 건드리므로 main() 경유 테스트에서는
  스텁으로 교체한다(bump_quota 자체는 tmp 경로로 직접 테스트).
"""
import importlib.util
import pathlib
import sys

import pytest

TESTS_DIR = pathlib.Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "skills" / "patent-search" / "scripts"
FIXTURES_DIR = TESTS_DIR / "fixtures"

FAKE_KEY = "TEST-fake key+val/==NOT-REAL"  # quote/quote_plus 변이가 서로 달라지는 문자 포함


def load_script(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def fixture_bytes(name):
    return (FIXTURES_DIR / name).read_bytes()


def _no_network(*args, **kwargs):
    raise AssertionError("테스트 중 실제 네트워크 호출 시도 — 금지됨")


def _prep(mod, monkeypatch):
    monkeypatch.setenv("KIPRIS_KEY", FAKE_KEY)
    monkeypatch.setattr(mod.urllib.request, "urlopen", _no_network)
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    mod.CALLS[0] = 0
    return mod


@pytest.fixture()
def search_mod(monkeypatch):
    mod = load_script("kipris_search_under_test", "kipris_search.py")
    yield _prep(mod, monkeypatch)
    sys.modules.pop("kipris_search_under_test", None)


@pytest.fixture()
def claims_mod(monkeypatch):
    mod = load_script("kipris_claims_under_test", "kipris_claims.py")
    yield _prep(mod, monkeypatch)
    sys.modules.pop("kipris_claims_under_test", None)


@pytest.fixture()
def legal_mod(monkeypatch):
    mod = load_script("kipris_legal_status_under_test", "kipris_legal_status.py")
    yield _prep(mod, monkeypatch)
    sys.modules.pop("kipris_legal_status_under_test", None)


@pytest.fixture()
def expand_mod(monkeypatch):
    mod = load_script("kipris_expand_under_test", "kipris_expand.py")
    # 확장 스크립트는 동시 실행 lock + 원장 예약을 쓴다 — 실제 스킬 폴더 파일/락을 건드리지
    # 않도록 lock은 no-op, 예약은 인메모리 카운터로 교체한다(원장 함수는 별도 단위 테스트).
    monkeypatch.setattr(mod, "acquire_run_lock",
                        lambda *_a, **_k: __import__("contextlib").nullcontext())
    monkeypatch.setattr(mod, "reserve_one", lambda *_a, **_k: 1)
    yield _prep(mod, monkeypatch)
    sys.modules.pop("kipris_expand_under_test", None)


@pytest.fixture()
def fto_mod():
    mod = load_script("fto_gate_under_test", "fto_gate.py")
    yield mod
    sys.modules.pop("fto_gate_under_test", None)


@pytest.fixture()
def chart_mod():
    mod = load_script("claim_chart_validate_under_test", "claim_chart_validate.py")
    yield mod
    sys.modules.pop("claim_chart_validate_under_test", None)
