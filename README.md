# patent-search — Claude Code 선행기술조사 스킬

[![tests](https://github.com/djfksjd/patent-search-skill/actions/workflows/tests.yml/badge.svg)](https://github.com/djfksjd/patent-search-skill/actions/workflows/tests.yml)

발명 아이템을 주면 **선행 특허 조사 리포트 + claim chart + 청구항 차별화 가이드**를 만들어 주는 [Claude Code](https://claude.com/claude-code) 스킬입니다. KIPRIS(한국)와 Google Patents(글로벌)를 조사해 **특허성(신규성·진보성) 관련성**과 **예비 FTO(Freedom-to-Operate) 관찰 필요도**를 분리해서 판정합니다.

> ⚠️ **면책**: 이 스킬의 산출물은 법률 자문이 아닙니다. 변리사의 선행기술조사·감정을 대체하지 않으며, 실제 출원·사업화 전에는 반드시 변리사 검토를 받으세요. 리포트의 모든 판정은 "관련성 높음 / 관찰 필요" 수준의 참고 정보입니다.

## 무엇을 해주나

- **조사 설계 인터뷰**: 조사 목적(특허성/예비 FTO/경쟁 동향), 출원국, 예상 출원 시점(기준일), 기존 공개 여부(공지예외)를 먼저 확인
- **구성요소 분해**: 발명을 "구성요소 + 요소 간 관계" 단위로 원자화한 분해표 작성
- **검색**: KIPRIS Plus API(한/영 키워드, IPC 확장, 출원인 축) + Google Patents 검색, 모든 검색식·시각·결과 수 기록(재현성)
- **정독**: 정독 대상 문헌의 청구항 전문·명세서를 원문으로 확인, 근거 인용(문헌번호+문단/청구항+인용+URL+확인일) 강제
- **Claim chart**: `내 구성요소 × 선행문헌` 매트릭스 (E 명시 개시 / I 내재 개시 / P 부분·상위개념 / N 미발견 / ?)
- **판정 2축 분리**: 특허성 축(단일 문헌 전 한정 개시 여부, 진보성 결합 논거)과 FTO 축(살아있는 독립항 관찰 필요도)을 섞지 않음
- **차별화 가이드**: 독립항 후보 / 종속항 fallback / 실시예 보강 구분, 회피설계 옵션, 진보성 논거(효과 실측 숙제 목록 포함)
- **diff 재조사 모드**: 이전 리포트의 검색식을 overlap 기간을 두고 재실행해 증분(신규 문헌·법적 상태 변화·보정)만 비교

## 설치

**한 줄 설치** — 설치된 호스트(claude/codex/agy/gemini)를 자동 감지해 전부 설치하고, CLI가 없으면 `~/.agents/skills/`에 clone(Cursor·Grok Build용)합니다. 스크립트는 Python 3 표준 라이브러리만 쓰므로 별도 런타임 의존성 설치가 없습니다:

```bash
curl -fsSL https://raw.githubusercontent.com/djfksjd/patent-search-skill/main/install.sh | bash
```

수동으로 하려면 아래에서 쓰는 에이전트의 방법을 고르세요 — 한 트리로 모든 호스트를 지원합니다(스킬 본체는 `skills/patent-search/` 아래에 있습니다).

### Claude Code

```bash
claude plugin marketplace add djfksjd/patent-search-skill
claude plugin install patent-search@djfksjd
```

### Codex

```bash
codex plugin marketplace add djfksjd/patent-search-skill
codex plugin add patent-search@djfksjd
```

### agy (Antigravity CLI)

```bash
agy plugin install djfksjd/patent-search-skill
agy plugin enable patent-search
```

### Gemini CLI

```bash
gemini extensions install https://github.com/djfksjd/patent-search-skill
```

### Cursor · Grok Build 등 파일 기반 호스트

```bash
git clone https://github.com/djfksjd/patent-search-skill.git ~/.agents/skills/patent-search
```

> 단일 스킬만 쓰고 싶다면 Claude Code 사용자 스킬 폴더에 직접 clone 해도 됩니다:
> `git clone https://github.com/djfksjd/patent-search-skill.git ~/.claude/skills/patent-search`

Claude Code에서 `/patent-search <아이템 설명>` 으로 호출하거나, "선행기술조사 해줘" 같은 요청 시 자동으로 발동됩니다.

## KIPRIS API 키 설정 (권장)

키가 없어도 Google Patents + KIPRIS 웹 폴백으로 동작하지만, 한국 문헌의 청구항 전문·서지 정확도를 위해 KIPRIS Plus 키를 권장합니다.

1. [plus.kipris.or.kr](https://plus.kipris.or.kr) 회원 가입
2. Open API 메뉴에서 상품 신청: **"특허·실용 공개·등록공보"** (+ 법적 상태 확인용 **"법적 상태 이력(특허·실용(ST.27), 상표, 디자인(ST.87))"** — `kipris_legal_status.py`가 사용. 미가입 상태로 실행하면 에러 31 → 종료 코드 4와 함께 가입 안내가 출력됩니다)
3. 마이페이지 → APIKEY관리에서 키 확인
4. 키 저장 — `.env`는 스킬 본체 폴더(`skills/patent-search/`) 안에 둡니다. 스크립트가 `scripts/`의 부모 폴더에서 `.env`를 읽기 때문입니다:
   ```bash
   cd <설치경로>/skills/patent-search   # 예: ~/.claude/skills/patent-search/skills/patent-search
   cp .env.example .env   # 파일을 열어 키 입력
   chmod 600 .env
   ```
   (환경변수 `KIPRIS_KEY`로 넘겨도 되며, 이 경우 `.env`보다 우선합니다.)

무료 쿼터는 **월 1,000회 호출**(매월 1일 초기화, 2026-07 기준)이며, 스킬은 쿼터를 아끼도록 설계되어 있습니다(검색은 검색식당 최대 `--max-pages`회 — 기본 3, 청구항 상세는 정독 대상만). 수집 스크립트들은 스킬 폴더 `kipris_quota.json`에 월별 호출 수를 누적 기록하고(이 머신 기준 하한 추정치), 800회 초과 시 경고를 출력합니다.

## 동봉 스크립트

Python 3 표준 라이브러리만 사용합니다(추가 설치 불필요).

| 스크립트 | 역할 | 사용 예 |
|---|---|---|
| `scripts/kipris_search.py` | 자유검색 일괄 실행(페이지네이션) → 중복 제거 TSV + 원본 XML + `search_manifest.json` | `python3 scripts/kipris_search.py "스포츠 영상 하이라이트 자동 생성" "선수 추적 개인별 영상" --rows 30 --max-pages 3 --out ./work` |
| `scripts/kipris_claims.py` | 출원번호별 서지+청구항 전문 → JSON + 원본 XML | `python3 scripts/kipris_claims.py 1020260075385 10-2024-0067638 --out ./work` |
| `scripts/kipris_legal_status.py` | 출원번호별 법적 상태 이력(WIPO ST.27 행정 이벤트) → `legal_status.json` + 원본 XML. 상품 미가입 시 종료 코드 4 | `python3 scripts/kipris_legal_status.py 1020260075385 --out ./work` |
| `scripts/kipris_expand.py` | 확장 축(로드맵 #8) — seed 서지상세 재사용으로 패밀리 + 후방 인용(priorArt) 1-hop 확장 후보 + 축별 status → `expansion.json`. 상한·쿼터 예약·동시 실행 lock. cited_by는 unsupported 고정, 예산 도달 시 종료 코드 3 | `python3 scripts/kipris_expand.py 1020260075385 10-2024-0067638 --out ./work` |
| `scripts/fto_gate.py` | FTO 게이트(오프라인) — 법적 상태 미확인 문헌을 "판정 불가"로 나열, 미확인이 있으면 종료 코드 2. `--expansion`으로 패밀리 커버리지 미확인 시 결론 보류 표기 | `python3 scripts/fto_gate.py --claims ./work/claims.json --legal ./work/legal_status.json --expansion ./work/expansion.json` |
| `scripts/claim_chart_validate.py` | claim chart JSON을 `references/claim_chart_schema.json`(v1)으로 검증 — E/I/P 근거 누락 등 위반 나열, 위반 시 종료 코드 1 | `python3 scripts/claim_chart_validate.py ./work/chart.json` |

동작 원칙:

- **HTTPS 고정** 호출이며, 키는 `KIPRIS_KEY` 환경변수 또는 스킬 폴더 `.env`에서 읽고 **어떤 출력·저장 파일에도 남기지 않습니다**(에러 메시지·원본 XML까지 마스킹). 키를 명령행 인자나 URL에 직접 넣지 마세요.
- **종료 코드**: 0 = 전건 성공, 1 = 하나 이상 실패, 4 = 법적 상태 이력 상품 미가입(`kipris_legal_status.py`), 2 = FTO 게이트 미통과(`fto_gate.py`), 3 = 예산/쿼터 도달 조기 종료(`kipris_expand.py` — 부분 결과 저장됨). 검색이 전체 건수를 다 못 담으면(에러로 중단된 경우 포함) 매니페스트에 `partial=true`로 표시됩니다 — 부분 수집을 완전 수집으로 오인하지 않도록 설계했습니다.
- **fail-closed**: 응답에 `resultCode`/`totalCount`가 없으면(API 스키마 변경 신호) 0건 성공이 아니라 해당 검색식 실패로 처리합니다. 429/5xx/타임아웃은 지수 백오프로 최대 2회 재시도합니다.
- **증거 보존**: `kipris_search.py`는 기존 산출물이 있는 `--out` 폴더에 쓰기를 거부하고(`--force`로만 강제), 원본 XML 파일명에 실행 시각을 포함합니다. `kipris_claims.py`는 기존 `claims.json`에 병합합니다.
- `claims.json`의 청구항은 **공보 서지 기준**이라 최신 보정·정정·심판 결과가 반영되지 않을 수 있습니다(`current_enforceable_claims: "unknown"`). FTO 용도로 쓰기 전 KIPRIS 웹에서 현재 청구항을 재확인하세요.
- `legal_status.json`은 **행정 이벤트 이력의 수집**이며 **현재 유효 청구항의 확정이 아닙니다** — 행정 이벤트만으로 최신 보정 청구항을 재구성하지 않고 `current_enforceable_claims: "unknown"`을 유지합니다. 리포트에 FTO 관찰 높음/낮음을 표기하려면 `fto_gate.py`가 종료 코드 0으로 통과해야 합니다(미통과 문헌은 "상태 미확인 — 판정 불가"로만 기재).

> 🔒 **보안 주의**: `raw_*.xml`, `bib_*.xml`, `legal_*.xml`, TSV/JSON 산출물과 검색어 목록에는 **무엇을 조사했는지(= 발명 방향)가 그대로 드러납니다**. 이 저장소의 `.gitignore`가 기본 차단하지만, 다른 곳에 복사·공유할 때도 비공개로 다루세요. KIPRIS 응답 데이터의 재배포·캐싱은 KIPRIS Plus 이용약관을 따릅니다.

## 산출물

작업 폴더에 `patent-search-<주제>-<YYYYMMDD-HHMM>.md` 리포트가 생성됩니다:

1. 요약 (특허성 축 / FTO 축 / 차별 후보 — 변리사 검토 필수 고지)
2. 조사 설계 (목적·기준일·출원국·공지예외 해당성)
3. 구성요소 분해표
4. 검색 기록 (검색식·소스·실행 시각·결과 수)
5. 문헌표 (법적 상태는 출처·확인일 병기)
6. Claim chart (셀마다 원문 인용 근거)
7. 차별화 가이드
8. 한계 고지 (18개월 미공개 출원, 비특허문헌 미조사 범위 등)

## 설계 원칙

- **정직성**: "유사 특허 없음"이라고 쓰지 않습니다 — "본 검색식·소스·기준일 범위에서 발견되지 않음"이라고 씁니다. 조사 불가능 영역(출원 후 ~18개월 미공개 출원)은 항상 한계로 고지합니다.
- **원문 근거 강제**: 스니펫만으로 판정하지 않고, 판정에 쓴 모든 셀에 문단/청구항 번호와 직접 인용을 남깁니다.
- **발명 정보 보호**: 외부 검색어는 일반화된 기술 키워드로만 구성하고, 첫 외부 검색 전에 검색어 목록을 사용자에게 확인받습니다. 발명 상세·수치·고객명은 검색창에 넣지 않습니다.
- **특허성 ≠ FTO**: 신규성 판단(기준일 이전 공개 문헌, 명세서 전체가 선행기술)과 침해 관찰(현재 유효한 청구항)은 다른 질문이므로 처음부터 분리합니다.

## 한계

- 출원 후 약 18개월간 미공개 상태인 출원은 어떤 도구로도 조사할 수 없습니다.
- Google Patents 접근은 비공식 경로라 차단·스키마 변경이 있을 수 있습니다 (스킬에 폴백 경로 내장).
- 기계번역 문헌은 원문과 다를 수 있어, 한국 문헌의 핵심 근거는 KIPRIS 원문으로 재확인합니다.
- 비특허문헌(논문·제품·영상)은 기본 조사 범위가 아니며 리포트에 그 사실을 고지합니다.

## 라이선스

MIT — [LICENSE](LICENSE) 참고. KIPRIS Plus API 이용 약관과 Google Patents 서비스 약관은 각 서비스의 정책을 따르세요.
