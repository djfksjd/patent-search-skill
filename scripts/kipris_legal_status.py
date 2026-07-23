# -*- coding: utf-8 -*-
"""KIPRIS Plus 법적 상태 이력(legStatusST27InfoSearchService/BasicInfo) — 행정 이벤트 수집 → JSON.

사용:
  python3 kipris_legal_status.py 1020260075385 10-2024-0067638 ... [--out DIR]
  python3 kipris_legal_status.py --file appnos.txt --out ./work

엔드포인트 (2026-07-23 plus.kipris.or.kr 공식 API 명세에서 확인):
  https://plus.kipris.or.kr/openapi/rest/legStatusST27InfoSearchService/BasicInfo
    ?applicationNumber=<13자리>&accessKey=<키>
  - 상품: "법적 상태 이력(특허·실용(ST.27), 상표, 디자인(ST.87))" — 문서 URL:
    https://plus.kipris.or.kr/portal/data/service/DBII_000000000000540/view.do?menuNo=210043
    (오퍼레이션 명세: 같은 페이지의 ADI_0000000000016535 "이력 정보 조회" 탭, 확인일 2026-07-23)
  - 주의: 이 서비스는 인증 파라미터 이름이 accessKey다(patUtiModInfoSearchSevice의
    ServiceKey와 다름). 응답은 <items><legalStatusST27Info> 반복 — WIPO ST.27 이벤트 코드
    (keyEventCode/detailLawEventCode/stateCode/nationalEventCode/eventDate 등).
  - 별도 상품이므로 미가입 시 에러 31 → 이 스크립트는 종료 코드 4로 fail-closed 처리한다.

핵심 계약 (Codex 합의): "이력 수집"과 "현재 청구항 확정"을 분리한다.
  - 이 스크립트는 행정 이벤트 이력만 수집한다. 행정 이벤트만으로 최신 보정 청구항을
    재구성하지 않는다 — 출력 JSON에 current_enforceable_claims="unknown"을 유지한다.
  - FTO 표기는 fto_gate.py를 통과한 문헌에만 허용된다(SKILL.md 참조).

- 키: 환경변수 KIPRIS_KEY 또는 스크립트 상위 폴더(.env). 키는 어떤 출력에도 남기지 않는다.
- HTTPS 고정. 출원번호는 하이픈 제거 후 13자리 숫자만 허용. 429/5xx/타임아웃은 백오프 재시도.
- fail-closed: resultCode 부재(스키마 변경 신호), 이벤트 0건, 응답 출원번호 불일치는
  전부 해당 문헌 실패로 처리한다. 0건을 "법적 상태 확인됨"으로 오인하지 않는다.
- 출력: <out>/legal_status.json      문헌별 행정 이벤트 이력 — 기존 파일이 있으면 병합(누적)
        <out>/legal_<출원번호>.xml   원본 응답(공개 저장소 커밋 금지 — 조사 대상 노출)
- 종료 코드: 0=전건 성공, 1=하나 이상 실패/스키마 오류, 4=상품 미가입(에러 31).
- 정독·FTO 대상만 호출할 것 — 문헌 1건 = API 1회 (무료 쿼터 월 1,000회). 사용량은 스킬 폴더
  kipris_quota.json에 누적 기록된다.
"""
import argparse, json, os, re, sys, time
import urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET

BASE = ("https://plus.kipris.or.kr/openapi/rest/legStatusST27InfoSearchService/"
        "BasicInfo")
STATUS_SOURCE = "legStatusST27InfoSearchService/BasicInfo(법적 상태 이력, WIPO ST.27)"
ERR31_MSG = ("상품(법적 상태 이력 — 특허·실용(ST.27)) 가입 필요 — plus.kipris.or.kr "
             "마이페이지에서 'Open API > 법적 상태 이력' 상품 신청 후 재실행 "
             "(README '키 발급/상품 가입' 참고)")
MAX_BODY = 20 * 1024 * 1024
CALLS = [0]

# 이벤트 레코드로 옮길 필드 (응답 태그명 → 출력 키). 샘플 응답에서 detailLawEventCode,
# 명세 그리드에서 detailedEventCode 두 표기가 관찰됨 — 둘 다 수용한다.
EVENT_FIELDS = [
    "keyEventCode", "detailLawEventCode", "detailedEventCode", "stateCode",
    "previousStageCode", "currentStageCode", "eventIndicatorCode",
    "nationalEventCode", "eventDate", "rightTypeCode", "rightType",
    "registrationNumber", "registrationDate", "publicationNumber",
    "publicationDate", "openNumber", "openingDate", "trialNumber",
    "demurrerNumber", "supplySerialNumber",
]


def load_key(script_path):
    key = os.environ.get("KIPRIS_KEY", "").strip()
    if key:
        return key
    env = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(script_path))), ".env")
    if os.path.exists(env):
        for line in open(env, encoding="utf-8"):
            if line.startswith("KIPRIS_KEY="):
                return line.split("=", 1)[1].strip()
    sys.exit("KIPRIS_KEY 없음 — 환경변수 또는 스킬 폴더 .env에 설정 (README 참고)")


def make_redactor(key):
    variants = {key, urllib.parse.quote(key, safe=""), urllib.parse.quote(key),
                urllib.parse.quote_plus(key)}
    def redact(text):
        for v in variants:
            if v:
                text = text.replace(v, "[REDACTED]")
        return text
    return redact


def redact_body(body, key):
    for v in (key, urllib.parse.quote(key, safe=""), urllib.parse.quote(key),
              urllib.parse.quote_plus(key)):
        if v:
            body = body.replace(v.encode(), b"[REDACTED]")
    return body


def fetch(url, tries=3):
    """429/5xx/네트워크 오류만 지수 백오프 재시도. 그 외 HTTP 오류는 즉시 raise(쿼터 보호)."""
    delay, last = 1.0, None
    for _ in range(tries):
        CALLS[0] += 1
        try:
            body = urllib.request.urlopen(url, timeout=30).read(MAX_BODY)
            if len(body) >= MAX_BODY:
                raise RuntimeError("응답이 20MB 한도에서 절단됨")
            return body
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                last = e
            else:
                raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
        time.sleep(delay)
        delay *= 3
    raise last


def bump_quota(script_path, n):
    try:
        skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(script_path)))
        path = os.path.join(skill_dir, "kipris_quota.json")
        data = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
        month = time.strftime("%Y-%m")
        data[month] = int(data.get(month, 0)) + n
        # 원자적 교체 — kipris_search.py bump_quota와 동일 계약
        tmp = f"{path}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, path)
        return data[month]
    except Exception:
        return None


class NotSubscribed(RuntimeError):
    """에러 31 — 법적 상태 이력 상품 미가입."""


def parse_events(root, an):
    """응답 XML → legal_events 목록. fail-closed: 스키마 신호가 없으면 예외."""
    code = root.findtext(".//resultCode")
    if code is None:
        raise RuntimeError("schema_error: 응답에 resultCode 없음 — "
                           "API 스키마 변경/차단 의심, 원본 XML 확인")
    if code == "31":
        raise NotSubscribed(ERR31_MSG)
    if code != "00":
        raise RuntimeError(f"resultCode {code}: {root.findtext('.//resultMsg') or ''}")
    events = []
    for item in root.findall(".//legalStatusST27Info"):
        resp_an = (item.findtext("applicationNumber") or "").replace("-", "").strip()
        if not resp_an:
            # 실응답은 이벤트마다 applicationNumber를 싣는다(2026-07-23 실호출 확인) —
            # 없으면 요청 문헌 귀속을 확인할 수 없으므로 fail-closed
            raise RuntimeError("schema_error: 이벤트에 applicationNumber 없음 — "
                               "요청 문헌 귀속 확인 불가(API 스키마 변경 의심)")
        if resp_an != an:
            raise RuntimeError(f"응답 출원번호 불일치: 요청 {an} / 응답 {resp_an} — "
                               "API 동작 변경 의심")
        ev = {}
        for f in EVENT_FIELDS:
            v = (item.findtext(f) or "").strip()
            if v:
                ev[f] = v
        if ev:
            events.append(ev)
    if not events:
        raise RuntimeError("schema_error: 법적 상태 이벤트 0건 — 미공개/번호 오류/"
                           "API 스키마 변경 의심(원본 XML 확인). 0건을 '상태 확인됨'으로 "
                           "취급하지 않는다")
    return events


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("appnos", nargs="*", help="출원번호(예: 1020260075385 또는 10-2026-0075385)")
    ap.add_argument("--file", help="출원번호 목록 파일(줄당 1개, # 주석 허용)")
    ap.add_argument("--out", default=".", help="출력 폴더")
    a = ap.parse_args()

    file_targets = []
    if a.file:
        try:
            file_targets = [l.strip() for l in open(a.file, encoding="utf-8")
                            if l.strip() and not l.strip().startswith("#")]
        except OSError:
            sys.exit(f"출원번호 파일 없음/읽기 실패: {a.file}")
    raw_targets = list(a.appnos) + file_targets
    targets, bad = [], []
    for t in raw_targets:
        canon = t.replace("-", "").strip()
        if re.fullmatch(r"\d{13}", canon):
            if canon not in targets:
                targets.append(canon)
        else:
            bad.append(t)
    if bad:
        sys.exit(f"잘못된 출원번호 형식(13자리 숫자 필요): {', '.join(bad)}")
    if not targets:
        ap.error("출원번호가 없습니다")

    os.makedirs(a.out, exist_ok=True)
    key = load_key(__file__)
    redact = make_redactor(key)

    # 기존 legal_status.json이 있으면 병합(이전 실행 증거를 덮어쓰지 않는다)
    path = os.path.join(a.out, "legal_status.json")
    out = {}
    if os.path.exists(path):
        try:
            out = json.load(open(path, encoding="utf-8"))
            print(f"기존 legal_status.json에 병합(기존 {len(out)}건)", file=sys.stderr)
        except Exception:
            backup = path + ".corrupt." + time.strftime("%Y%m%d-%H%M%S")
            os.rename(path, backup)
            print(f"기존 legal_status.json 파싱 실패 — {backup}으로 보존 후 새로 생성",
                  file=sys.stderr)

    failed, not_subscribed = 0, False
    for an in targets:
        try:
            params = urllib.parse.urlencode({"applicationNumber": an, "accessKey": key})
            body = fetch(f"{BASE}?{params}")
            body = redact_body(body, key)
            with open(os.path.join(a.out, f"legal_{an}.xml"), "wb") as f:
                f.write(body)
            events = parse_events(ET.fromstring(body), an)
        except Exception as e:
            failed += 1
            if isinstance(e, NotSubscribed):
                not_subscribed = True
            err = redact(f"{type(e).__name__}: {e}")
            # 기존 정상 레코드를 오류로 덮어쓰지 않는다 (증거 보존) — 오류는 별도 키에 기록
            if isinstance(out.get(an), dict) and out[an].get("legal_events"):
                out[an]["last_refresh_error"] = err
                print(f"{an}: 재조회 실패 — 기존 정상 레코드 유지 ({err})", file=sys.stderr)
            else:
                out[an] = {"error": err}
                print(f"{an}: 실패 — {err}", file=sys.stderr)
            if not_subscribed:
                # 미가입은 전 문헌 공통 — 추가 호출은 쿼터 낭비이므로 중단.
                # 기존 정상 레코드도 이번 실행에서 재조회하지 못했음을 남긴다 —
                # 표시 없이 두면 fto_gate가 최신 상태로 오인한다(fail-closed)
                for rest in targets[targets.index(an) + 1:]:
                    if isinstance(out.get(rest), dict) and out[rest].get("legal_events"):
                        out[rest]["last_refresh_error"] = redact(
                            f"이번 실행에서 조회 생략(미가입) — {ERR31_MSG}")
                    else:
                        out[rest] = {"error": redact(ERR31_MSG)}
                    failed += 1
                break
            time.sleep(0.5)
            continue
        out[an] = {
            "schema_version": 1,
            # 계약: 이력 수집 ≠ 현재 청구항 확정 — 행정 이벤트로 보정 청구항을 재구성하지 않는다
            "current_enforceable_claims": "unknown",
            "status_source": STATUS_SOURCE,
            "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "n_events": len(events),
            "legal_events": events,
        }
        last = events[-1]
        print(f"{an} — 이벤트 {len(events)}건 "
              f"(최종 keyEventCode={last.get('keyEventCode', '?')} "
              f"eventDate={last.get('eventDate', '?')})")
        time.sleep(0.5)

    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    month_total = bump_quota(__file__, CALLS[0])
    quota_msg = f" / 이번 달 누적 {month_total}회(이 머신 기준)" if month_total else ""
    print(f"저장: {path} (성공 {len(targets)-failed} / 실패 {failed}) — "
          f"API 호출 {CALLS[0]}회{quota_msg}")
    if not_subscribed:
        print(ERR31_MSG, file=sys.stderr)
        sys.exit(4)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
