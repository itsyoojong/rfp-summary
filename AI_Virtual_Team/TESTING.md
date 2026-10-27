# 검증 결과

2026-10-26, Python 3.13.3 / macOS에서 실행했다. 프로젝트의 지원 기준은 Python 3.11 이상이며, Python 3.11과 Linux에서의 별도 실행은 아직 하지 않았다.

| 검사 | 실제 결과 |
| --- | --- |
| 전체 자동 테스트 | 183 passed, 1 skipped |
| Codex CLI 가짜 subprocess 및 승인 흐름 | 28 passed |
| LLM 및 명시적 회의 workflow | 21 passed |
| Discord adapter 및 실제 서비스 연결 | 30 passed |
| GitHub / Notion 모의 HTTP | 78 passed |
| 서비스·스케줄 실행 함수·DB | 15 passed, 1 skipped |
| 보고 기준 및 동기화 회귀 | 6 passed |
| 별도 프로세스 CLI | 5 passed |
| Ruff | 통과 |
| compileall | 이전 구현에서 통과; 이번 변경에서는 별도 재실행하지 않음 |
| 로컬 Mock 데모 | 생성 → 회의 → 승인 → 보고 → 재시작 확인 성공 |
| 실제 Codex 로컬 점검 | CLI 0.153.0, ChatGPT 로그인 확인, DB·추론 호출 없음 |
| 실제 Codex 추론 smoke test | 미실행 / 미검증 |

이번 변경의 최종 실행 명령은 다음과 같다. 현재 가상환경에는 프로젝트가 editable install되어 있지 않아 `PYTHONPATH`를 지정했다. 최초 지정 없이 실행한 신규 테스트는 import 단계에서 실패했으며, 아래 방식으로 수정 후 전체 검증했다.

```bash
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q -ra
.venv/bin/python -m ruff check .
PYTHONPATH="$PWD/src" .venv/bin/python -m ai_virtual_team demo --db /tmp/ai-team-demo.sqlite3
PYTHONPATH="$PWD/src" .venv/bin/python -m ai_virtual_team codex-check
```

환경의 네트워크 제약으로 APScheduler를 설치하지 못했다. 기존 시스템 패키지 및 로컬 캐시를 사용해 테스트 환경을 구성했으며 `.venv`는 이 환경에서만 시스템 패키지를 참조한다. 저장소의 설치 안내는 일반적인 독립 가상환경 설치 방식이다.

건너뛴 테스트는 `test_real_apscheduler_job_registration_and_execution` 한 개다. 스케줄러 실행 함수 자체의 opt-in 검사, 서울 날짜 처리, 보고 중복 방지, 재시작 후 전송 중복 방지와 불확실한 전송 보류는 통과했다. 실제 APScheduler event loop에서 정해진 시각에 job이 실행되는 것은 아직 검증하지 못했다. 의존성 설치 후 전체 테스트를 다시 실행하면 이 테스트도 실행된다.

실제 Discord 봇 로그인, Notion 읽기·쓰기, Groq 추론은 인증 정보를 사용하지 않았으므로 미검증이다. GitHub 공개 저장소 GET 연결은 시도했지만 이 환경에서 실패했다. 이 결과를 저장소 존재 여부나 저장소 권한 문제로 단정하지 않는다. 모든 HTTP 성공·실패 시나리오 테스트는 모의 응답을 사용한다.

Codex 연결 테스트는 실제 로컬 자식 프로세스로 실행되는 가짜 CLI를 사용한다. 실제 Codex 추론, 외부 서비스 쓰기 또는 외부 저장소의 테스트 실행을 하지 않는다. ChatGPT 로그인과 API 키 로그인 구별, 버전 제한, 비밀 환경변수 제외, 비신뢰 입력의 stdin 분리, 읽기 전용 및 실행 기능 차단 인자, JSON Schema, 출력 크기 제한, 잘못된 JSON과 도구 이벤트 거절, timeout·취소 시 프로세스 회수, timeout 재시도의 분당 예산 사용을 확인했다. 역할 다섯 단계와 승인 전 Proposed·관리자 제한·보류·승인·재시작 보존 및 회의 중 실패 시 앞선 발언 보존도 검증했다.

`LLM_MODE=codex`이며 `CODEX_BINARY`가 존재하지 않는 실행 파일을 가리켜도 `demo`가 두 번 모두 성공하는 별도 프로세스 테스트를 추가했다. 이는 데모가 실제 Codex를 실행하지 않는다는 검증이다. 요구된 `/tmp/ai-team-demo.sqlite3` 실행은 기존 데모 DB를 재사용했고 발언 5개, 승인 작업 3개, 외부 호출 0회, 보고 중복 방지와 재시작 보존을 확인했다. 새 DB에서의 전체 흐름은 자동 테스트가 별도로 실행한다.

실제 CLI의 `codex-check` 결과는 `auth: ChatGPT`, `version: codex-cli 0.153.0`, `inference_verified: false`였다. 실제 실행 명령에 잘못된 임시 JSON Schema를 주는 로컬 파싱 점검은 스키마 오류로 종료했다. 이 결과는 모델 연결 성공이나 실제 모델의 도구 제한을 검증한 것으로 해석하지 않는다. 실제 추론 smoke test는 별도 작업이며 이번 요청에서는 수행하지 않았다.

데모의 Mock 결과는 구현 및 테스트 완료의 근거가 아니다. 데모는 회의와 승인 시스템의 동작을 검증하며, RFP Insight 저장소를 수정하거나 그 저장소의 테스트를 실행하지 않는다. 데모의 `actor_id=1`은 격리된 로컬 시뮬레이션 값이며 실제 Discord 사용자 ID나 인증 정보가 아니다.

향후 실서비스 확인은 실제 `.env` 설정 후 `notion-check`, `github-check`, `bot` 명령으로 진행한다. Notion에 쓰기 전에 schema 검사 결과를 확인하고, Discord에서 프로젝트 생성 → 회의 → 보류/승인 → 상태 조회 → 보고의 전체 흐름을 실행해야 한다. 실제 보고 시각은 사용자가 지정한 후에만 설정한다.

## 후속 실제 추론 점검 (2026-10-27)

가상 안건의 실제 PM 추론을 시도했으나 Codex 내부 app-server 초기화에서 `Operation not permitted`로 종료됐다. 진단을 위한 재실행도 같은 오류로 종료됐다. 모델 응답은 검증하지 못했으며 운영 DB·외부 저장소는 사용하지 않았다. 사용자 터미널에서 동일한 제한으로 한 번만 검사할 수 있는 `codex-smoke` 명령을 추가했다. 이 명령은 PM 의제 JSON과 단계 규칙을 확인하며 DB 생성·승인·외부 서비스 쓰기를 수행하지 않는다. 가짜 CLI 기반 성공 및 잘못된 검토 참조 거절 테스트를 추가했다.

추가 변경 후 전체 검사: 185 passed, 1 skipped(APScheduler 미설치), Ruff 통과, Mock 데모 성공(외부 호출 0회). 실제 PM 추론은 위 초기화 오류로 여전히 미검증이다.
