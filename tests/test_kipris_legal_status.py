# -*- coding: utf-8 -*-
"""kipris_legal_status.py 계약 테스트 — fixture XML 주입, 네트워크 없음."""
import json
import os

import pytest

from conftest import FAKE_KEY, fixture_bytes

AN = "1020990000001"


def run_main(mod, monkeypatch, argv_tail, fetch):
    monkeypatch.setattr(mod, "bump_quota", lambda *_: 42)
    monkeypatch.setattr(mod, "fetch", fetch)
    monkeypatch.setattr(mod.sys, "argv", ["kipris_legal_status.py"] + argv_tail)
    mod.main()


def read_legal(out_dir):
    with open(os.path.join(out_dir, "legal_status.json"), encoding="utf-8") as f:
        return json.load(f)


def test_ok_record_events_and_unknown_claims_contract(legal_mod, monkeypatch, tmp_path):
    out = str(tmp_path / "out")
    run_main(legal_mod, monkeypatch, ["10-2099-0000001", "--out", out],
             lambda url: fixture_bytes("legal_ok.xml"))
    rec = read_legal(out)[AN]
    assert rec["schema_version"] == 1
    # 핵심 계약: 이력 수집 ≠ 현재 청구항 확정 — unknown 고정
    assert rec["current_enforceable_claims"] == "unknown"
    assert rec["status_source"].startswith("legStatusST27InfoSearchService/BasicInfo")
    assert rec["retrieved_at"]
    assert rec["n_events"] == 2 and len(rec["legal_events"]) == 2
    ev = rec["legal_events"][0]
    assert ev["keyEventCode"] == "A10" and ev["eventDate"] == "20990101"
    assert ev["nationalEventCode"] == "PA0105"
    # 공백뿐인 필드는 이벤트에 싣지 않는다
    assert "trialNumber" not in ev and "rightTypeCode" not in ev
    assert rec["legal_events"][1]["keyEventCode"] == "E10"
    assert os.path.exists(os.path.join(out, f"legal_{AN}.xml"))


def test_err31_not_subscribed_exits_4(legal_mod, monkeypatch, tmp_path):
    out = str(tmp_path / "out")
    with pytest.raises(SystemExit) as e:
        run_main(legal_mod, monkeypatch, [AN, "--out", out],
                 lambda url: fixture_bytes("legal_err31.xml"))
    assert e.value.code == 4  # 상품 미가입 전용 종료 코드
    rec = read_legal(out)[AN]
    assert "error" in rec and "가입 필요" in rec["error"]
    assert "legal_events" not in rec


def test_err31_stops_further_calls(legal_mod, monkeypatch, tmp_path):
    """미가입은 전 문헌 공통 — 후속 문헌에 API를 더 쓰지 않는다(쿼터 보호)."""
    out = str(tmp_path / "out")
    calls = []

    def fetch(url):
        calls.append(url)
        return fixture_bytes("legal_err31.xml")

    other = "1020990000002"
    with pytest.raises(SystemExit) as e:
        run_main(legal_mod, monkeypatch, [AN, other, "--out", out], fetch)
    assert e.value.code == 4
    assert len(calls) == 1  # 두 번째 문헌은 호출하지 않음
    data = read_legal(out)
    assert "가입 필요" in data[AN]["error"]
    assert "가입 필요" in data[other]["error"]  # 미확인으로 명시 기록(fto_gate가 걸러냄)


def test_missing_resultcode_is_schema_error(legal_mod, monkeypatch, tmp_path):
    out = str(tmp_path / "out")
    with pytest.raises(SystemExit) as e:
        run_main(legal_mod, monkeypatch, [AN, "--out", out],
                 lambda url: fixture_bytes("legal_no_resultcode.xml"))
    assert e.value.code == 1
    rec = read_legal(out)[AN]
    assert "error" in rec and "schema_error" in rec["error"]


def test_response_appno_mismatch_is_error(legal_mod, monkeypatch, tmp_path):
    out = str(tmp_path / "out")
    with pytest.raises(SystemExit) as e:
        run_main(legal_mod, monkeypatch, [AN, "--out", out],
                 lambda url: fixture_bytes("legal_mismatch.xml"))
    assert e.value.code == 1
    rec = read_legal(out)[AN]
    assert "error" in rec and "불일치" in rec["error"]
    assert "legal_events" not in rec  # 성공 레코드로 위장하지 않음


def test_zero_events_is_failure(legal_mod, monkeypatch, tmp_path):
    """0건을 '법적 상태 확인됨'으로 오인하지 않는다 — fail-closed."""
    out = str(tmp_path / "out")
    with pytest.raises(SystemExit) as e:
        run_main(legal_mod, monkeypatch, [AN, "--out", out],
                 lambda url: fixture_bytes("legal_empty.xml"))
    assert e.value.code == 1
    rec = read_legal(out)[AN]
    assert "error" in rec and "0건" in rec["error"]


def test_refresh_failure_preserves_existing_record(legal_mod, monkeypatch, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    existing = {AN: {"schema_version": 1, "current_enforceable_claims": "unknown",
                     "legal_events": [{"keyEventCode": "A10", "eventDate": "20990101"}],
                     "n_events": 1, "retrieved_at": "2099-01-01T00:00:00+0900"}}
    (out / "legal_status.json").write_text(json.dumps(existing, ensure_ascii=False),
                                           encoding="utf-8")

    def boom(url):
        raise RuntimeError("simulated network failure")

    with pytest.raises(SystemExit) as e:
        run_main(legal_mod, monkeypatch, [AN, "--out", str(out)], boom)
    assert e.value.code == 1
    rec = read_legal(str(out))[AN]
    assert rec["legal_events"] == [{"keyEventCode": "A10", "eventDate": "20990101"}]
    assert "simulated network failure" in rec["last_refresh_error"]
    assert "error" not in rec


def test_raw_xml_saved_with_key_redacted(legal_mod, monkeypatch, tmp_path):
    out = str(tmp_path / "out")
    key_bytes = FAKE_KEY.encode()

    def fetch(url):
        # 응답 본문에 키가 섞여 오는 최악의 경우를 시뮬레이션
        return fixture_bytes("legal_ok.xml").replace(b"</response>",
                                                     key_bytes + b"</response>")

    run_main(legal_mod, monkeypatch, [AN, "--out", out], fetch)
    saved = open(os.path.join(out, f"legal_{AN}.xml"), "rb").read()
    assert key_bytes not in saved
    assert b"[REDACTED]" in saved


def test_invalid_appno_rejected_before_network(legal_mod, monkeypatch, tmp_path):
    def no_fetch(url):
        raise AssertionError("잘못된 번호에서 네트워크 호출 발생")

    with pytest.raises(SystemExit) as e:
        run_main(legal_mod, monkeypatch, ["12-34", "--out", str(tmp_path)], no_fetch)
    assert "형식" in str(e.value.code)
