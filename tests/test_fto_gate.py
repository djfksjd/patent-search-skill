# -*- coding: utf-8 -*-
"""fto_gate.py 판정 테스트 — 파일 기반, 네트워크 없음."""
import json

import pytest

AN1 = "1020990000001"
AN2 = "1020990000002"

CLAIMS = {AN1: {"title": "가공 문헌 1", "claims": ["청구항 1."]},
          AN2: {"title": "가공 문헌 2", "claims": ["청구항 1."]}}
LEGAL_OK = {"schema_version": 1, "current_enforceable_claims": "unknown",
            "status_source": "legStatusST27InfoSearchService/BasicInfo(법적 상태 이력, WIPO ST.27)",
            "legal_events": [{"keyEventCode": "A10", "eventDate": "20990101"}],
            "n_events": 1, "retrieved_at": "2099-01-01T00:00:00+0900"}


def run_gate(mod, monkeypatch, claims_path, legal_path, expansion_path=None):
    argv = ["fto_gate.py", "--claims", str(claims_path), "--legal", str(legal_path)]
    if expansion_path is not None:
        argv += ["--expansion", str(expansion_path)]
    monkeypatch.setattr(mod.sys, "argv", argv)
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


def _expansion(family_status, n=0):
    return {"axes": {"family": {"status": family_status, "n_candidates": n,
                                "reason": "빈 familyInfo를 없음으로 단정하지 않는다"}}}


def test_expansion_family_unknown_holds_coverage(fto_mod, monkeypatch, tmp_path, capsys):
    """family 미완이면 패밀리 전체 FTO 결론 보류 — KR 게이트 판정(exit 0)은 유지."""
    c = write(tmp_path / "claims.json", CLAIMS)
    l = write(tmp_path / "legal_status.json", {AN1: LEGAL_OK, AN2: LEGAL_OK})
    x = write(tmp_path / "expansion.json", _expansion("unknown"))
    run_gate(fto_mod, monkeypatch, c, l, x)  # 예외 없음 = exit 0(게이트 판정 불변)
    out = capsys.readouterr().out
    assert "패밀리 커버리지 미확인" in out and "결론 보류" in out
    assert "판정 가능" in out  # KR 문헌 게이트는 그대로 통과


def test_expansion_family_complete_reports_coverage(fto_mod, monkeypatch, tmp_path, capsys):
    c = write(tmp_path / "claims.json", {AN1: CLAIMS[AN1]})
    l = write(tmp_path / "legal_status.json", {AN1: LEGAL_OK})
    x = write(tmp_path / "expansion.json", _expansion("complete", n=3))
    run_gate(fto_mod, monkeypatch, c, l, x)
    out = capsys.readouterr().out
    assert "패밀리 커버리지: complete" in out
    assert "발견≠증거" in out


def test_expansion_missing_file_holds_coverage(fto_mod, monkeypatch, tmp_path, capsys):
    c = write(tmp_path / "claims.json", {AN1: CLAIMS[AN1]})
    l = write(tmp_path / "legal_status.json", {AN1: LEGAL_OK})
    run_gate(fto_mod, monkeypatch, c, l, tmp_path / "nope.json")
    out = capsys.readouterr().out
    assert "패밀리 커버리지: 미확인" in out


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


def test_refresh_error_rejected(fto_mod, monkeypatch, tmp_path, capsys):
    """정상 이력이 있어도 최근 재조회 실패면 상태 최신성 미확인 — 판정 불가(fail-closed)."""
    rec = dict(LEGAL_OK, last_refresh_error="RuntimeError: simulated")
    c = write(tmp_path / "claims.json", {AN1: CLAIMS[AN1]})
    l = write(tmp_path / "legal_status.json", {AN1: rec})
    with pytest.raises(SystemExit) as e:
        run_gate(fto_mod, monkeypatch, c, l)
    assert e.value.code == 2
    out = capsys.readouterr().out
    assert "판정 불가" in out and "재조회 실패" in out


def test_malformed_events_rejected(fto_mod, monkeypatch, tmp_path, capsys):
    """legal_events에 null/빈 항목이 섞인 레코드는 판정 불가."""
    rec = dict(LEGAL_OK, legal_events=[None])
    c = write(tmp_path / "claims.json", {AN1: CLAIMS[AN1]})
    l = write(tmp_path / "legal_status.json", {AN1: rec})
    with pytest.raises(SystemExit) as e:
        run_gate(fto_mod, monkeypatch, c, l)
    assert e.value.code == 2
    assert "비정상 항목" in capsys.readouterr().out


def test_claims_contract_violation_rejected(fto_mod, monkeypatch, tmp_path, capsys):
    """current_enforceable_claims가 'unknown'이 아닌 레코드는 계약 위반 — 판정 불가."""
    rec = dict(LEGAL_OK, current_enforceable_claims="claims 1-10")
    c = write(tmp_path / "claims.json", {AN1: CLAIMS[AN1]})
    l = write(tmp_path / "legal_status.json", {AN1: rec})
    with pytest.raises(SystemExit) as e:
        run_gate(fto_mod, monkeypatch, c, l)
    assert e.value.code == 2
    assert "계약 위반" in capsys.readouterr().out


@pytest.mark.parametrize("patch,needle", [
    ({"schema_version": 999}, "미지원"),
    ({"schema_version": True}, "미지원"),  # bool은 int 하위형 — True == 1 우회 차단
    ({"status_source": "unverified"}, "산출물이 아님"),
    ({"retrieved_at": "?"}, "형식 불량"),
    ({"retrieved_at": "2026-99-99T99:99garbage"}, "형식 불량"),  # 달력 검증
    ({"legal_events": [{"garbage": "x"}]}, "비정상 항목"),
    ({"legal_events": [{"keyEventCode": None}]}, "비정상 항목"),  # 키만 있고 값 없음
])
def test_value_validation_rejects_garbage(fto_mod, monkeypatch, tmp_path, capsys,
                                          patch, needle):
    """필드 존재만이 아니라 값까지 검증한다 — 위조·혼입 레코드 fail-closed."""
    rec = dict(LEGAL_OK, **patch)
    c = write(tmp_path / "claims.json", {AN1: CLAIMS[AN1]})
    l = write(tmp_path / "legal_status.json", {AN1: rec})
    with pytest.raises(SystemExit) as e:
        run_gate(fto_mod, monkeypatch, c, l)
    assert e.value.code == 2
    assert needle in capsys.readouterr().out


def test_missing_required_field_rejected(fto_mod, monkeypatch, tmp_path, capsys):
    """status_source 등 필수 필드가 빠진 레코드는 판정 불가."""
    rec = {k: v for k, v in LEGAL_OK.items() if k != "status_source"}
    c = write(tmp_path / "claims.json", {AN1: CLAIMS[AN1]})
    l = write(tmp_path / "legal_status.json", {AN1: rec})
    with pytest.raises(SystemExit) as e:
        run_gate(fto_mod, monkeypatch, c, l)
    assert e.value.code == 2
    assert "산출물이 아님" in capsys.readouterr().out
