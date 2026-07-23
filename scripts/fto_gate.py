# -*- coding: utf-8 -*-
"""FTO 게이트 — fail-closed validator (네트워크 없음).

사용:
  python3 fto_gate.py --claims <작업폴더>/claims.json --legal <작업폴더>/legal_status.json

claims.json(kipris_claims.py)의 문헌 각각에 대해 legal_status.json(kipris_legal_status.py)의
법적 상태 확인 여부를 검사한다:

- 상태 확인됨   = legal_status.json에 해당 출원번호의 정상 레코드(legal_events 1건 이상,
                  error 없음)가 있다 → "FTO 관찰 판정 가능" 목록.
- 상태 미확인   = legal_status.json 자체가 없거나, 레코드가 없거나, error 레코드다
                  → "FTO 관찰 높음/낮음 판정 불가 — 상태 미확인" 목록.

종료 코드: 0=전 문헌 상태 확인됨(판정 가능), 2=하나 이상 상태 미확인(또는 입력 불능).
리포트 작성 시 이 게이트를 통과하지 못한 문헌에는 FTO 관찰 높음/낮음 등 확정 표현을
쓰지 못한다(SKILL.md 참조). 판정 가능 ≠ 판정 결과 — 판정 자체는 사람이 원문으로 한다.

주의: 이 게이트는 "법적 상태 이력을 확인했는가"만 검사한다. 현재 유효 청구항 확정은
별개 문제다(legal_status.json의 current_enforceable_claims="unknown" 유지 계약).
"""
import argparse
import json
import os
import sys


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
        return True, (f"이벤트 {len(events)}건 (경고: 최근 재조회 실패 — "
                      f"{rec['last_refresh_error']}; retrieved_at="
                      f"{rec.get('retrieved_at', '?')} 기준)")
    return True, f"이벤트 {len(events)}건, retrieved_at={rec.get('retrieved_at', '?')}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--claims", required=True, help="kipris_claims.py 출력 claims.json")
    ap.add_argument("--legal", required=True,
                    help="kipris_legal_status.py 출력 legal_status.json")
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
