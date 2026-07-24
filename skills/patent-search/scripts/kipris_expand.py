# -*- coding: utf-8 -*-
"""KIPRIS 확장 축 (로드맵 #8) — seed 서지상세 재사용으로 family + 후방 인용(priorArt) 1-hop 확장.

사용:
  python3 kipris_expand.py 1020260075385 10-2024-0067638 ... [--out DIR]
  python3 kipris_expand.py --file seeds.txt --out ./work [--gp-discovery "검색식"]

무엇을 하는가 (Codex 머지 게이트 합의 계약):
  - **기존 API 재사용**: 새 전용 API에 의존하지 않는다. seed 출원번호마다 기존
    getBibliographyDetailInfoSearch(kipris_claims.py와 동일 엔드포인트) 응답에서
    `familyInfoArray`(패밀리)와 `priorArtDocumentsInfoArray`(심사관/출원인이 인용한
    선행문헌 = **후방 인용**)를 추출해 1-hop 확장 후보 목록을 만든다.
  - **빈 값 = unknown, not 없음**: 실 KIPRIS 응답에서 `<familyInfo/>`가 비어 오는 것이
    관찰됐다. 빈 familyInfo·필드 부재·파싱 실패는 전부 unknown/unsupported로 처리하고
    "패밀리 없음"으로 단정하지 않는다(축 status 참조).
  - **피인용(cited-by) 미지원**: 공식 피인용 오퍼레이션·실응답을 확인하지 못했다 →
    cited_by 축은 항상 status="unsupported". 0건 위장·HTML 스크레이핑 금지.
  - **인용 범위 엄격 제한**: 1-hop만 / seed 최대 5 / 패밀리 후보 최대 10 / 인용 후보 최대 20 /
    (선택) 추가 상세조회 최대 15 / 서로 다른 seed 2회 연속 신규 후보 0건이면 종료.
    상한·쿼터로 끝나면 status는 `partial(limit_reached)` — `saturated`가 아니다.
  - **쿼터 예약**: 실행 전 동시 실행 lock을 잡고, **각 네트워크 호출 직전에** 원장
    kipris_quota.json에 1회를 원자적으로 예약한다(재시도·실패도 예산 포함). 실행 상한
    기본 15회(확장 축 추가분), 월 로컬 원장 800회 hard stop(실제 잔량 확인 후 --override-monthly).
  - **발견 ≠ 증거**: 이 축들로 발견된 문헌은 후보일 뿐이다. 특허성/FTO 근거로 쓰기 전
    공식 원문·공개일·청구항·법적상태를 기존 검증 경로(kipris_claims/kipris_legal_status/
    fto_gate)로 재확인해야 한다 — 출력 JSON에 명시된다.

- 키: 환경변수 KIPRIS_KEY 또는 스킬 폴더 .env. 키는 어떤 출력에도 남기지 않는다(마스킹).
- HTTPS 고정. 출원번호는 하이픈 제거 후 13자리 숫자만 허용. 429/5xx/타임아웃은 백오프 재시도.
- 출력: <out>/expansion.json     축별 후보 + status 기계 기록
        <out>/bib_<출원번호>.xml  seed 원본 응답(공개 저장소 커밋 금지 — 조사 대상 노출)
- 종료 코드: 0=전 seed 성공, 1=하나 이상 seed 실패, 3=예산/쿼터 도달로 조기 종료(부분 결과).
"""
import argparse, contextlib, json, os, re, sys, time
import urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
import kipris_http

KIPRIS_HOSTS = ("plus.kipris.or.kr",)
GP_HOSTS = ("patents.google.com",)

try:
    import fcntl  # POSIX 파일 락(동시 실행 lock + 원장 예약 직렬화)
except ImportError:  # pragma: no cover - 비 POSIX 폴백
    fcntl = None

BASE = ("https://plus.kipris.or.kr/kipo-api/kipi/patUtiModInfoSearchSevice/"
        "getBibliographyDetailInfoSearch")
# 패밀리 문헌번호로 인정하는 알려진 필드명(정확 추출 — 미지 필드를 채택하지 않는다)
FAMILY_NUM_FIELDS = ("applicationNumber", "familyApplicationNumber", "documentNumber",
                     "familyDocumentNumber", "publicationNumber", "registrationNumber",
                     "number")
FAMILY_SOURCE = "getBibliographyDetailInfoSearch: familyInfoArray/familyInfo"
PRIORART_SOURCE = "getBibliographyDetailInfoSearch: priorArtDocumentsInfoArray/priorArtDocumentsInfo"
MAX_BODY = 20 * 1024 * 1024
CALLS = [0]

# 계약 상한(계약 3·5) — **코드 상수로 고정**. CLI 인자는 이 상한을 낮추는 방향으로만
# 반영된다(effective = min(요청, 상수)). 상향은 허용하지 않는다 — 월 hard stop만
# --override-monthly(실제 잔량 확인 후)로 넘길 수 있다. NO-GO #1 대응.
HARD_MAX_SEEDS = 5
HARD_MAX_CALLS = 15          # 확장 축 추가분(전체 파이프라인 40회의 일부 — SKILL.md 참조)
HARD_MAX_FAMILY = 10
HARD_MAX_CITATION = 20
HARD_MONTHLY_STOP = 800


class BudgetExhausted(RuntimeError):
    """이번 실행의 호출 상한(--max-calls) 도달 — partial(limit_reached)."""


class QuotaHardStop(RuntimeError):
    """월 로컬 원장 hard stop 도달 — partial(limit_reached)."""


class RunLocked(RuntimeError):
    """다른 kipris_expand 실행이 lock을 점유 중."""


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
    # 대소문자 혼합 %XX 인코딩까지 잡는 정규식 마스킹(kipris_http.make_redact)
    return kipris_http.make_redact(key)


def redact_body(body, key):
    return kipris_http.make_redact_bytes(key)(body)


@contextlib.contextmanager
def file_lock(lock_path, timeout=15.0):
    """POSIX flock 기반 배타 락. fcntl이 없으면(비 POSIX) no-op(락 없이 진행)."""
    if fcntl is None:  # pragma: no cover
        yield
        return
    f = open(lock_path, "w")
    start = time.time()
    try:
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.time() - start > timeout:
                    raise RuntimeError(f"락 대기 시간 초과: {lock_path} — 다른 실행이 사용 중")
                time.sleep(0.1)
        yield
    finally:
        try:
            fcntl.flock(f, fcntl.LOCK_UN)
        except OSError:
            pass
        f.close()


@contextlib.contextmanager
def acquire_run_lock(skill_dir):
    """확장 실행 전체를 감싸는 동시 실행 lock — 이미 다른 실행이 잡고 있으면 RunLocked."""
    if fcntl is None:  # pragma: no cover
        yield
        return
    lock_path = os.path.join(skill_dir, "kipris_expand.lock")
    f = open(lock_path, "w")
    try:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RunLocked("다른 kipris_expand 실행이 진행 중 — 동시 실행 금지(쿼터 보호)")
        yield
    finally:
        try:
            fcntl.flock(f, fcntl.LOCK_UN)
        except OSError:
            pass
        f.close()


def reserve_one(skill_dir, monthly_cap, override):
    """네트워크 호출 직전 원장에 1회를 원자적으로 예약한다(계약 5: 요청 전 예약).

    - 원장 락 하에서 read-modify-write → 동시 실행 간 증가분 유실 방지.
    - 월 누적 + 1 > monthly_cap 이고 override 아니면 QuotaHardStop(호출 안 함).
    - pid-unique tmp + os.replace 로 원자 교체(기존 bump_quota 규약 재사용·확장).
    - 반환: 예약 후 이번 달 누적치.
    """
    path = os.path.join(skill_dir, "kipris_quota.json")
    with file_lock(os.path.join(skill_dir, "kipris_quota.lock")):
        data = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
        month = time.strftime("%Y-%m")
        cur = int(data.get(month, 0))
        if not override and cur + 1 > monthly_cap:
            raise QuotaHardStop(
                f"월 로컬 원장 {cur}/{monthly_cap}회 — hard stop. 실제 잔량을 KIPRIS Plus "
                "마이페이지에서 확인 후 --override-monthly로만 진행")
        data[month] = cur + 1
        tmp = f"{path}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, path)
        return data[month]


def fetch(url, reserve, tries=3):
    """각 시도(재시도 포함) 직전에 reserve()로 예산을 예약한다 — 실패·재시도도 예산 포함.

    reserve()가 BudgetExhausted/QuotaHardStop을 던지면 즉시 전파(호출하지 않고 중단).
    429/5xx/네트워크 오류만 지수 백오프 재시도. 그 외 HTTP 오류는 즉시 raise(쿼터 보호).
    """
    delay, last = 1.0, None
    for _ in range(tries):
        reserve()  # 요청 전 원자적 예약(계약 5)
        CALLS[0] += 1
        try:
            return kipris_http.open_validated(url, 30, KIPRIS_HOSTS, MAX_BODY)
        except kipris_http.RedirectBlocked:
            raise  # 외부 호스트 유출 시도 — 재시도 없이 즉시 실패
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


def normalize_doc_number(raw):
    """문헌번호 문자열 → {raw, country, digits, kind, normalized_appno}.

    예: "KR1020210147858 A" → country=KR, digits=1020210147858, kind=A,
        normalized_appno=1020210147858(KR 13자리이므로 국내 출원번호로 정규화).
    KR가 아니거나 13자리가 아니면 normalized_appno=None(직접 재조회 대상 아님).
    """
    s = (raw or "").strip()
    compact = re.sub(r"\s+", "", s)
    m = re.match(r"^([A-Za-z]{2})?(\d+)([A-Za-z]\d?)?$", compact)
    if m:
        country = m.group(1).upper() if m.group(1) else None
        digits = m.group(2)
        kind = m.group(3).upper() if m.group(3) else None
    else:
        country, digits, kind = None, re.sub(r"\D", "", compact), None
    normalized = digits if (country in (None, "KR") and len(digits) == 13) else None
    return {"raw": s, "country": country, "digits": digits, "kind": kind,
            "normalized_appno": normalized}


def parse_seed(root, an):
    """seed 응답 XML → (family_list, prior_art_list). fail-closed: 스키마 신호 없으면 예외.

    family_list: familyInfo 요소별 dict. 비어있으면(자식 없음/전부 공백) family_empty 신호로
                 빈 dict를 포함하지 않고, 대신 family_present 플래그로 구분한다.
    반환 dict:
      {"family": [{...}...], "family_had_element": bool, "family_all_empty": bool,
       "prior_art": [{documentsNumber, examinerQuotationFlag, examiner_cited, ...}...]}
    """
    code = root.findtext(".//resultCode")
    if code is None:
        raise RuntimeError("schema_error: 응답에 resultCode 없음 — "
                           "API 스키마 변경/차단 의심, 원본 XML 확인")
    if code != "00":
        raise RuntimeError(f"resultCode {code}: {root.findtext('.//resultMsg') or ''}")
    # NO-GO #3(fail-closed): 응답 출원번호는 **존재하고 요청과 일치**해야 한다. 부재는
    # 요청 문헌 귀속을 확인할 수 없으므로 실패(fail-open 금지 — kipris_claims/legal 패턴 이식).
    resp_an = (root.findtext(".//applicationNumber") or "").replace("-", "").strip()
    if not resp_an:
        raise RuntimeError("schema_error: 응답에 applicationNumber 없음 — "
                           "요청 문헌 귀속 확인 불가(API 스키마 변경/차단 의심)")
    if resp_an != an:
        raise RuntimeError(f"응답 출원번호 불일치: 요청 {an} / 응답 {resp_an} — API 동작 변경 의심")

    # --- 패밀리 ---
    # 문헌번호는 **알려진 필드명에서만** 추출한다. 미지 구조(예: <unexpected>garbage</>)를
    # 첫 값으로 채택하지 않는다 — 그런 요소는 family_unparsed로 표시해 unknown으로 흐르게 한다.
    family_elems = root.findall(".//familyInfoArray/familyInfo")
    family, nonempty, family_unparsed = [], 0, False
    for fe in family_elems:
        fields = {c.tag: (c.text or "").strip() for c in fe if (c.text or "").strip()}
        if not fields:
            continue  # <familyInfo/> 등 빈 요소 — "패밀리 없음"으로 단정하지 않는다(unknown)
        nonempty += 1
        num = ""
        for k in FAMILY_NUM_FIELDS:
            if fields.get(k):
                num = fields[k]
                break
        if not num:
            # 내용은 있으나 알려진 문헌번호 필드가 없다 → 미지 구조. 가짜 후보를 만들지 않고
            # unknown 신호만 남긴다(garbage가 complete로 흐르지 못하게 — NO-GO #3).
            family_unparsed = True
            continue
        norm = normalize_doc_number(num)
        country = (fields.get("countryCode") or fields.get("country")
                   or norm["country"])
        family.append({"document": num, "fields": fields, "country": country,
                       "normalized_appno": norm["normalized_appno"]})
    result = {
        "family": family,
        "family_had_element": bool(family_elems),
        "family_all_empty": bool(family_elems) and nonempty == 0,
        # 내용은 있으나 알려진 번호 필드가 없는 familyInfo가 있었는가 → 커버리지 unknown
        "family_unparsed": family_unparsed,
    }

    # --- 후방 인용(priorArt) ---
    # documentsNumber에서만 정확 추출한다. 내용이 있는데 documentsNumber가 없는 요소는
    # 미지 구조 → prior_art_unparsed로 표시(unknown으로 흐르게). 완전 빈 요소는 무시.
    prior, prior_unparsed = [], False
    # 컨테이너(priorArtDocumentsInfoArray) 자체의 존재 여부를 기록한다 — 부재는
    # "이 응답이 인용 정보를 담지 않음"(unknown)이고, 컨테이너가 있는데 0건일 때만
    # 인용 0건 complete로 본다(계약 2의 빈 값≠없음 구분, family와 동일).
    prior_had_container = root.find(".//priorArtDocumentsInfoArray") is not None
    for pe in root.findall(".//priorArtDocumentsInfoArray/priorArtDocumentsInfo"):
        docnum = (pe.findtext("documentsNumber") or "").strip()
        if not docnum:
            has_content = any((c.text or "").strip() for c in pe)
            if has_content:
                prior_unparsed = True  # documentsNumber 없는 비어있지 않은 요소 — 스키마 이상
            continue
        flag = (pe.findtext("examinerQuotationFlag") or "").strip()
        norm = normalize_doc_number(docnum)
        prior.append({
            "documentsNumber": docnum,
            "examinerQuotationFlag": flag,
            "examiner_cited": flag.upper() == "Y",
            "country": norm["country"],
            "normalized_appno": norm["normalized_appno"],
        })
    result["prior_art"] = prior
    result["prior_art_unparsed"] = prior_unparsed
    result["prior_art_had_container"] = prior_had_container
    return result


def build_axes(seeds, seed_records, seed_errors, limits, dropped_seeds,
               termination_reason, budget_stop, raw_paths, checked_at):
    """수집 결과 → 축별 status 기계 기록(계약 6). unsupported/failed를 빈 성공으로 병합 금지."""
    n_seeds = len(seeds)
    n_ok = sum(1 for an in seeds if an in seed_records)
    n_failed = len(seed_errors)
    limit_hit = termination_reason in ("run_budget", "monthly_hard_stop",
                                       "candidate_limit_reached") or budget_stop \
        or dropped_seeds > 0

    # ----- family 후보 집계 -----
    family_candidates, seen_fam = [], set()
    fam_limit_hit = False
    any_empty = dropped_seeds > 0  # 처리 못 한 seed의 패밀리는 미확인
    any_docs = False
    for an in seeds:
        rec = seed_records.get(an)
        if rec is None:
            any_empty = True  # 실패 seed → 그 문헌 패밀리 미확인
            continue
        if rec["family_all_empty"] or not rec["family_had_element"] or rec.get("family_unparsed"):
            any_empty = True  # 빈/부재/미지 구조 → 커버리지 미확인
        for f in rec["family"]:
            any_docs = True
            key = f["normalized_appno"] or f["document"]
            if key in seen_fam:
                continue
            if len(family_candidates) >= limits["max_family_candidates"]:
                fam_limit_hit = True
                break
            seen_fam.add(key)
            family_candidates.append({**f, "from_seed": an})
        if fam_limit_hit:
            break

    if n_ok == 0:
        fam_status = "failed"
    elif fam_limit_hit or limit_hit:
        fam_status = "partial"
    elif any_empty or n_failed:
        # 빈 familyInfo·미처리 seed가 하나라도 있으면 패밀리 커버리지 미확인(계약 2·8)
        fam_status = "unknown"
    elif any_docs:
        fam_status = "complete"
    else:
        fam_status = "unknown"
    fam_reason = ("빈 familyInfo/필드 부재를 '패밀리 없음'으로 단정하지 않는다 — "
                  "비어있지 않은 실 응답 fixture로 검증 전까지 unknown. FTO 패밀리 결론 보류.")

    # ----- priorArt(후방 인용) 후보 집계 -----
    citation_candidates, seen_cit = [], set()
    cit_limit_hit = False
    for an in seeds:
        rec = seed_records.get(an)
        if rec is None:
            continue
        for p in rec["prior_art"]:
            key = p["normalized_appno"] or p["documentsNumber"]
            if key in seen_cit:
                continue
            if len(citation_candidates) >= limits["max_citation_candidates"]:
                cit_limit_hit = True
                break
            seen_cit.add(key)
            citation_candidates.append({**p, "from_seed": an})
        if cit_limit_hit:
            break

    pa_unparsed = any(seed_records[an].get("prior_art_unparsed")
                      for an in seeds if an in seed_records)
    # priorArt 컨테이너가 아예 없던 seed가 하나라도 있으면 커버리지 unknown —
    # 컨테이너 부재를 "인용 0건 complete"로 오인하면 fail-open(Codex 잔여 #1)
    pa_missing_container = any(
        not seed_records[an].get("prior_art_had_container")
        for an in seeds if an in seed_records)
    if n_ok == 0:
        pa_status = "failed"
    elif cit_limit_hit or limit_hit:
        pa_status = "partial"
    elif pa_unparsed or pa_missing_container:
        # documentsNumber 없는 비어있지 않은 요소(스키마 이상) 또는 컨테이너 부재
        # → complete 금지, unknown(계약 2). 컨테이너가 있으면서 0건일 때만 complete.
        pa_status = "unknown"
    elif n_failed:
        pa_status = "partial"
    else:
        pa_status = "complete"
    pa_reason = ("실 KIPRIS 응답에서 documentsNumber/examinerQuotationFlag가 채워짐이 확인된 축 "
                 "(후방 인용). priorArt 0건은 이 응답에 인용 문헌이 없다는 뜻일 뿐, 다른 경로의 "
                 "인용 부재를 보장하지 않는다.")

    def axis(status, source, applied_limit, reason, candidates, axis_limit_hit):
        # NO-GO #2: 축별 절단은 축 고유의 'limit_reached'로 기록한다 — main 공통 사유
        # (all_seeds_processed 등)로 덮어쓰지 않는다.
        axis_term = "limit_reached" if axis_limit_hit else termination_reason
        return {
            "status": status,
            "source": source,
            "checked_at": checked_at,
            "n_seeds": n_seeds,
            "n_seeds_ok": n_ok,
            "n_seeds_failed": n_failed,
            "n_calls": CALLS[0],
            "applied_limit": applied_limit,
            "termination_reason": axis_term,
            "raw_response_paths": raw_paths,
            "n_candidates": len(candidates),
            "reason": reason,
            "candidates": candidates,
        }

    return {
        "family": axis(fam_status, FAMILY_SOURCE, limits["max_family_candidates"],
                       fam_reason, family_candidates, fam_limit_hit or limit_hit),
        "prior_art_backward": axis(pa_status, PRIORART_SOURCE,
                                   limits["max_citation_candidates"], pa_reason,
                                   citation_candidates, cit_limit_hit or limit_hit),
        "cited_by": {
            "status": "unsupported",
            "source": None,
            "checked_at": checked_at,
            "n_seeds": n_seeds,
            "n_calls": 0,
            "applied_limit": None,
            "termination_reason": "no_verified_operation",
            "raw_response_paths": [],
            "n_candidates": 0,
            "reason": ("공식 피인용(cited-by) 오퍼레이션·실응답을 확인하지 못했다. "
                       "0건 위장·HTML 스크레이핑 금지 — 필요 시 KIPRIS 웹 인용정보에서 수동 확인."),
            "candidates": [],
        },
    }


def gp_discovery(query, out_dir, redact, timeout=8.0, tries=2):
    """Google Patents xhr — discovery_only 격리 어댑터(계약 9). 핵심 의존 아님.

    명시적 옵션(--gp-discovery)으로만 호출. 짧은 timeout·최대 2회 시도. 원본 JSON·스키마
    지문 보존. 차단 우회·HTML 스크레이핑 금지 — 차단/실패해도 KIPRIS 결과는 유지하되
    전체 검색 포화 선언은 막는다(status가 complete가 아니면 포화 아님).
    """
    url = ("https://patents.google.com/xhr/query?url="
           + urllib.parse.quote(f"q={query}", safe=""))
    checked_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    last = None
    for _ in range(tries):
        try:
            # 리다이렉트 차단·호스트 검증(patents.google.com만). 키 미포함이라
            # 유출 위험은 없으나 임의 호스트로의 리다이렉트는 막는다.
            body = kipris_http.open_validated(
                url, timeout, GP_HOSTS, MAX_BODY,
                headers={"User-Agent": "patent-search-skill"})
            text = body.decode("utf-8", "replace")
            if "Sorry" in text[:2000] or not text.lstrip().startswith("{"):
                # 차단 페이지(HTML) — 우회하지 않는다
                return {"status": "failed", "source": "patents.google.com/xhr/query",
                        "checked_at": checked_at, "reason": "차단/비 JSON 응답 — 우회 금지",
                        "schema_fingerprint": None, "n_candidates": 0, "candidates": []}
            data = json.loads(text)
            raw_path = os.path.join(out_dir, f"gp_discovery_{time.strftime('%Y%m%d-%H%M%S')}.json")
            with open(raw_path, "w", encoding="utf-8") as f:
                f.write(redact(text))
            results = (data.get("results") or {}).get("cluster") or []
            cands = []
            for cl in results:
                for r in (cl.get("result") or []):
                    pat = (r.get("patent") or {})
                    num = pat.get("publication_number") or pat.get("pn")
                    if num:
                        cands.append({"document": num, "title": pat.get("title")})
            return {"status": "discovery_only", "source": "patents.google.com/xhr/query",
                    "checked_at": checked_at, "raw_response_path": raw_path,
                    "schema_fingerprint": sorted(data.keys()),
                    "reason": ("발견 전용 — 검증 안 된 비공식 엔드포인트. 후보는 공식 원문으로 "
                               "재확인 필요. 이 축만으로 검색 포화를 선언하지 않는다."),
                    "n_candidates": len(cands), "candidates": cands[:20]}
        except Exception as e:  # noqa: BLE001 — 어떤 실패든 KIPRIS 결과는 유지
            last = redact(f"{type(e).__name__}: {e}")
        time.sleep(0.5)
    return {"status": "failed", "source": "patents.google.com/xhr/query",
            "checked_at": checked_at, "reason": f"조회 실패(우회 금지): {last}",
            "schema_fingerprint": None, "n_candidates": 0, "candidates": []}


def parse_seed_args(a):
    file_targets = []
    if a.file:
        try:
            file_targets = [l.strip() for l in open(a.file, encoding="utf-8")
                            if l.strip() and not l.strip().startswith("#")]
        except OSError:
            sys.exit(f"seed 파일 없음/읽기 실패: {a.file}")
    raw = list(a.seeds) + file_targets
    seeds, bad = [], []
    for t in raw:
        canon = t.replace("-", "").strip()
        if re.fullmatch(r"\d{13}", canon):
            if canon not in seeds:
                seeds.append(canon)
        else:
            bad.append(t)
    if bad:
        sys.exit(f"잘못된 출원번호 형식(13자리 숫자 필요): {', '.join(bad)}")
    if not seeds:
        sys.exit("seed 출원번호가 없습니다")
    return seeds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("seeds", nargs="*", help="seed 출원번호(예: 1020260075385)")
    ap.add_argument("--file", help="seed 목록 파일(줄당 1개, # 주석 허용)")
    ap.add_argument("--out", default=".", help="출력 폴더")
    ap.add_argument("--max-seeds", type=int, default=HARD_MAX_SEEDS,
                    help=f"seed 상한(계약 {HARD_MAX_SEEDS} — 낮추는 방향만 반영)")
    ap.add_argument("--max-calls", type=int, default=HARD_MAX_CALLS,
                    help=f"이번 실행 API 호출 상한(계약 {HARD_MAX_CALLS} — 낮추는 방향만 반영)")
    ap.add_argument("--max-family-candidates", type=int, default=HARD_MAX_FAMILY,
                    help=f"패밀리 후보 상한(계약 {HARD_MAX_FAMILY} — 낮추는 방향만 반영)")
    ap.add_argument("--max-citation-candidates", type=int, default=HARD_MAX_CITATION,
                    help=f"인용 후보 상한(계약 {HARD_MAX_CITATION} — 낮추는 방향만 반영)")
    ap.add_argument("--monthly-hard-stop", type=int, default=HARD_MONTHLY_STOP,
                    help=f"월 원장 hard stop(계약 {HARD_MONTHLY_STOP} — --override-monthly "
                         "없이는 상향 불가)")
    ap.add_argument("--override-monthly", action="store_true",
                    help="월 원장 hard stop을 넘어 진행/상향(실제 잔량 확인 후에만)")
    ap.add_argument("--gp-discovery", metavar="QUERY",
                    help="Google Patents discovery_only 폴백(격리 — 핵심 의존 아님)")
    ap.add_argument("--force", action="store_true", help="기존 expansion.json 덮어쓰기")
    a = ap.parse_args()
    if min(a.max_seeds, a.max_calls, a.max_family_candidates,
           a.max_citation_candidates, a.monthly_hard_stop) < 1:
        ap.error("모든 상한 인자는 1 이상")

    # 계약 상한 고정: CLI는 낮추는 방향만 반영한다(effective = min(요청, 상수)) — NO-GO #1.
    def clamp(name, requested, hard):
        eff = min(requested, hard)
        if requested > hard:
            print(f"경고: --{name} {requested} > 계약 상한 {hard} — {hard}으로 고정",
                  file=sys.stderr)
        return eff

    max_seeds = clamp("max-seeds", a.max_seeds, HARD_MAX_SEEDS)
    max_calls = clamp("max-calls", a.max_calls, HARD_MAX_CALLS)
    max_family = clamp("max-family-candidates", a.max_family_candidates, HARD_MAX_FAMILY)
    max_citation = clamp("max-citation-candidates", a.max_citation_candidates, HARD_MAX_CITATION)
    # 월 hard stop은 override 없이는 절대 상향 불가(계약 5). override면 요청값 그대로 허용.
    if a.override_monthly:
        monthly_stop = a.monthly_hard_stop
    else:
        monthly_stop = min(a.monthly_hard_stop, HARD_MONTHLY_STOP)
        if a.monthly_hard_stop > HARD_MONTHLY_STOP:
            print(f"경고: --monthly-hard-stop {a.monthly_hard_stop} > 계약 {HARD_MONTHLY_STOP} — "
                  f"{HARD_MONTHLY_STOP}으로 고정(상향은 --override-monthly 필요)", file=sys.stderr)

    seeds_all = parse_seed_args(a)
    dropped_seeds = max(0, len(seeds_all) - max_seeds)
    seeds = seeds_all[:max_seeds]

    os.makedirs(a.out, exist_ok=True)
    exp_path = os.path.join(a.out, "expansion.json")
    if os.path.exists(exp_path) and not a.force:
        sys.exit(f"기존 산출물 존재: {exp_path} — 다른 --out 또는 --force(증거 보존)")
    key = load_key(__file__)
    redact = make_redactor(key)
    skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    limits = {"max_seeds": max_seeds, "max_calls": max_calls,
              "max_family_candidates": max_family,
              "max_citation_candidates": max_citation,
              "monthly_hard_stop": monthly_stop,
              "override_monthly": a.override_monthly,
              "hard_caps": {"max_seeds": HARD_MAX_SEEDS, "max_calls": HARD_MAX_CALLS,
                            "max_family_candidates": HARD_MAX_FAMILY,
                            "max_citation_candidates": HARD_MAX_CITATION,
                            "monthly_hard_stop": HARD_MONTHLY_STOP}}

    seed_records, seed_errors, raw_paths, reused_seeds = {}, [], [], []
    consecutive_zero, termination_reason, budget_stop = 0, "all_seeds_processed", False
    month_total = [None]
    run_calls = [0]

    def reserve():
        if run_calls[0] >= max_calls:
            raise BudgetExhausted(f"이번 실행 호출 상한 {max_calls}회 도달")
        month_total[0] = reserve_one(skill_dir, monthly_stop, a.override_monthly)
        run_calls[0] += 1

    try:
        with acquire_run_lock(skill_dir):
            prev_seen = set()  # 지금까지 발견한 후보 키(신규 판정용)
            for an in seeds:
                rp = os.path.join(a.out, f"bib_{an}.xml")
                try:
                    # 계약 1: 기존 seed 서지 응답(bib_<출원번호>.xml)이 있으면 재파싱 재사용
                    # — 재호출·덮어쓰기 금지(쿼터 절약·증거 보존). NO-GO #5.
                    if os.path.exists(rp):
                        with open(rp, "rb") as f:
                            body = f.read()
                        reused_seeds.append(an)
                        print(f"{an}: 기존 bib_{an}.xml 재사용(재호출 안 함)")
                    else:
                        params = urllib.parse.urlencode(
                            {"applicationNumber": an, "ServiceKey": key})
                        body = fetch(f"{BASE}?{params}", reserve)
                        body = redact_body(body, key)
                        with open(rp, "wb") as f:
                            f.write(body)
                    raw_paths.append(f"bib_{an}.xml")
                    rec = parse_seed(ET.fromstring(body), an)
                except (BudgetExhausted, QuotaHardStop) as e:
                    budget_stop = True
                    termination_reason = ("run_budget" if isinstance(e, BudgetExhausted)
                                          else "monthly_hard_stop")
                    print(f"{an}: 예산/쿼터 도달로 중단 — {redact(str(e))}", file=sys.stderr)
                    break
                except Exception as e:  # noqa: BLE001
                    seed_errors.append({"seed": an, "error": redact(f"{type(e).__name__}: {e}")})
                    print(f"{an}: 실패 — {redact(str(e))}", file=sys.stderr)
                    continue
                seed_records[an] = rec
                # 신규 후보(패밀리+인용) 수 = 이번 seed가 새로 추가한 서로 다른 문헌 키
                keys = set()
                for f in rec["family"]:
                    keys.add(f["normalized_appno"] or f["document"])
                for p in rec["prior_art"]:
                    keys.add(p["normalized_appno"] or p["documentsNumber"])
                new_keys = keys - prev_seen
                prev_seen |= keys
                fam_n = len(rec["family"])
                pa_n = len(rec["prior_art"])
                print(f"{an}: 패밀리 {fam_n}건"
                      + (" (빈 familyInfo — unknown)" if rec["family_all_empty"] else "")
                      + f" / 후방 인용 {pa_n}건 / 신규 후보 {len(new_keys)}건")
                # 후보 상한 도달 확인(계약 3)
                if len(prev_seen) >= (max_family + max_citation):
                    termination_reason = "candidate_limit_reached"
                    print("후보 상한 도달 — 종료(partial: limit_reached)", file=sys.stderr)
                    break
                if not new_keys:
                    consecutive_zero += 1
                    if consecutive_zero >= 2:
                        termination_reason = "two_consecutive_zero_new"
                        print("서로 다른 seed 2회 연속 신규 후보 0건 — 종료", file=sys.stderr)
                        break
                else:
                    consecutive_zero = 0
    except RunLocked as e:
        sys.exit(f"동시 실행 차단: {e}")

    if dropped_seeds and termination_reason == "all_seeds_processed":
        termination_reason = "candidate_limit_reached"  # seed 상한 초과분 존재 → 미완

    checked_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    axes = build_axes(seeds, seed_records, seed_errors, limits, dropped_seeds,
                      termination_reason, budget_stop, raw_paths, checked_at)

    if a.gp_discovery:
        axes["google_patents_discovery"] = gp_discovery(a.gp_discovery, a.out, redact)

    out = {
        "tool": "kipris_expand",
        "schema_version": 1,
        "endpoint": "getBibliographyDetailInfoSearch(https, 기존 API 재사용)",
        "run_ts": time.strftime("%Y%m%d-%H%M%S"),
        "seeds": seeds,
        "seeds_requested": seeds_all,
        "dropped_seeds": dropped_seeds,
        "reused_seeds": reused_seeds,  # 기존 bib_<출원번호>.xml 재사용(재호출 안 함 — 계약 1)
        "limits": limits,
        "api_calls": CALLS[0],
        "month_calls_estimate": month_total[0],
        "termination_reason": termination_reason,
        "seed_errors": seed_errors,
        "axes": axes,
        "discovery_not_evidence": (
            "이 축들로 발견된 문헌은 후보일 뿐이다. 특허성·FTO 근거로 쓰기 전 공식 원문·"
            "공개일·청구항·법적상태를 kipris_claims/kipris_legal_status/fto_gate로 재확인할 것."),
        "family_coverage_note": (
            "family 축 status가 complete가 아니면 '패밀리 전체 FTO 낮음'·'해외 권리 없음' 결론 금지 "
            "→ '패밀리 커버리지 미확인'으로 보류(fto_gate --expansion 참조)."),
    }
    with open(exp_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    quota_msg = f" / 이번 달 누적 {month_total[0]}회(이 머신 기준)" if month_total[0] else ""
    print(f"저장: {exp_path} — seed {len(seed_records)}/{len(seeds)} 성공, "
          f"API {CALLS[0]}회{quota_msg}, 종료 사유={termination_reason}")
    print(f"  family={axes['family']['status']} "
          f"prior_art={axes['prior_art_backward']['status']} "
          f"cited_by={axes['cited_by']['status']}")
    if budget_stop:
        sys.exit(3)  # 예산/쿼터 도달 조기 종료(부분 결과)
    if seed_errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
