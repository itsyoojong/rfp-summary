# AI Virtual Team 구현 계획

기준일: 2026-10-26. 작업 경계: 현재 `AI Virtual Team/` 폴더만 변경한다. 상위 RFP Insight 소스, 기존 브랜치, 원격 저장소는 변경하지 않는다.

## 구조와 설계 원칙

`src/ai_virtual_team/`에 설정, Pydantic 모델, SQLite 저장소, 역할 프롬프트, LLM provider, 명시적 회의 workflow, application service, Discord adapter, 읽기 전용 GitHub adapter, Notion adapter, scheduler, CLI를 분리한다. SQLite 트랜잭션으로 승인과 작업 확정을 묶고, 동기화 outbox를 같은 트랜잭션에 저장한다. 외부 API 실패는 내부 상태를 롤백시키지 않는다. LLM 출력은 제안이며 직접 DB 변경이나 도구 실행 권한을 갖지 않는다.

회의는 PM 의제 → Developer 검토 → QA 반론 → Developer 응답 → PM 종합의 유한 단계로 진행한다. 모든 단계의 입력과 출력, 역할별 문맥을 검증하고 저장한다. 인간 승인 전에는 Proposed 상태이며, 승인 시 기준 계획 버전을 확인한다. 목표 변경은 MVP에서 지원하지 않는다.

## 단계별 구현 및 확인

1. Phase 1: 패키지, 설정, DB, 모델, Mock LLM과 영속성/권한/승인 테스트. API 키 없이 실행한다.
2. Phase 2: Groq provider와 회의 오케스트레이션. timeout, 제한된 retry, 호출량 제한, 실패 시 발언 보존을 검증한다.
3. Phase 3: discord.py 슬래시 명령, 관리자 및 채널 경계, 스레드와 fallback, 안전한 출력. 인증 없이 명령 등록을 검증한다.
4. Phase 4: GitHub GET 전용 조회 및 Notion 2026-03-11 data source 동기화. 중복 방지, 충돌 탐지, 외부 실패 재시도를 모의 HTTP로 검증한다. 공개 RFP 저장소 조회는 가능한 경우 실제 GET으로 확인한다.
5. Phase 5: Asia/Seoul 일일 보고, 명시적으로 설정했을 때만 APScheduler 활성화, 날짜별 중복 방지와 재시작 복구. 통합 테스트와 로컬 데모를 실행한다.

독립된 adapter 구현과 검증은 병렬 진행하되, phase 완료 기록은 순서대로 남긴다. 실서비스 인증이 없으면 Discord/Notion/Groq 연동은 미검증으로 보고한다. 자동 회의 규칙, 저장소 코드 수정/테스트 실행/PR 생성은 이번 범위에 넣지 않는다.

## 검증 기록

검증일: 2026-10-26 (Asia/Seoul). 구현 중 실행 환경 연결이 끊겼으며, 재연결 후 파일을 재확인하고 미저장 파일을 작성했다. 기존 RFP Insight의 추적 파일과 브랜치는 변경하지 않았다.

### Phase 1 — 완료

패키지 설정, Pydantic 모델, SQLite 저장소, Mock LLM을 구현했다. DB 재열기 영속성과 관리자 검사, 트랜잭션 실패 시 outbox를 포함한 롤백을 테스트했다. `tests/test_service.py`의 Phase 1 테스트 2개가 통과했다. 저장소의 `list()` 메서드가 타입 annotation의 `list`를 가리는 초기 import 오류는 지연 annotation으로 수정하고 재검증했다.

### Phase 2 — 완료 / 실제 Groq 미검증

역할별 프롬프트, 5단계 상호 검토, 승인된 공통 상태 전달, 단계별 Pydantic 검증, 즉시 발언 저장을 구현했다. Groq timeout, retry, 호출량 제한과 실패 출력 보안은 MockTransport로 검증했다. `tests/test_llm_workflow.py` 21개 통과. API 키를 사용한 실서비스 호출은 하지 않았다.

### Phase 3 — 완료 / 실제 Discord 미검증

필수 슬래시 명령 8개와 `report_schedule`, `task_update`를 구현했다. 서버/상위 채널 격리, 관리자 제한, 스레드 fallback, 안전한 출력 분할, 실제 TeamService를 통한 명령 흐름을 테스트했다. `tests/test_discord.py` 30개 통과. 봇 로그인과 실제 서버 메시지 전송은 미검증이다.

### Phase 4 — 완료 / 실제 외부 연결 미검증

GitHub GET 전용 조회, 선택적 파일 본문/커밋 patch 조회, 완료 근거 검증을 구현했다. Notion 2026-03-11 data source API, 결정적 External ID, 충돌 탐지, 생성 결과 불확실성 보존, 외부 쓰기 전 실패 구분을 구현했다. `tests/test_integrations.py` 78개 통과. 공개 RFP 저장소의 실제 GET은 제한된 네트워크 환경에서 GitHubError로 실패했으므로 연결 성공으로 간주하지 않는다. Notion 인증 연결은 미검증이다.

### Phase 5 — 구현 완료 / APScheduler 실제 실행 검증 보류

일일 보고 생성, DB 기반 사실/추정 구분, 날짜별 중복 방지, 실패 구간의 GitHub 조회 기준 보존, 전송 결과 불확실성 처리, 재시작 복구, 사용자 지정 시각에만 활성화되는 APScheduler adapter를 구현했다. 보고 생성과 delivery 로직은 직접 실행하여 검증했다.

현재 환경의 네트워크 제약 때문에 APScheduler 설치가 되지 않았다. 다른 의존성은 기존 시스템 패키지 및 오프라인 캐시로 준비했다. 실제 APScheduler job 등록/실행 테스트 1개는 명시적으로 skip했다. 일반 네트워크 환경에서 `python -m pip install -e '.[dev]'` 후 이 테스트를 포함한 전체 검증이 필요하다.

### 최종 실행 결과

- `.venv/bin/python -m pytest -q -ra`: **154 passed, 1 skipped**.
- `.venv/bin/python -m ruff check .`: **All checks passed**.
- `.venv/bin/python -m compileall -q src tests`: 성공.
- `.venv/bin/python -m ai_virtual_team demo --db /tmp/ai-team-demo.sqlite3`: 성공. 역할별 발언 5개, 승인 작업 3개, 외부 API 호출 0회.
- 동일 데모를 별도 프로세스로 다시 실행: 프로젝트·회의·작업 보존, 같은 날짜의 report ID 유지 확인.
- `notion-schema`: 네 종류의 data source 속성 출력 확인.
- `pip check`: APScheduler 미설치 1건 확인. 이를 숨기거나 설치 완료로 기록하지 않는다.

최종 회귀 검증에서 승인 중간 실패의 완전 롤백, 중복 회의 차단, 오래된 제안 승인 차단, GitHub 조회 중 완료 작업의 중복 보고 방지, 실패한 GitHub 조회 구간 보존, Notion의 모호한 생성 결과와 확인된 미생성 실패 구분, 실제 프로젝트 DB에 대한 데모 실행 거부를 확인했다.

실서비스 인증 정보, 자동 보고 시각, 실제 관리자 ID는 임의로 설정하지 않았다. 자동 회의 조건과 외부 저장소 변경 기능은 구현하지 않았다.

## 후속 변경 — ChatGPT 로그인 Codex CLI 연결 (2026-10-26)

사용자 요청에 따라 실제 회의 provider를 Codex CLI로 선택할 수 있도록 `CodexLLM`을 추가했다. `LLM_MODE=codex`를 설정 예제와 로컬 `.env`에 적용했으며, 기존 `.env`가 없어 로컬에는 provider 선택 한 줄만 생성했다. API 키·관리자 ID·외부 연동 설정을 생성하거나 추정하지 않았다. `.env`는 Git ignore 대상임을 확인했다. 명시적으로 선택하는 기존 Groq 모드는 호환성 용도로 유지했고, 기본 설정이 없는 경우와 `demo`는 계속 Mock이다.

Codex CLI 0.153.0의 로컬 help·feature 목록을 확인한 뒤 버전을 고정했다. ChatGPT 로그인만 허용하고, 역할별 지침과 기존 `AgentInput`을 일회성 프로세스에 전달한다. 사용자 config 및 실행 도구 기능을 차단하며 읽기 전용 sandbox를 사용한다. 호출 폴더·스키마·로그·상태는 임시 디렉터리에 둔다. 프로세스 실행에 shell을 사용하지 않으며 API 키·봇 토큰 등 비밀 환경변수를 상속하지 않는다. timeout·취소 시 프로세스 그룹을 회수하고, 스트림 크기·입력·JSON 크기를 제한한다. 출력은 JSONL 완료 여부, 도구 이벤트 유무, Pydantic, 기존 회의 단계 규칙을 통과해야 저장된다.

`codex-check`는 DB를 만들지 않는 버전·로그인 점검 명령이다. 로컬 CLI 0.153.0 및 ChatGPT 로그인은 실제로 확인했다. 모델 추론은 실행하지 않았고 `inference_verified=false`로 출력한다. 안전 옵션은 로컬 CLI 계약과 공식 설정 스키마를 참고했지만 실제 모델을 이용한 도구 차단 smoke test까지 완료한 것으로 보고하지 않는다.

기존 관리자/서버/채널 경계, 승인과 outbox의 단일 트랜잭션, SQLite 상태 기준, GitHub GET 제한은 수정하지 않았다. Codex가 오류를 반환해도 Mock으로 대체하거나 자동 승인하지 않는다. 외부 저장소 코드 수정·push·PR 생성·테스트 실행 기능은 추가하지 않았다. 상위 소스나 Git 브랜치를 변경하지 않았다.

최종 검증은 `PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q -ra`로 **183 passed, 1 skipped**, `.venv/bin/python -m ruff check .`로 **All checks passed**였다. 신규 Codex 테스트 28개와 Codex 설정에서도 Mock 데모를 강제하는 CLI 회귀 사례가 포함된다. skip은 기존 APScheduler 미설치 건이다. 최초 신규 테스트는 editable install이 없는 환경에서 import 오류가 났으며, 소스 경로를 지정해 해결했다.

`PYTHONPATH="$PWD/src" .venv/bin/python -m ai_virtual_team demo --db /tmp/ai-team-demo.sqlite3`도 성공했다. 기존 데모 DB에서 발언 5개·승인 작업 3개·외부 호출 0회·같은 날짜 보고 중복 방지·재시작 보존을 확인했다. 새 DB 생성부터 승인까지의 흐름은 가짜 Codex와 Mock의 자동 테스트에서 검증했다. 실제 Codex 추론, Discord·Notion 연동 및 GitHub live 연결은 이번 변경의 완료 근거에 포함하지 않는다.

## 실행 환경 경로 복구 (2026-10-27)

폴더명 변경 전 `AI Virtual Team/.venv` 경로가 activation 스크립트와 CLI shebang에 남아, 활성화 후 `python`을 찾지 못하는 문제를 확인했다. 현재 `AI_Virtual_Team/.venv` 경로로 해당 로컬 스크립트와 pyvenv.cfg를 수정했다. 새 zsh에서 activate 후 프로젝트 Python 3.13.3 및 pip 경로를 확인했고, PYTHONPATH를 지정한 codex-check에서 CLI 0.153.0과 ChatGPT 로그인을 확인했다. 애플리케이션 코드는 변경하지 않았으며 전체 테스트와 실제 추론은 이번 경로 복구에서 재실행하지 않았다.

## 후속 실제 추론 점검 (2026-10-27)

가상 안건의 실제 PM 추론을 시도했으나 Codex 내부 app-server 초기화에서 `Operation not permitted`로 종료됐다. 진단을 위한 재실행도 같은 오류로 종료됐다. 모델 응답은 검증하지 못했으며 운영 DB·외부 저장소는 사용하지 않았다. 사용자 터미널에서 동일한 제한으로 한 번만 검사할 수 있는 `codex-smoke` 명령을 추가했다. 이 명령은 PM 의제 JSON과 단계 규칙을 확인하며 DB 생성·승인·외부 서비스 쓰기를 수행하지 않는다. 가짜 CLI 기반 성공 및 잘못된 검토 참조 거절 테스트를 추가했다.

추가 변경 후 전체 검사: 185 passed, 1 skipped(APScheduler 미설치), Ruff 통과, Mock 데모 성공(외부 호출 0회). 실제 PM 추론은 위 초기화 오류로 여전히 미검증이다.

## 사용자 smoke 실패 진단 보완 (2026-10-27)

사용자 터미널에서 CLI 종료 이후 출력 검증이 실패했다는 보고를 받았다. 기존 오류 문구만으로는 실패 원인을 특정할 수 없으며, 실제 출력 원문은 확보하지 않았다. 파서의 실패 지점별 고정 진단 코드와 행 번호를 추가했다. Pydantic 오류는 허용된 오류 종류만 출력하고 원문·입력값·알 수 없는 필드명 및 이벤트명은 노출하지 않는다. 기존 도구 이벤트 거절 및 스키마 검증은 완화하지 않았다. 실제 추론 성공 여부는 사용자 재실행 결과 확인 전까지 미검증이다.

진단 보완 후 검사: 193 passed, 1 skipped(APScheduler 미설치). Ruff 및 Mock 데모 통과, 외부 호출 0회. 실제 smoke 실패 원인은 아직 특정하지 못했다.
