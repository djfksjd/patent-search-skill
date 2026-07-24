# -*- coding: utf-8 -*-
"""FTO 게이트 — fail-closed validator (네트워크 없음).

사용:
  python3 fto_gate.py --claims <작업폴더>/claims.json --legal <작업폴더>/legal_status.json

claims.json(kipris_claims.py)의 문헌 각각에 대해 legal_status.json(kipris_legal_status.py)의
법적 상태 확인 여부를 검사한다:

- 상태 확인됨   = legal_status.json에 해당 출원번호의 정상 레코드(legal_events 1건 이상,
                  error·last_refresh_error 없음, 필수 필드 온전,
                  current_enforceable_claims=="unknown")가 있다 → "FTO 관찰 판정 가능" 목록.
- 상태 미확인   = legal_status.json 자체가 없거나, 레코드가 없거나, error 레코드거나,
                  최근 재조회가 실패했거나(last_refresh_error — 상태 최신성 미확인),
                  레코드 구조가 계약과 다르다 → "판정 불가 — 상태 미확인" 목록.

종료 코드: 0=전 문헌 상태 확인됨(판정 가능), 2=하나 이상 상태 미확인(또는 입력 불능).
리포트 작성 시 이 게이트를 통과하지 못한 문헌에는 FTO 관찰 높음/낮음 등 확정 표현을
쓰지 못한다(SKILL.md 참조). 판정 가능 ≠ 판정 결과 — 판정 자체는 사람이 원문으로 한다.

주의: 이 게이트는 "법적 상태 이력을 확인했는가"만 검사한다. 현재 유효 청구항 확정은
별개 문제다(legal_status.json의 current_enforceable_claims="unknown" 유지 계약).

선택: --expansion <expansion.json>(kipris_expand.py 산출)을 주면 **패밀리 커버리지**를 함께
표기한다. family 축 status가 complete가 아니면(unknown/partial/failed/unsupported)
"패밀리 커버리지 미확인 — 패밀리 전체 FTO 낮음·해외 권리 없음 결론 보류"를 출력한다.
이는 KR 문헌 단위 게이트 판정(위)을 바꾸지 않는다 — 별개 축의 보류 표기다(계약 8).
"""
import argparse
import datetime
import json
import os
import sys

# kipris_legal_status.py 산출 계약과 동기 유지 — 값이 다르면 게이트가 막는다(fail-closed)
EXPECTED_STATUS_SOURCE = "legStatusST27InfoSearchService/BasicInfo(법적 상태 이력, WIPO ST.27)"
ST27_EVENT_KEYS = {
    "keyEventCode", "detailLawEventCode", "detailedEventCode", "stateCode",
    "previousStageCode", "currentStageCode", "eventIndicatorCode",
    "nationalEventCode", "eventDate", "rightTypeCode", "rightType",
    "registrationNumber", "registrationDate", "publicationNumber",
    "publicationDate", "openNumber", "openingDate", "trialNumber",
    "demurrerNumber", "supplySerialNumber",
}


def load_json(path, label):
    if not os.path.exists(path):
        return None, f"{label} 파일 없음: {path}"
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return None, f"{label} 파싱 실패: {type(e).__name__}: {e}"
    if not isinstance(data, dict):
        return None, f"{label} 형식 오류: 최상위가 객체가 아님"
    return data, None


def classify(appno, legal):
    """(verified: bool, reason: str) — fail-closed: 불확실하면 미확인."""
    if legal is None:
        return False, "legal_status.json 없음/불능"
    rec = legal.get(appno)
    if not isinstance(rec, dict):
        return False, "legal_status.json에 레코드 없음"
    if rec.get("error"):
        return False, f"법적 상태 조회 오류: {rec['error']}"
    events = rec.get("legal_events")
    if not isinstance(events, list) or not events:
        return False, "legal_events 없음/0건"
    if rec.get("last_refresh_error"):
        # 이력이 있어도 최근 재조회가 실패했으면 상태가 최신인지 알 수 없다 —
        # 법적 상태는 시점 민감(등록·소멸)이므로 판정 불가로 내린다
        return False, (f"최근 재조회 실패 — 상태가 최신인지 미확인 "
                       f"({rec['last_refresh_error']}; retrieved_at="
                       f"{rec.get('retrieved_at', '?')} 기준)")
    # 레코드 구조 검증 — kipris_legal_status.py가 쓰는 정상 레코드의 필수 필드가
    # 없거나(수기 편집·타 도구 산출물 혼입) 청구항 분리 계약을 위반하면 판정 불가
    def valid_event(ev):
        return isinstance(ev, dict) and any(
            k in ST27_EVENT_KEYS and isinstance(v, str) and v.strip()
            for k, v in ev.items())
    if not all(valid_event(ev) for ev in events):
        return False, ("legal_events에 비정상 항목(빈/비객체/유효한 ST.27 필드 값 없음) "
                       "포함 — kipris_legal_status.py 산출물이 아닌 것으로 의심")
    if rec.get("current_enforceable_claims") != "unknown":
        return False, ("current_enforceable_claims != 'unknown' — 이력 수집으로 "
                       "현재 청구항을 확정하지 않는다는 계약 위반 레코드")
    sv = rec.get("schema_version")
    # bool은 int의 하위형(True == 1)이라 명시적으로 배제한다
    if not isinstance(sv, int) or isinstance(sv, bool) or sv != 1:
        return False, (f"schema_version {sv!r} 미지원 — "
                       "이 게이트는 v1 레코드만 검증한다(게이트를 함께 갱신할 것)")
    if rec.get("status_source") != EXPECTED_STATUS_SOURCE:
        return False, (f"status_source {rec.get('status_source')!r} — 검증된 소스"
                       f"({EXPECTED_STATUS_SOURCE})의 산출물이 아님")
    retrieved = rec.get("retrieved_at")
    if not isinstance(retrieved, str):
        return False, f"retrieved_at 형식 불량: {retrieved!r} — 수집 시점 확인 불가"
    try:
        # 달력 검증까지 수행(정규식은 2026-99-99를 통과시킨다) — 앞 16자만 파싱
        datetime.datetime.strptime(retrieved[:16], "%Y-%m-%dT%H:%M")
    except ValueError:
        return False, f"retrieved_at 형식 불량: {retrieved!r} — 수집 시점 확인 불가"
    return True, f"이벤트 {len(events)}건, retrieved_at={retrieved}"


EXPECTED_EXPANSION_TOOL = "kipris_expand"
EXPECTED_EXPANSION_SCHEMA = 1


def validate_expansion(data, claims_appnos):
    """expansion.json이 **현재 claims에 대한** 정품 kipris_expand 산출물인지 검증(NO-GO #4).

    (verified_complete: bool, note: str). fail-closed: 위조·타 특허 expansion·스키마
    미달은 전부 거부한다. 아래를 모두 만족할 때만 family=complete를 인정한다:
      (a) tool == kipris_expand, schema_version == 1
      (b) seeds가 리스트이고 현재 claims의 출원번호 집합을 **포함**(claims ⊆ seeds)
      (c) family 축 source 존재 + status == complete
    """
    if not isinstance(data, dict):
        return False, "expansion.json 형식 오류: 최상위가 객체가 아님"
    if data.get("tool") != EXPECTED_EXPANSION_TOOL:
        return False, f"tool={data.get('tool')!r} — kipris_expand 산출물이 아님(위조 의심)"
    sv = data.get("schema_version")
    if not isinstance(sv, int) or isinstance(sv, bool) or sv != EXPECTED_EXPANSION_SCHEMA:
        return False, f"schema_version={sv!r} 미지원 — 게이트를 함께 갱신할 것"
    seeds = data.get("seeds")
    if not isinstance(seeds, list) or not all(isinstance(s, str) for s in seeds):
        return False, "seeds 형식 불량 — 확장 대상 문헌 집합 확인 불가"
    seed_set = {s.replace("-", "").strip() for s in seeds}
    claim_set = {a.replace("-", "").strip() for a in claims_appnos}
    missing = claim_set - seed_set
    if missing:
        return False, (f"seeds가 현재 claims를 포괄하지 않음(누락 {len(missing)}건: "
                       f"{sorted(missing)[:3]}…) — 다른 조사의 expansion이거나 seed 미포함")
    axis = (data.get("axes") or {}).get("family")
    if not isinstance(axis, dict):
        return False, "family 축 없음"
    if not axis.get("source"):
        return False, "family 축 source 없음 — 산출물 신뢰 불가"
    if axis.get("status") != "complete":
        return False, f"family status={axis.get('status')!r}"
    return True, f"후보 {axis.get('n_candidates', '?')}건"


def report_family_coverage(expansion_path, claims_appnos):
    """expansion.json을 현재 claims와 대조해 패밀리 커버리지를 표기(계약 8 + NO-GO #4).

    family가 complete로 **검증**되지 않으면 패밀리 전체 FTO 결론을 보류한다. fail-closed:
    파일 없음/파싱 실패/위조/타 특허 expansion/스키마 미달은 전부 '미확인'. KR 문헌 단위
    게이트 판정은 바꾸지 않는다."""
    data, err = load_json(expansion_path, "expansion.json")
    if err:
        print(f"== 패밀리 커버리지: 미확인 (expansion.json 불능: {err}) — "
              "패밀리 전체 FTO 결론 보류 ==")
        return
    ok, note = validate_expansion(data, claims_appnos)
    if ok:
        print(f"== 패밀리 커버리지: complete ({note}) — "
              "그래도 발견≠증거: 각 패밀리 문헌은 공식 원문으로 재확인 ==")
        return
    print(f"== 패밀리 커버리지 미확인 ({note}) — "
          "'패밀리 전체 FTO 낮음'·'해외 권리 없음' 결론 보류 ==")
    axis = (data.get("axes") or {}).get("family") if isinstance(data, dict) else None
    if isinstance(axis, dict) and axis.get("reason"):
        print(f"   사유: {axis['reason']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--claims", required=True, help="kipris_claims.py 출력 claims.json")
    ap.add_argument("--legal", required=True,
                    help="kipris_legal_status.py 출력 legal_status.json")
    ap.add_argument("--expansion", help="(선택) kipris_expand.py 출력 expansion.json — "
                    "패밀리 커버리지 미확인 시 FTO 결론 보류 표기")
    a = ap.parse_args()

    claims, err = load_json(a.claims, "claims.json")
    if err:
        print(f"게이트 실행 불능(fail-closed): {err}", file=sys.stderr)
        sys.exit(2)
    if not claims:
        print("게이트 실행 불능(fail-closed): claims.json에 문헌이 없음", file=sys.stderr)
        sys.exit(2)

    legal, err = load_json(a.legal, "legal_status.json")
    if err:
        print(f"경고: {err} — 전 문헌 상태 미확인 처리", file=sys.stderr)

    # 선택: 패밀리 커버리지 표기(KR 문헌 단위 게이트와 별개 — 판정을 바꾸지 않는다).
    # expansion을 현재 claims의 출원번호 집합과 대조해 위조·타 특허 산출물을 거부한다.
    if a.expansion:
        report_family_coverage(a.expansion, list(claims))

    verified, unverified = [], []
    for appno in sorted(claims):
        ok, reason = classify(appno, legal)
        (verified if ok else unverified).append((appno, reason))

    if verified:
        print("== FTO 관찰 판정 가능 (법적 상태 확인됨 — 판정 자체는 원문 기반으로 별도 수행) ==")
        for appno, reason in verified:
            print(f"  {appno}: {reason}")
    if unverified:
        print("== FTO 관찰 높음/낮음 판정 불가 — 상태 미확인 (확정 표현 금지) ==")
        for appno, reason in unverified:
            print(f"  {appno}: {reason}")
        print(f"게이트 미통과: {len(unverified)}/{len(claims)}건 상태 미확인 — "
              "kipris_legal_status.py로 확인 후 재실행", file=sys.stderr)
        sys.exit(2)
    print(f"게이트 통과: {len(verified)}건 전 문헌 법적 상태 확인됨")


if __name__ == "__main__":
    main()
