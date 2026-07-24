# -*- coding: utf-8 -*-
"""KIPRIS/Google Patents 요청용 리다이렉트 안전 오프너 (patent-search 공용).

기본 urllib.request.urlopen은 자동 리다이렉트를 따라간다 — KIPRIS 요청은
ServiceKey(accessKey)를 **쿼리스트링**에 싣기 때문에, 서버(또는 경로 상의
장비)가 302로 외부 호스트를 가리키면 키가 그 호스트로 새어 나간다(Codex
patent #8). 이 모듈은:
  - 자동 리다이렉트를 끄고,
  - 최초 URL과 모든 리다이렉트 홉을 요청 전에 https + 허용 호스트로 검증하며,
  - 위반 시 RedirectBlocked를 올린다(외부로 요청 자체가 나가지 않는다).
에러 메시지에는 **키가 실린 전체 URL을 넣지 않는다**(호스트만).
"""
import re
import urllib.error
import urllib.parse
import urllib.request

_REDIRECT_CODES = (301, 302, 303, 307, 308)


def key_variants(key):
    """마스킹 대상 키의 표기 변형 집합 — raw + percent/plus 인코딩(대문자 %XX)."""
    if not key:
        return set()
    return {v for v in (key, urllib.parse.quote(key, safe=""),
                        urllib.parse.quote(key), urllib.parse.quote_plus(key)) if v}


def _variant_regex_src(v):
    """키 변형 1개를 정규식 소스로 — %XX의 헥사는 **대·소문자 무관**으로 매칭한다.
    quote는 %2B를 내지만 서버가 %2b·%2B를 섞어 반사해도(예: A%2bB%2FC%3d%3D) 잡힌다
    (Codex #8b: 대/소문자 각각만 열거하면 혼합 표기가 샌다)."""
    out, i = [], 0
    while i < len(v):
        if v[i] == "%" and re.match(r"[0-9A-Fa-f]{2}$", v[i + 1:i + 3] or ""):
            h1, h2 = v[i + 1], v[i + 2]
            out.append("%[" + h1.lower() + h1.upper() + "][" + h2.lower() + h2.upper() + "]")
            i += 3
        else:
            out.append(re.escape(v[i]))
            i += 1
    return "".join(out)


def _key_pattern(key):
    variants = key_variants(key)
    if not variants:
        return None
    # 긴 변형 우선(부분 겹침 방지)
    src = "|".join(sorted((_variant_regex_src(v) for v in variants),
                          key=len, reverse=True))
    return src


def make_redact(key):
    """str용 마스킹 함수 — 키의 raw/인코딩(대소문자 혼합 %XX 포함)을 [REDACTED]로."""
    src = _key_pattern(key)
    if not src:
        return lambda text: text
    rx = re.compile(src)
    return lambda text: rx.sub("[REDACTED]", text)


def make_redact_bytes(key):
    """bytes용 마스킹 함수 — 저장 XML 등 바이트 본문의 키를 [REDACTED]로."""
    src = _key_pattern(key)
    if not src:
        return lambda body: body
    rx = re.compile(src.encode())
    return lambda body: rx.sub(b"[REDACTED]", body)
_MAX_REDIRECTS = 5


class RedirectBlocked(RuntimeError):
    """리다이렉트/최초 URL이 허용 호스트·스킴을 벗어남 — 요청을 보내지 않음."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # 3xx를 따라가지 않고 HTTPError로 표출


_OPENER = urllib.request.build_opener(_NoRedirect)


def host_ok(url, allowed_hosts):
    """https + **정확 호스트 일치만**. KIPRIS·Google Patents는 고정 단일 호스트라
    서브도메인 와일드카드를 허용하지 않는다 — attacker.plus.kipris.or.kr 같은
    하위 도메인으로 키가 새는 것을 막는다(Codex #8a). userinfo·포트 위장은
    hostname으로 차단."""
    try:
        p = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    if p.scheme != "https":
        return False
    h = (p.hostname or "").lower().rstrip(".")
    return h in allowed_hosts


def open_validated(url, timeout, allowed_hosts, maxbytes=None, headers=None):
    """리다이렉트를 끄고 각 홉을 요청 전에 검증해 최대 5홉 수동 추적한 뒤 본문
    바이트를 반환한다. maxbytes를 주면 그 이상 읽으면 절단 오류를 올린다.
    headers는 요청 헤더(예: User-Agent). RedirectBlocked/HTTPError/URLError는
    호출부가 처리한다 — 메시지에 키 URL 금지."""
    if not host_ok(url, allowed_hosts):
        raise RedirectBlocked("허용되지 않은 최초 URL host/scheme")
    for _ in range(_MAX_REDIRECTS + 1):
        req = urllib.request.Request(url, headers=headers or {})
        try:
            resp = _OPENER.open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code not in _REDIRECT_CODES:
                raise
            loc = e.headers.get("Location") if e.headers else None
            e.close()
            if not loc:
                raise RedirectBlocked("리다이렉트 Location 없음")
            nxt = urllib.parse.urljoin(url, loc)
            if not host_ok(nxt, allowed_hosts):
                # 대상 host만 노출(키 실린 쿼리는 제외)
                bad = urllib.parse.urlsplit(nxt).hostname or "?"
                raise RedirectBlocked(f"리다이렉트 대상 호스트 불허: {bad}")
            url = nxt
            continue
        with resp:
            if maxbytes is None:
                return resp.read()
            body = resp.read(maxbytes)
            if len(body) >= maxbytes:
                raise RuntimeError(f"응답이 {maxbytes} 바이트 한도에서 절단됨")
            return body
    raise RedirectBlocked(f"리다이렉트 {_MAX_REDIRECTS}홉 초과")
