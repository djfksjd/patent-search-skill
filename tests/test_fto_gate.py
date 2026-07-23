# -*- coding: utf-8 -*-
"""fto_gate.py 판정 테스트 — 파일 기반, 네트워크 없음."""
import json

import pytest

AN1 = "1020990000001"
AN2 = "1020990000002"

CLAIMS = {AN1: {"title": "가공 문헌 1", "claims": ["청구항 1."]},
          AN2: {"title": "가공 문헌 2", "claims": ["청구항 1."]}}
LEGAL_OK = {"schema_version": 1, "current_enforceable_claims": "unknown",
            "legal_events": [{"keyEventCode": "A10", "eventDate": "20990101"}],
            "n_events": 1, "retrieved_at": "2099-01-01T00:00:00+0900"}


def run_gate(mod, monkeypatch, claims_path, legal_path):
    monkeypatch.setattr(mod.sys, "argv",
                        ["fto_gate.py", "--claims", str(claims_path),
                         "--legal", str(legal_path)])
    mod.main()


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_all_verified_exit_0(fto_mod, monkeypatch, tmp_path, capsys):
    c = write(tmp_path / "claims.json", CLAIMS)
    l = write(tmp_path / "legal_status.json", {AN1: LEGAL_OK, AN2: LEGAL_OK})
    run_gate(fto_mod, monkeypatch, c, l)  # 예외 없이 종료 = exit 0
    out = capsys.readouterr().out
    assert "판정 가능" in out and AN1 in out and AN2 in out
    assert "판정 불가" not in out


def test_missing_record_exit_2(fto_mod, monkeypatch, tmp_path, capsys):
    c = write(tmp_path / "claims.json", CLAIMS)
    l = write(tmp_path / "legal_status.json", {AN1: LEGAL_OK})  # AN2 미확인
    with pytest.raises(SystemExit) as e:
        run_gate(fto_mod, monkeypatch, c, l)
    assert e.value.code == 2
    out = capsys.readouterr().out
    assert "판정 불가 — 상태 미확인" in out and AN2 in out
    assert "판정 가능" in out and AN1 in out  # 확인된 건은 판정 가능 목록에


def test_error_record_exit_2(fto_mod, monkeypatch, tmp_path, capsys):
    c = write(tmp_path / "claims.json", {AN1: CLAIMS[AN1]})
    l = write(tmp_path / "legal_status.json",
              {AN1: {"error": "상품(법적 상태 이력 — 특허·실용(ST.27)) 가입 필요"}})
    with pytest.raises(SystemExit) as e:
        run_gate(fto_mod, monkeypatch, c, l)
    assert e.value.code == 2
    assert "가입 필요" in capsys.readouterr().out


def test_empty_events_exit_2(fto_mod, monkeypatch, tmp_path):
    c = write(tmp_path / "claims.json", {AN1: CLAIMS[AN1]})
    l = write(tmp_path / "legal_status.json",
              {AN1: {"schema_version": 1, "legal_events": []}})
    with pytest.raises(SystemExit) as e:
        run_gate(fto_mod, monkeypatch, c, l)
    assert e.value.code == 2


def test_missing_legal_file_exit_2(fto_mod, monkeypatch, tmp_path, capsys):
    c = write(tmp_path / "claims.json", CLAIMS)
    with pytest.raises(SystemExit) as e:
        run_gate(fto_mod, monkeypatch, c, tmp_path / "no_such.json")
    assert e.value.code == 2
    out = capsys.readouterr().out
    assert out.count("판정 불가") >= 1 and AN1 in out and AN2 in out


def test_missing_claims_file_fail_closed(fto_mod, monkeypatch, tmp_path):
    l = write(tmp_path / "legal_status.json", {AN1: LEGAL_OK})
    with pytest.raises(SystemExit) as e:
        run_gate(fto_mod, monkeypatch, tmp_path / "no_claims.json", l)
    assert e.value.code == 2


def test_refresh_warning_still_verified(fto_mod, monkeypatch, tmp_path, capsys):
    """정상 이력 + last_refresh_error는 통과하되 경고를 표기한다."""
    rec = dict(LEGAL_OK, last_refresh_error="RuntimeError: simulated")
    c = write(tmp_path / "claims.json", {AN1: CLAIMS[AN1]})
    l = write(tmp_path / "legal_status.json", {AN1: rec})
    run_gate(fto_mod, monkeypatch, c, l)
    out = capsys.readouterr().out
    assert "판정 가능" in out and "경고" in out
