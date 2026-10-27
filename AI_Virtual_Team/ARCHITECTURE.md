# AI Virtual Team 아키텍처

이 시스템의 상태 변경 주체는 `TeamService`와 SQLite입니다. 에이전트는 검증 가능한 형식의 의견과 계획을 제안하고, 인간 관리자가 승인합니다. Discord나 Notion에 표시된 문장 자체가 승인 또는 작업 완료를 발생시키지는 않습니다.

## 모듈별 책임

| 경로 | 책임과 경계 |
| --- | --- |
| `config.py` | `.env` 로딩, 비밀 값, 호출 한도, 관리자 ID, 연결 설정 검증 |
| `models.py` | 프로젝트·작업·회의·발언·보고 및 LLM 입출력의 Pydantic 계약 |
| `database.py` | SQLite 저장, 트랜잭션, 승인·활동 기록, outbox, 전송 기록 |
| `service.py` | 프로젝트 생성, 회의 저장, 승인, 상태 전환, 사실 기반 보고, 동기화 실행 |
| `orchestrator.py` | 역할 순서, 발언 수, 상호 검토 참조와 최종 출력 검증 |
| `llm.py` | Mock/Groq 공통 provider 계약, 프롬프트 조립, 응답 검증, timeout·재시도·호출 제한 |
| `codex_llm.py` | ChatGPT 인증 Codex CLI, 제한된 자식 프로세스 실행, 환경변수 차단, JSONL·Pydantic 검증 |
| `prompts/` | 공통 경계와 PM·Developer·QA별 역할 지침 |
| `discord_bot.py` | 슬래시 명령, Discord 권한·채널 경계, 스레드, 안전한 출력 |
| `integrations/github.py` | 고정 GitHub API 호스트의 읽기 전용 저장소 조회와 완료 근거 확인 |
| `integrations/notion.py` | 현재 data source API를 이용한 단방향 동기화와 충돌 검사 |
| `scheduler.py` | 서울 시간의 선택적 일일 보고, 오늘 보충 실행, Notion 재시도 |
| `runtime.py` | 서비스 조립, 단일 프로세스 파일 잠금 |
| `cli.py`, `__main__.py` | Discord와 동일한 서비스 로직을 쓰는 로컬 명령 및 Mock 데모 |
| `ports.py` | 향후 개발 도구의 Protocol과 현재 실행을 거절하는 구현 |

이 정도 규모에서는 ORM, 작업 큐 서버, 마이크로서비스를 도입하지 않았습니다. 저장소와 서비스를 작게 나누면 Python의 함수 호출과 트랜잭션을 따라가며 동작을 이해할 수 있고, 외부 API를 모의 객체로 바꿔 테스트하기도 쉽습니다. 나중에 오케스트레이션만 LangGraph로 교체하더라도 Discord 명령과 승인 규칙을 함께 재작성하지 않도록 경계를 유지했습니다.

## 회의와 역할별 문맥

회의는 `Running`으로 저장한 다음 순서가 정해진 다섯 발언을 실행합니다.

| 순서 | 역할·단계 | 출력과 다음 단계의 관계 |
| --- | --- | --- |
| 1 | PM · `agenda` | 주제와 결정할 항목 정의 |
| 2 | Developer · `technical` | PM의 1번 발언을 참조해 구현 방향과 위험 검토 |
| 3 | QA · `review` | Developer의 2번 발언을 참조해 반론·검증 시나리오 제시 |
| 4 | Developer · `response` | QA의 3번 발언에 응답하고 계획 보완 |
| 5 | PM · `synthesis` | QA·Developer 응답을 참조해 회의록과 작업 초안 종합 |

`AgentInput`에는 프로젝트 스냅샷, 주제, 현재 단계, GitHub 근거, 공유 발언, 해당 역할의 이전 발언이 들어갑니다. 역할별 프롬프트와 `private_context`는 분리되며 `shared_messages`로 서로의 의견을 검토합니다. 현재 private context는 해당 회의 안에서의 역할별 발언 기록입니다. Codex 모드는 발언마다 독립된 일회성 CLI 프로세스를 실행하며, Codex 세션을 resume하지 않습니다. 영속 상태는 SQLite에서 공급하고, 회의를 넘어서는 개인별 장기 메모리는 구현하지 않습니다.

`AgentOutput`의 허용되지 않은 필드는 Pydantic이 거절합니다. `review_of`에는 앞선 발언 번호만 넣을 수 있고, 단계별로 반드시 검토할 발언 번호를 검사합니다. 중간 단계는 작업 목록이나 최종 회의록을 확정할 수 없습니다. 최종 PM 단계만 `TaskProposal`과 `MeetingSummary`를 제안하며, 작업이 있으면 승인 필요 항목도 있어야 합니다.

구조화된 `test_execution`은 `not_run`만 허용합니다. Discord 발언에도 모델의 검토 의견이며 실제 테스트 실행은 없었다고 표시합니다. 자유 서술의 의미를 완벽히 검증하는 것은 아니므로, 문장 속 주장을 실행 증거나 완료 상태로 승격시키지 않습니다.

검증된 발언은 다음 LLM 호출 전에 DB에 저장합니다. Discord 전송은 저장 다음에 실행하므로 전송 오류가 앞선 발언을 없애지 않습니다. 이후 LLM 호출이 실패하면 회의를 `Failed`로 남기고, 취소되거나 프로세스가 종료되면 `Interrupted`로 처리합니다. 정상 완료는 `Pending Approval`이며, 보류는 `Held`, 승인 후에는 `Approved`가 됩니다. 자동 재회의나 이벤트 기반 회의 조건은 없습니다.

이 흐름에서 최대 발언 수는 방어적인 실행 상한입니다. `MEETING_MAX_TURNS`를 5보다 크게 바꿔도 토론 횟수가 임의로 늘어나지는 않습니다. 역할 순서를 늘리려면 명시적으로 워크플로와 테스트를 변경해야 합니다.

## 제안과 승인 사이의 경계

회의마다 시작 시점의 `base_plan_version`을 저장합니다. 승인을 요청하면 현재 프로젝트의 `plan_version`과 비교합니다. 그 사이 다른 계획이 승인됐다면 오래된 제안을 거절하고 최신 상태로 새 회의를 요청하도록 합니다. 이미 승인된 회의의 같은 승인 요청은 작업을 중복 생성하지 않습니다.

승인 트랜잭션은 회의 상태, 해당 작업의 `Proposed → Approved`, 프로젝트의 승인 버전, 승인 주체와 시각, 활동 기록, Notion outbox를 한 번에 저장합니다. 중간 실패로 작업만 승인되거나 승인 기록만 남는 상태를 줄이기 위한 설계입니다. 보류한 작업은 `Proposed`로 남아 실행 대상에 포함되지 않습니다.

현재 목표 변경 API는 없습니다. 승인되는 계획은 새 작업을 추가하며 기존 승인 작업을 수정하거나 삭제하지 않습니다. 작업 상태 갱신도 관리자가 수행합니다. `Done`은 저장소 근거를 조회한 후 작업 revision을 다시 비교하여, 조회 중 다른 변경이 있었다면 재검토를 요구합니다. 병합 PR·닫힌 Issue와 인간의 검토 기록을 보존하지만, 코드 품질이나 실제 테스트 성공을 자동으로 증명하는 검증기는 아닙니다.

## SQLite를 상태의 기준으로 두는 이유

SQLite의 `entities` 테이블은 프로젝트·작업·회의·보고의 검증된 JSON 스냅샷을 저장합니다. 자주 검사하는 상태·프로젝트 ID·날짜·revision은 별도 열에 둡니다. JSON은 현재 모델과의 대응을 읽기 쉽게 하고, 인덱스는 중요한 중복 조건을 DB 자체에서 막습니다.

| 저장 영역 | 내용·제약 |
| --- | --- |
| `entities` | 종류와 ID가 기본키; 프로젝트별 하루 보고 하나; 동시에 Running 회의 하나 |
| `messages` | 회의별 발언 순서가 유일하며 역할과 구조화 출력 보존 |
| `approvals` | 승인자, 회의, 결정, 시각; 회의별 실제 승인 기록 중복 방지 |
| `activity` | 생성·승인·상태 변경·오류 등 운영 이력 |
| `outbox` | 동기화할 최신 revision, 재시도 상태, 시도 표시, 소유자와 임대 만료 시각 |
| `sync_state` | 원격 page ID, 마지막 비교 기준, hash, revision, 수정 시각 |
| `deliveries` | 보고서별 Discord 전송 상태 `sending`, `sent`, `uncertain` |
| `schema_version` | 현재 스키마 버전 기록; 범용 마이그레이션 도구는 아직 없음 |

트랜잭션은 `BEGIN IMMEDIATE`로 시작하고 오류가 나면 롤백합니다. WAL과 busy timeout을 설정하지만, 여러 운영 프로세스를 지원하는 구조라고 주장하지는 않습니다. 실제 CLI·봇 실행 진입점에서 `fcntl.flock`으로 같은 DB 경로의 두 번째 프로세스를 거절합니다. Mac/Linux 단일 프로세스 운영이 지원 범위이며, 경로 별칭을 사용하거나 서비스 객체를 직접 여러 개 실행해 이 경계를 우회하지 않아야 합니다.

트랜잭션 안에서는 외부 HTTP나 LLM 호출을 `await`하지 않습니다. 외부 응답이 늦는 동안 DB 쓰기 잠금을 계속 잡고 있으면 승인이나 다른 프로젝트 작업도 멈출 수 있기 때문입니다. 먼저 필요한 상태를 읽고 외부 요청을 수행한 뒤, 짧은 트랜잭션에서 현재 버전과 제약을 다시 확인하고 결과를 반영합니다.

## Notion 동기화의 실패 모델

Notion은 내부 DB의 표시용 복제본입니다. 프로그램은 기존 네 data source의 행을 동기화하며, Notion에서 바꾼 값을 DB로 가져오지 않습니다. `External ID=종류:내부ID`로 기존 행을 찾고 `Revision`, `Payload Hash`, 관리 속성 fingerprint, `last_edited_time`을 비교합니다. 스키마의 원본은 `integrations/notion.py`의 `SCHEMAS`이며 설치 절차와 전체 속성표는 README에 있습니다.

DB의 상태 변경과 outbox 기록은 같은 트랜잭션에 저장합니다. 동기화 직전에 outbox 항목을 점유하고 시도 시작 표시를 영구 기록한 뒤 HTTP 요청을 수행합니다. 이 순서가 필요한 이유는 원격 생성이 성공한 직후 로컬 프로세스가 종료될 수 있기 때문입니다. 로컬에 page ID가 없다고 해서 원격 생성도 실패했다고 판단하면 같은 행을 두 번 만들 수 있습니다.

| 상황 | 처리 |
| --- | --- |
| 기존 원격 행이 기대하는 현재 속성과 일치 | 성공한 이전 요청으로 재조정하고 중복 생성하지 않음 |
| 같은 External ID가 두 개 이상 | `conflict`로 보류 |
| 원격 관리 속성, 위치, 수정 시각 또는 revision이 예상과 다름 | DB를 유지하고 `conflict`로 보류 |
| 생성 요청 후 timeout·5xx·성공 응답 파싱 실패 | 결과 불확실 표시를 유지하고 조회로 재조정; 행이 안 보인다고 재생성하지 않음 |
| 쓰기 전 검증 실패 또는 명확한 생성 거절 | 이전 불확실 시도와 원격 매핑이 없을 때만 시도 표시를 해제하고 재시도 가능 |
| 전송 중 로컬 변경으로 더 최신 revision이 생김 | 오래된 응답으로 새 outbox 항목을 지우지 않도록 revision·소유자 검사 |
| 취소 또는 재시작 | 저장된 시도 표시를 유지하여 다음 실행에서 조정 |

동기화 한 번의 작업량은 최대 8개, 시간 예산은 12초입니다. 봇이 실행 중이고 Notion이 설정되어 있으면 60초 간격으로 실패한 항목을 다시 대기열에 넣습니다. 충돌은 이 자동 재시도 대상에서 제외하며, 인간이 확인한 후 `sync --retry-conflicts`로 다시 검사합니다. 이 명령은 불확실 생성 표시를 지우거나 강제로 덮어쓰지 않습니다.

Notion은 이 방식에서 유일성 제약과 원자적인 비교 후 수정 기능을 제공하지 않습니다. 읽기와 PATCH 사이에 다른 사용자가 수정하는 경쟁 상황까지 완전히 막지는 못합니다. `External ID`와 비교 기준은 중복·충돌을 줄이는 방어이며 분산 환경의 정확히 한 번 실행 보장은 아닙니다. 원격 변경을 과거 값으로 복구해도 수정 시각이 달라질 수 있어 자동으로 해결되지 않는 충돌이 있습니다. 현재 DB의 기대 속성과 정확히 일치하는 경우에는 재조정할 수 있지만, 범용 강제 해결 기능은 아직 제공하지 않습니다.

## 일일 보고와 스케줄

PM의 일일 보고는 검증 가능한 사실을 정해진 템플릿으로 조합합니다. 완료 작업은 DB의 `Done`과 완료 시각을 기준으로 찾고 근거를 함께 보존합니다. 진행 상태, 장애 요인, 승인 대기 회의, 다음 승인 작업을 DB에서 읽습니다. 에이전트의 미해결 의견은 별도의 추정·제안 필드에 넣습니다. 이렇게 하면 보고서를 자연스럽게 만들려는 LLM이 실제로 없던 완료나 테스트 결과를 추가하는 경로를 줄일 수 있습니다.

GitHub 조회는 GET 전용이며 호스트와 저장소 형식을 제한합니다. 커밋·Issue는 API의 시간 필터, PR은 갱신 시각 필터를 사용합니다. API별 조회 상한과 부분 실패를 그대로 남기며, 실패한 구간을 변경 없음으로 보고하지 않습니다. 다음 조회의 기준은 마지막으로 성공한 GitHub 스냅샷이므로 이전 보고의 조회 실패 이후 기록을 다시 확인할 수 있습니다. 파일 트리는 현재 기본 브랜치의 구조이지 기간 내 변경 파일 집합이 아닙니다.

기본 workflow가 에이전트에 전달하는 것은 이 메타데이터 스냅샷입니다. 상세 검토를 위한 `read_file`과 `commit_details`는 별도의 Python API로 분리되어 있으며 LLM 자동 도구 호출에 연결하지 않았습니다. 파일 읽기는 커밋을 고정하고 일반 blob의 크기·UTF-8 여부를 검사합니다. 변경 상세는 최대 50개 파일, patch 파일당 8 KiB·전체 64 KiB로 제한하고 잘린 사실을 표시합니다. 이를 통해 나중에 소스 검토 기능을 추가할 때에도 임의 URL이나 코드 실행을 제공하지 않고 필요한 읽기 범위부터 연결할 수 있습니다.

보고 생성의 유일성은 메모리의 스케줄러가 아니라 `(project_id, 서울 날짜)` DB 제약이 책임집니다. 같은 날짜의 요청은 기존 보고서를 반환합니다. 최초 작성 후의 변경은 보고서를 덮어쓰지 않고 다음 보고에 반영하며, 현재 상태는 별도 조회합니다. 외부 조회가 자정을 넘기면 최종 생성 시점의 서울 날짜와 기준 시각을 다시 계산합니다.

APScheduler 3.x의 `AsyncIOScheduler`를 봇과 같은 이벤트 루프에서 사용합니다. 일정 자체는 프로젝트의 `report_time`에 저장하고 시작 시 cron job을 다시 만듭니다. 중복 방지의 기준이 DB이므로 별도 scheduler job store 없이 복구할 수 있습니다. 시각이 없으면 보고 job을 만들지 않습니다. 재시작 시 오늘 이미 지난 시각만 보충하며 이전 날짜까지 소급하지 않습니다.

보고서 저장과 Discord 전송은 서로 다른 성공 조건입니다. 전송 전에 `sending`을 기록하고 끝나면 `sent`로 바꿉니다. 일부 메시지만 전송됐거나 응답을 잃은 경우에는 `uncertain`으로 남겨 자동 재전송을 막습니다. 보고서가 이미 존재한다는 이유로 전송도 성공했다고 판단하지 않습니다. 봇이 연결 준비를 마친 뒤 전송하며, 운영자가 실제 채널을 확인하고 `/daily_report`를 명시적으로 다시 호출할 수 있습니다.

## 외부 입력과 LLM 경계

Discord의 주제, 프로젝트 설명, GitHub 문서·메시지는 신뢰할 수 없는 자료로 프롬프트에 표시합니다. 역할 지침과 출력 계약을 별도 system 메시지에 두고, 자료 안의 지시를 실행 권한으로 취급하지 않습니다. 이 프롬프트만으로 모든 주입 공격을 방지한다고 주장하지 않습니다. 더 중요한 제한은 LLM에 shell, DB, HTTP 임의 요청, Git 쓰기 도구를 전달하지 않는 구조입니다.

Groq provider는 호출 전체 timeout과 HTTP timeout을 함께 적용하며, 유한한 재시도와 프로세스 전체 분당 호출 제한을 둡니다. 재시도도 호출 예산을 사용합니다. 기본 모델에는 JSON Schema를 요청하고, 어떤 응답이든 로컬 `AgentOutput`으로 다시 검사합니다. 출력 필드 검증은 의미의 진실성을 증명하지 않으므로 상태 변경은 별도의 서비스 규칙과 인간 승인으로 처리합니다.

Discord는 관리자 사용자 ID를 검사하고 모든 프로젝트 접근에 서버·부모 채널 범위를 확인합니다. 사용자 발언과 출력은 멘션을 이스케이프하고 알림을 차단합니다. 사용자에게 반환하는 오류에는 원격 응답 본문이나 비밀 값을 포함하지 않습니다. API 키는 `SecretStr` 설정으로 다루지만, 운영체제 접근 통제·암호화·보안 감사가 모두 구현된 제품이라는 의미는 아닙니다.

## 확장 지점과 미구현 범위

### Codex CLI 제안 경계

`LLM_MODE=codex`는 기존 `LLMProvider` 계약만 구현합니다. `TeamService`, 승인 트랜잭션, outbox, Discord 관리자·서버·채널 검사, GitHub GET adapter는 변경하지 않습니다. PM·Developer·QA는 같은 ChatGPT 로그인과 선택 모델을 사용하지만 지침과 역할별 문맥이 다릅니다. 프로그램이 시작하는 고정된 CLI 프로세스는 모델에게 제공하는 shell 실행 도구와 구별합니다. 모델 텍스트로 명령·실행 파일·인자를 구성하지 않습니다.

허용 CLI는 0.153.0으로 고정하고 `login status`가 ChatGPT 로그인을 나타내야 합니다. 어댑터는 auth.json을 읽거나 복사하지 않으며, CLI가 자신의 인증 저장소를 사용합니다. 자식 환경에는 PATH·HOME·CODEX_HOME 등 최소 운영 값만 남기고 API 키, 봇 토큰, 연결 URL, 프록시, 부모 세션 값은 전달하지 않습니다. `forced_login_method=chatgpt`와 `model_provider=openai`도 명시합니다.

발언마다 저장소 밖에 임시 작업 디렉터리를 만들고 `--ignore-user-config`, `--strict-config`, `--ephemeral`, `--sandbox read-only`, `approval_policy=never`, `project_doc_max_bytes=0`을 사용합니다. 사용자 MCP 설정, 셸·unified exec·Code Mode·앱·플러그인·hooks·브라우저·컴퓨터 사용·멀티에이전트·이미지 생성·웹 검색을 비활성화합니다. `read-only`는 쓰기 방어 계층이며 셸 실행 금지의 대체물이 아닙니다. 기존 CLI의 파일 변경 도구 요청도 읽기 전용 정책의 대상이며, 도구 이벤트를 포함한 결과는 승인 제안으로 수용하지 않습니다. JSONL 이벤트 거절은 사후 출력 검증이므로 실행 방지 장치 자체로 취급하지 않습니다. OS 수준의 적대적 CLI 바이너리 격리나 실제 모델의 공격적 도구 호출 검증까지 완료했다는 의미는 아닙니다.

공통·역할 지침은 developer 설정으로 전달하고 외부 자료는 stdin의 비신뢰 JSON으로 전달합니다. `--output-schema`는 임시 스키마를 사용하며 최종 `agent_message`와 `turn.completed`가 있어야 합니다. 실패·알 수 없는 이벤트·도구 사용·잘못된 JSON·추가 필드·실행하지 않은 테스트 통과 주장은 거절합니다. 이후 기존 오케스트레이터가 단계별 참조 및 승인 필요 항목을 다시 검사합니다.

입력은 100,000자, 최종 JSON은 400,000바이트, stdout/stderr는 각각 2,000,000바이트로 제한합니다. 출력 스트림을 동시에 제한된 크기로 읽으며 timeout 또는 취소 시 프로세스 그룹을 종료하고 회수합니다. 임시 스키마·로그·상태 파일도 삭제합니다. 시간 초과만 유한 재시도하고 재시도도 공통 분당 호출 예산을 소비합니다. 인증·CLI 종료·형식 오류에는 자동 재시도나 Mock 전환이 없습니다. CLI stderr 원문과 Pydantic의 원본 입력 오류는 사용자에게 노출하지 않습니다.

구현 시 로컬 `codex --version`, `codex exec --help`, `codex features list`, `codex login status`와 [OpenAI Codex 설정 스키마](https://raw.githubusercontent.com/openai/codex/main/codex-rs/core/config.schema.json)를 확인했습니다. 외부 스키마는 로컬 버전과 달라질 수 있으므로 향후 버전의 호환성을 추정하지 않습니다. Mock subprocess 테스트는 어댑터 계약의 근거이며 실제 CLI 모델 추론과 도구 제한의 end-to-end 검증을 대체하지 않습니다.

`LLMProvider`는 `generate(AgentInput) → AgentOutput` 계약입니다. 다른 LLM을 연결해도 서비스는 제안과 승인 경계를 유지합니다. `Orchestrator`는 순서와 검토 규칙만 담당하므로 분기와 재개 요구가 커지면 해당 계층을 LangGraph로 전환할 수 있습니다.

`ports.py`의 `DevelopmentTools`는 향후 `apply_patch`, `run_tests`, `create_pull_request`를 구현할 위치입니다. 현재 `DisabledDevelopmentTools`는 이 작업을 거절하며 provider나 workflow에 연결되지 않습니다. 실제 실행을 추가할 때는 별도 인간 승인, 대상 저장소·명령 범위 제한, 실행 격리와 증거 모델을 먼저 설계해야 합니다.

현재 단계에는 목표 변경, 기존 작업 계획 대체, 자동 회의 조건, 장기 개인 메모리, 실행 중 회의의 중간 단계 재개, 테스트 실행, PR 생성, Notion 양방향 동기화, 다중 호스트 운영이 없습니다. 완료된 구현의 범위와 실서비스 검증을 구분해야 하며, 테스트 실행 기록은 [TESTING.md](TESTING.md)에 남깁니다. 이번 환경에서는 APScheduler 실제 라이브러리가 없어 등록 검증을 건너뛰었고, Discord·Notion·Groq 인증 연결과 GitHub live 조회도 완료하지 못했습니다.

공식 API 판단 근거는 [Notion data source](https://developers.notion.com/reference/data-source), [현재 database 메타데이터](https://developers.notion.com/reference/retrieve-database), [Groq 구조화 출력](https://console.groq.com/docs/structured-outputs), [discord.py interactions](https://discordpy.readthedocs.io/en/stable/interactions/api.html), [APScheduler 3.x](https://apscheduler.readthedocs.io/en/3.x/userguide.html)를 참고하세요.
