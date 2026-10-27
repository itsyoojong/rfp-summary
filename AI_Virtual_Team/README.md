# AI Virtual Team

인간 리더가 승인하는 Python 기반 가상 개발팀 MVP입니다. PM, Developer, QA가 다섯 단계의 회의에서 서로의 의견을 검토하고 작업 초안을 만듭니다. 프로젝트, 발언, 승인, 작업, 보고서는 SQLite에 남고, Discord는 대화 창구, Notion은 동기화된 프로젝트 관리 화면으로 사용합니다.

실제 AI 회의는 ChatGPT에 로그인한 로컬 Codex CLI로 진행합니다. 세 역할에 서로 다른 프롬프트와 문맥을 전달하며, 승인과 영속 상태는 기존 Python 서비스가 관리합니다. Mock 모드와 데모는 Codex나 외부 API를 호출하지 않습니다.

이 MVP의 Developer는 요구사항과 GitHub 기록을 검토하고 구현 계획을 제안합니다. 외부 저장소의 코드를 수정하거나 테스트를 실행하거나 PR을 만들지는 않습니다. 기존 [RFP Insight 저장소](https://github.com/itsyoojong/rfp-summary)는 읽기 전용 연결 대상으로만 사용합니다. 계획의 목표를 변경하는 기능과 자동 회의 소집 규칙도 포함하지 않습니다.

설계 이유는 [ARCHITECTURE.md](ARCHITECTURE.md), 단계별 진행 기록은 [PLANS.md](PLANS.md), 실행한 검증과 남은 제한은 [TESTING.md](TESTING.md)에 있습니다.

## 설치와 첫 실행

Mac 또는 Linux에서 Python 3.11 이상을 준비하고, 이 README가 있는 디렉터리에서 실행합니다. `python3 --version`이 3.11 미만이면 설치된 3.11 이상의 Python 실행 파일을 사용하세요.

```bash
python3 --version
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
```

이미 `.env`를 설정한 경우 마지막 복사 명령은 다시 실행하지 않습니다. `.env`, 가상환경, SQLite 파일은 `.gitignore`에서 제외합니다. 저장소에 키를 직접 적지 마세요.

API 키 없이 전체 흐름을 먼저 확인할 수 있습니다.

```bash
python -m ai_virtual_team demo --db runtime/demo.sqlite3
python -m ai_virtual_team demo --db runtime/demo.sqlite3
```

데모는 외부 연결을 강제로 비활성화하고, Mock 에이전트의 다섯 발언과 세 작업을 만듭니다. 데모 전용 시뮬레이션 리더가 계획을 승인한 뒤 같은 날짜의 보고서 중복 방지와 DB를 다시 열었을 때의 상태 보존을 확인합니다. 이 시뮬레이션 승인은 실제 Discord 사용자의 승인을 뜻하지 않습니다. 두 번째 실행에서는 기존 데모 데이터를 재사용합니다. 운영 DB인 `runtime/team.sqlite3`와 별도로 사용하세요. 명시적으로 `--db`를 지정하더라도 일반 프로젝트가 든 DB는 복구·변경 전에 거절합니다.

정상 설치 후에는 `ai-team` 명령도 `python -m ai_virtual_team`과 같은 기능을 제공합니다.

## 환경변수

설정은 실행 위치의 `.env`와 환경변수에서 읽습니다. 실제 값이 필요한 키와 ID는 발급받거나 확인한 값을 직접 입력합니다. 미설정 값을 임의로 만들지 않습니다.

| 환경변수 | 기본값 또는 용도 |
| --- | --- |
| `DATABASE_PATH` | `runtime/team.sqlite3` |
| `LLM_MODE` | 설정이 없으면 `mock`; `.env.example`은 `codex`. 기존 `groq`는 명시 선택 시만 사용 |
| `CODEX_BINARY` | `codex`; CLI 실행 파일 경로를 지정할 수 있음. 셸 명령 문자열은 불가 |
| `CODEX_MODEL` | 생략하면 CLI 기본 모델. 계정에서 지원하는 모델 ID를 선택적으로 지정 |
| `CODEX_TIMEOUT_SECONDS` | Codex 추론 시도당 120초, 최대 설정 600초 |
| `GROQ_MODEL` | 이전 Groq 모드 전용; `openai/gpt-oss-20b` |
| `GROQ_API_KEY` | 이전 `LLM_MODE=groq` 전용. Codex에는 전달하지 않음 |
| `LLM_TIMEOUT_SECONDS` | Groq 호출 시도당 30초; Codex에는 적용하지 않음 |
| `LLM_MAX_RETRIES` | 최초 호출 이후 최대 2회. Codex는 시간 초과만 재시도하며 인증·출력 오류는 즉시 실패 |
| `LLM_CALLS_PER_MINUTE` | 프로세스 전체에서 재시도를 포함해 분당 20회 |
| `LLM_MAX_OUTPUT_TOKENS` | Groq 응답당 최대 4,000 토큰. Codex는 최종 JSON 400 KB 및 출력 스트림별 2 MB 제한 |
| `MEETING_MAX_TURNS` | 기본 5; 5~12를 허용하지만 현재 회의는 고정된 5단계 |
| `DISCORD_BOT_TOKEN` | Discord Developer Portal에서 발급한 Bot token |
| `DISCORD_GUILD_ID` | 개발용 서버 ID; 미설정 시 전역 명령 등록 |
| `ADMIN_USER_IDS` | 실제 관리자 Discord 사용자 ID를 쉼표로 구분; 빈 값이면 관리자 작업 금지 |
| `GITHUB_ENABLED` | `false`; 회의와 보고에서 GitHub를 조회하려면 `true` |
| `GITHUB_TOKEN` | 비공개 저장소 접근 등에 필요한 경우 설정하는 읽기 권한 토큰 |
| `NOTION_TOKEN` | 접근을 허용한 Notion connection의 실제 토큰 |
| `NOTION_VERSION` | `2026-03-11` |
| `NOTION_PROJECTS_DATA_SOURCE_ID` | Projects 테이블의 실제 data source ID |
| `NOTION_TASKS_DATA_SOURCE_ID` | Tasks 테이블의 실제 data source ID |
| `NOTION_MEETINGS_DATA_SOURCE_ID` | Meetings 테이블의 실제 data source ID |
| `NOTION_REPORTS_DATA_SOURCE_ID` | Daily Reports 테이블의 실제 data source ID |
| `DAILY_REPORT_TIME` | 미설정 시 자동 보고 없음; 새 프로젝트에 적용할 서울 시간 `HH:MM` |

Notion 토큰을 설정하면 네 개의 data source ID도 모두 필요합니다. `DAILY_REPORT_TIME`은 새 프로젝트의 기본값이므로 기존 프로젝트의 일정은 `/report_schedule` 또는 CLI `schedule`로 변경합니다.

## 사람이 직접 승인하는 로컬 사용법

`.env`의 `ADMIN_USER_IDS`에 본인의 실제 Discord 사용자 ID를 적고, `LLM_MODE=mock`, `GITHUB_ENABLED=false`로 두면 외부 인증 없이 직접 승인 흐름을 연습할 수 있습니다. 아래 `read` 명령 다음에는 안내된 실제 ID 또는 직전 출력의 ID를 입력하세요. CLI의 `--actor-id`는 로컬 운영자가 전달하는 ID이며 Discord 로그인 인증을 수행하지 않습니다. CLI는 신뢰할 수 있는 본인 컴퓨터에서 사용하는 관리 인터페이스입니다.

먼저 실제 관리자 사용자 ID를 입력하고 프로젝트를 생성합니다.

```bash
read -r AI_TEAM_ACTOR_ID
python -m ai_virtual_team project-create \
  --name 'RFP Insight' \
  --goal 'RFP 분석 결과의 원문 근거와 완료 기준을 검토합니다.' \
  --repository https://github.com/itsyoojong/rfp-summary \
  --actor-id "$AI_TEAM_ACTOR_ID"
```

출력된 프로젝트의 `id`를 입력한 후 회의를 시작합니다.

```bash
read -r AI_TEAM_PROJECT_ID
python -m ai_virtual_team meeting-start "$AI_TEAM_PROJECT_ID" \
  --topic '킥오프: 요구사항, 구현 순서와 완료 기준' \
  --actor-id "$AI_TEAM_ACTOR_ID"
python -m ai_virtual_team meeting-summary "$AI_TEAM_PROJECT_ID"
python -m ai_virtual_team tasks "$AI_TEAM_PROJECT_ID"
```

회의록과 각 작업의 완료 기준을 읽은 다음, 출력된 회의 `id`를 입력합니다. 승인할 때만 다음 명령을 실행합니다. 보류하려면 같은 명령에 `--hold`를 붙입니다.

```bash
read -r AI_TEAM_MEETING_ID
python -m ai_virtual_team approve "$AI_TEAM_PROJECT_ID" "$AI_TEAM_MEETING_ID" \
  --actor-id "$AI_TEAM_ACTOR_ID"
python -m ai_virtual_team status "$AI_TEAM_PROJECT_ID"
python -m ai_virtual_team report "$AI_TEAM_PROJECT_ID"
```

`python -m ai_virtual_team projects`로 저장된 프로젝트를 찾을 수 있습니다. 명령마다 `--db`를 추가하면 다른 DB를 사용할 수 있습니다. CLI에서 만든 프로젝트에는 Discord 서버·채널 연결이 없습니다. Discord 협업과 자동 전송을 사용할 프로젝트는 해당 서버 채널에서 `/project_create`로 생성하세요.

## Discord 연결

[Discord Developer Portal](https://discord.com/developers/applications)에서 애플리케이션과 봇을 만들고 Bot token을 `.env`의 `DISCORD_BOT_TOKEN`에 입력합니다. OAuth2 초대에는 `bot`, `applications.commands` scope를 사용합니다. 프로젝트 채널에는 View Channels, Send Messages, Create Public Threads, Send Messages in Threads, Read Message History 권한을 부여합니다. 이 구현은 슬래시 명령을 사용하므로 Message Content intent를 켤 필요가 없습니다.

Discord 개발자 모드에서 복사한 사용자 ID를 `ADMIN_USER_IDS`에 넣고, 개발 서버 ID를 `DISCORD_GUILD_ID`에 넣으면 해당 서버에 명령을 등록합니다. 서버 ID를 생략하면 전역 등록을 사용하며 표시까지 시간이 걸릴 수 있습니다.

```bash
python -m ai_virtual_team bot
```

봇이 실행되는 동안 같은 DB에 대한 별도의 CLI 프로세스는 잠금으로 거절됩니다. 운영 중에는 Discord 명령을 사용하고, 로컬 관리 명령이 필요하면 봇을 종료한 뒤 실행합니다. `Ctrl+C`로 종료해도 저장된 프로젝트와 회의는 유지됩니다.

아래 대괄호는 선택 인자 표시입니다. 실제 Discord에서는 이름이 붙은 입력 칸으로 제공합니다.

| 명령과 인자 | 동작 | 권한 |
| --- | --- | --- |
| `/project_create name goal [description] [repository]` | 현재 서버·채널에 프로젝트 생성 | 지정 관리자 |
| `/meeting_start project_id topic` | 지정 주제로 다섯 단계 회의 시작 | 프로젝트 채널 사용자 |
| `/meeting_summary project_id` | 최근 회의의 목적, 의견, 합의안, 반대, 미결정, 후속 작업 조회 | 프로젝트 채널 사용자 |
| `/project_status project_id` | 승인 버전, 작업 수, 최근 회의·보고, 동기화 상태 조회 | 프로젝트 채널 사용자 |
| `/task_list project_id` | 작업 상태, 담당자, 설명과 완료 기준 조회 | 프로젝트 채널 사용자 |
| `/plan_approve project_id meeting_id [approve]` | 기본 `true`는 승인, `false`는 보류 | 지정 관리자 |
| `/daily_report project_id` | 오늘 보고서 생성 또는 기존 보고서 조회 | 프로젝트 채널 사용자 |
| `/help` | 명령 안내 | 서버 사용자 |
| `/report_schedule project_id time` | 서울 시간 `HH:MM`으로 설정, `off`로 해제 | 지정 관리자 |
| `/task_update project_id task_id status [evidence_url] [note] [blocker]` | 허용된 작업 상태 전환과 근거 기록 | 지정 관리자 |

프로젝트 명령은 프로젝트를 만든 서버와 채널, 또는 그 채널의 스레드에서만 허용하며 DM은 거절합니다. 회의는 가능하면 공개 스레드에 기록하고, 스레드 생성 권한이 없으면 현재 채널을 사용합니다. 발언마다 `[PM]`, `[Developer]`, `[QA]`를 표시하며, 멘션 알림과 링크 미리보기를 차단하고 긴 메시지를 나눠 보냅니다. 전송 실패가 이미 저장한 발언을 삭제하지 않습니다.

## 작업 상태와 완료 근거

승인 전 작업은 `Proposed`로 저장됩니다. 인간이 해당 회의의 계획을 승인하면 `Approved`가 됩니다. 이후에는 관리자가 `/task_update` 또는 CLI `task-update`로 상태를 기록합니다. AI 발언이 작업을 자동 완료하지 않습니다.

| 현재 상태 | 허용되는 다음 상태 |
| --- | --- |
| `Proposed` | 인간의 계획 승인으로만 `Approved` |
| `Approved` | `In Progress`, `Blocked` |
| `In Progress` | `In Review`, `Blocked` |
| `Blocked` | `In Progress` |
| `In Review` | `Done`, `In Progress`, `Blocked` |
| `Done` | 재검토를 위한 `In Review` |

`Blocked`에는 `blocker`가 필요합니다. `Done`에는 연결된 GitHub에서 확인한 동일 저장소의 병합된 PR 또는 닫힌 Issue URL과, 인간이 완료 기준을 검토한 `note`가 필요합니다. 닫힌 Issue나 병합 PR은 테스트 실행·통과 자체를 증명하지 않습니다. 실제 테스트를 실행하지 않은 경우 검토 기록에도 미실행이라고 적어야 합니다. 이 MVP는 테스트 실행기를 제공하지 않습니다.

## Notion data source 설정

이 구현은 2026-10-26에 확인한 공식 문서에 맞춰 `Notion-Version: 2026-03-11`과 data source 구조를 사용합니다. `database`는 컨테이너이고, 데이터 행의 스키마와 조회 대상은 `data_source`입니다. 기존 `database_id`를 새 `data_source_id` 설정에 그대로 넣지 마세요. [버전 문서](https://developers.notion.com/reference/versioning)와 [data source 설명](https://developers.notion.com/reference/data-source)을 참고할 수 있습니다.

사용자가 Notion에서 Projects, Tasks, Meetings, Daily Reports용 테이블 네 개를 만들고 해당 connection에 접근을 허용합니다. 행 읽기·생성·수정 권한이 필요합니다. 프로그램은 테이블이나 스키마를 자동 생성하지 않습니다. 각 테이블의 data source ID를 확인해 네 환경변수에 입력합니다. 컨테이너 database ID만 알고 있다면 현재 버전으로 `GET /v1/databases/{database_id}`를 호출해 응답의 `data_sources`에서 실제 테이블 ID를 확인할 수 있습니다. 이는 컨테이너 메타데이터 조회이며, 구형 database 기반 행 쓰기 API를 쓰는 것이 아닙니다. [현재 database 조회 문서](https://developers.notion.com/reference/retrieve-database)에 구조가 나와 있습니다.

모든 테이블에 다음 공통 속성을 정확한 이름과 타입으로 만듭니다. `rich_text`는 Notion UI의 텍스트, `select`는 선택, `multi_select`는 다중 선택입니다. `Status`라는 속성 이름을 쓰더라도 타입은 **선택(`select`)**으로 만들어야 합니다.

| 공통 속성 | API 타입 | 역할 |
| --- | --- | --- |
| `Name` | `title` | 행 제목 |
| `External ID` | `rich_text` | `종류:내부ID` 형식의 동기화 식별자 |
| `Revision` | `number` | 내부 상태 버전 |
| `Payload Hash` | `rich_text` | 관리 속성 비교용 해시 |

공통 속성에 더해 아래 속성을 추가합니다. 같은 행에 나열된 속성은 모두 같은 타입입니다.

| 테이블 | 속성명 | API 타입 |
| --- | --- | --- |
| Projects | `Goal`, `Description` | `rich_text` |
| Projects | `Status` | `select` |
| Projects | `GitHub` | `url` |
| Projects | `Created` | `date` |
| Tasks | `Description`, `Acceptance Criteria`, `Links`, `Project ID`, `Evidence`, `Verification` | `rich_text` |
| Tasks | `Agent`, `Priority`, `Status` | `select` |
| Meetings | `Date` | `date` |
| Meetings | `Topic`, `Summary`, `Decisions`, `Follow-ups`, `Project ID` | `rich_text` |
| Meetings | `Participants` | `multi_select` |
| Meetings | `Status` | `select` |
| Daily Reports | `Date` | `date` |
| Daily Reports | `Completed`, `In Progress`, `Blockers`, `Decisions`, `Next Steps`, `GitHub`, `Suggestions`, `Project ID` | `rich_text` |

| 선택 속성 | 사용할 옵션 |
| --- | --- |
| Projects `Status` | `Planning`, `Active` |
| Tasks `Agent`와 Meetings `Participants` | `PM`, `Developer`, `QA` |
| Tasks `Priority` | `High`, `Medium`, `Low` |
| Tasks `Status` | `Proposed`, `Approved`, `In Progress`, `Blocked`, `In Review`, `Done` |
| Meetings `Status` | `Running`, `Pending Approval`, `Approved`, `Held`, `Failed`, `Interrupted` |

`Name`, `External ID`, `Revision`, `Payload Hash`와 나머지 관리 속성은 동기화 대상입니다. Notion에서 직접 작업 상태를 바꿔도 내부 DB로 역반영되지 않습니다. 메모가 필요하면 프로그램이 관리하지 않는 별도 속성을 쓰되, 페이지 수정 시각 변화로 충돌 검토가 발생할 수 있습니다.

```bash
python -m ai_virtual_team notion-schema
python -m ai_virtual_team notion-check
python -m ai_virtual_team sync
```

`notion-schema`는 코드의 정확한 스키마를 인증 없이 출력합니다. `notion-check`는 실제 data source의 속성명과 타입을 읽기 전용으로 검사합니다. 선택 옵션의 적합성이나 전체 쓰기 권한까지 보장하는 검사는 아닙니다. 네 결과가 `valid: true`인지 확인한 후 동기화하세요. 동기화는 `/v1/data_sources/{id}/query`와 `parent.type=data_source_id`인 페이지 생성·수정을 사용합니다.

DB 변경과 동기화 대기 기록을 먼저 함께 저장하므로 Notion 실패가 작업 상태를 지우지 않습니다. 한 번에 최대 8개, 시간 예산 12초로 처리하고, 봇 실행 중에는 60초마다 실패한 동기화를 다시 시도합니다. 대기 항목이 많으면 여러 차례에 걸쳐 반영됩니다.

동일 `External ID`가 여러 개이거나 원격 관리 속성·수정 시각이 달라지면 `conflict`로 보류합니다. 사람이 원격 항목과 DB의 기대값을 비교하고 복구한 뒤 `python -m ai_virtual_team sync --retry-conflicts`로 재검사합니다. 이 옵션은 강제 덮어쓰기가 아닙니다. 원격 값을 과거 값으로 되돌려도 수정 시각이 달라 계속 보류될 수 있으며, 현재 DB가 기대하는 속성과 정확히 일치하면 재조정할 수 있습니다. 범용 충돌 해결·동기화 기준 재설정 UI는 아직 없습니다.

생성 요청이 전송됐을 가능성이 있는데 응답을 잃으면 새 행을 무작정 다시 만들지 않습니다. 다음 시도에서 `External ID`로 기존 행을 찾고, 찾지 못하면 수동 확인을 요구하는 상태로 남깁니다. 연결 오류가 쓰기 전에 발생했거나 API가 생성을 명확히 거절한 경우에만, 이전 불확실한 시도가 없다는 조건 아래 다음 생성 시도를 허용합니다. Notion API에는 이 구현이 사용할 수 있는 원자적 비교·수정이나 유일성 제약이 없으므로 여러 독립 실행기 사이의 정확히 한 번 실행을 보장하지 않습니다.

## Codex CLI와 ChatGPT 로그인 연결

이 어댑터는 로컬에서 확인한 **Codex CLI 0.153.0**만 허용합니다. 도구와 설정의 의미가 바뀔 수 있어 다른 버전은 자동 허용하지 않습니다. 업그레이드하려면 `codex_llm.py`의 도구 제한과 CLI 계약을 먼저 검토하고 테스트해야 합니다.

봇 또는 로컬 CLI를 실행하는 것과 같은 OS 사용자로 로그인합니다. 이미 로그인되어 있다면 `codex login`을 반복할 필요는 없습니다. OpenAI API 키나 Groq API 키는 필요하지 않으며 API 키 방식의 Codex 로그인은 거절합니다.

```bash
codex --version
codex login
python -m ai_virtual_team codex-check
```

기존 `.env`에서 `LLM_MODE=codex`로 변경합니다. CLI가 PATH에 없다면 `CODEX_BINARY`에 절대 경로를 지정합니다. `CODEX_MODEL`은 선택 사항입니다. 관리자 ID, Discord 및 Notion 설정은 별도로 유지합니다. `codex-check`는 버전과 로그인 상태만 확인하며, DB 생성·회의·실제 모델 추론을 수행하지 않습니다. `inference_verified: false`는 의도된 결과입니다.

이후 위의 `project-create` → `meeting-start` → 사람이 검토한 뒤 `approve` 흐름이나 기존 Discord `/meeting_start`를 그대로 사용합니다. 실제 회의는 다섯 차례 Codex 추론을 호출하며 ChatGPT 계정의 사용 한도를 따릅니다. 추론 실패 시 회의를 실패로 기록하고 기존 발언을 보존하며, Mock으로 자동 대체하거나 계획을 자동 승인하지 않습니다.

프로젝트를 editable install하지 않은 환경에서는 아래처럼 소스 경로를 지정할 수 있습니다.

```bash
PYTHONPATH="$PWD/src" .venv/bin/python -m ai_virtual_team codex-check
PYTHONPATH="$PWD/src" .venv/bin/python -m ai_virtual_team demo --db /tmp/ai-team-demo.sqlite3
```

각 호출은 저장소 밖의 임시 디렉터리에서 `codex exec --ignore-user-config --ephemeral --sandbox read-only`로 실행합니다. 사용자 설정의 MCP·플러그인·hooks를 상속하지 않고 실행·브라우저·앱·하위 에이전트 도구 기능을 끕니다. 역할 지침은 developer 설정, 외부 자료는 stdin에 분리해 전달합니다. 출력은 JSON Schema 요청 후 로컬 Pydantic과 회의 단계 검증을 다시 거칩니다. `read-only`만으로 셸을 차단한다고 가정하지 않으며, 구체적인 제한과 남은 검증 범위는 [ARCHITECTURE.md](ARCHITECTURE.md)에 설명합니다.

## 읽기 전용 GitHub 연결

`GITHUB_ENABLED=true`로 설정하면 회의와 보고에서 저장소 기본 브랜치의 파일 구조, 커밋, PR, Issue를 GET으로 조회합니다. 조회량에는 상한이 있으며 누락 가능성과 오류를 보고서에 함께 기록합니다. 파일 트리는 현재 구조이고 변경 파일 목록이 아닙니다. 실패를 변경 없음으로 해석하지 않습니다. Discord 보고서는 기록 종류별 최대 10개와 조회 건수만 보여주며 전체 스냅샷은 DB에 남습니다.

Python 서비스 API에는 `GitHubService.read_file(repository, path, ref=None)`과 `commit_details(repository, sha)`도 있습니다. 전자는 커밋에 고정한 일반 파일 중 64 KiB 이하의 UTF-8 텍스트만 읽고 심볼릭 링크와 서브모듈을 거절합니다. 후자는 최대 50개 변경 파일과 파일당 8 KiB·전체 64 KiB 이하의 patch를 읽습니다. 기본 회의는 메타데이터 스냅샷만 참고하며, 이 상세 조회 API를 LLM이 임의로 호출하지는 않습니다. 실제 소스 내용을 분석하는 흐름을 추가할 때 명시적으로 연결할 수 있는 경계입니다.

```bash
python -m ai_virtual_team github-check \
  --repository https://github.com/itsyoojong/rfp-summary
```

이 점검 명령은 `GITHUB_ENABLED` 값과 별개로 사용자가 지정한 저장소에 실제 GET 요청을 합니다. 공개 저장소는 토큰 없이도 시도할 수 있습니다. 비공개 저장소에 연결할 때는 필요한 읽기 권한만 가진 토큰을 사용합니다.

이전 Groq 어댑터는 호환성을 위해 남겨 두었습니다. 명시적으로 `LLM_MODE=groq`와 `GROQ_API_KEY`를 설정할 때만 사용하며 Codex 실패 시의 대체 경로로 사용하지 않습니다.

## 자동 보고와 재시작

자동 보고는 시각을 명시한 프로젝트에만 등록됩니다. 시간대는 `Asia/Seoul`이며 봇 프로세스가 계속 실행되어 있어야 합니다. 운영체제 예약 작업을 설치하는 방식은 아닙니다. 예를 들어 `/report_schedule project_id:<실제 ID> time:09:00`으로 설정하고 `time:off`로 해제합니다.

일일 보고는 PM의 업무이지만 사실 부분은 LLM이 재작성하지 않습니다. DB의 작업 상태와 검증된 근거, GitHub 조회 결과를 정해진 템플릿으로 구성하고, 미검증 의견은 추정·제안으로 구분합니다. 같은 프로젝트·서울 날짜에는 보고서 하나만 생성합니다. 수동 보고를 다시 요청해도 당일 최초 스냅샷을 반환하며 그날의 보고를 덮어쓰지 않습니다. 최신 작업 상태는 `/project_status`와 `/task_list`에서 확인합니다.

재시작 시 DB의 일정으로 스케줄을 다시 만들고, 오늘의 보고 시각이 지났다면 오늘 보고만 보충합니다. 과거 날짜를 자동으로 소급 작성하지 않습니다. 보고서 생성 중복 방지와 Discord 전송 기록은 별도입니다. 전송 도중 응답을 잃거나 프로세스가 종료되면 `uncertain`으로 남겨 무작정 재전송하지 않습니다. 운영자는 실제 채널을 확인하고 필요할 때 `/daily_report`로 저장된 보고서를 명시적으로 다시 출력합니다. 이 명령은 전송 기록을 강제로 초기화하지 않습니다.

프로세스 재시작으로 끝나지 못한 회의는 `Interrupted`가 되고, 기존 발언은 보존됩니다. 새 회의를 수동으로 시작해 이어서 검토할 수 있으며 중간 발언부터 자동 재개하지는 않습니다.

## 검증 상태와 현재 한계

```bash
python -m pytest -q
python -m ruff check .
python -m ai_virtual_team demo --db /tmp/ai-team-demo.sqlite3
```

테스트는 Mock LLM, 가짜 Codex CLI 프로세스, 임시 SQLite, 모의 HTTP, 로그인하지 않은 Discord 명령을 사용합니다. 정확한 결과는 [TESTING.md](TESTING.md)와 [PLANS.md](PLANS.md)를 확인하세요. APScheduler가 없어 실제 등록 검증은 건너뛰었고, 시간·중복·전송 복구 로직은 별도로 검증했습니다. 의존성 설치 후 봇을 실행하기 전에 해당 검증을 다시 수행해야 합니다.

Codex 0.153.0과 ChatGPT 로그인 상태는 로컬에서 확인했습니다. 이번 변경에서는 실제 Codex 추론 및 실서비스 end-to-end smoke test를 실행하지 않았습니다. Discord·Notion·Groq 인증 연동과 GitHub live 조회도 미검증입니다. 모의 테스트 통과를 실서비스 연결 성공으로 간주하지 않습니다.

이 버전은 단일 Mac/Linux 프로세스를 위한 MVP입니다. 코드 수정·테스트 실행·PR 생성, 목표 수정, 기존 작업 계획의 자동 대체, 이벤트 기반 회의, Notion 양방향 동기화, 다중 프로세스 운영, 범용 충돌 해결 UI, 웹 대시보드는 구현하지 않았습니다. 현재 승인 흐름은 새 작업을 추가로 확정하며 기존 승인 작업을 조용히 덮어쓰지 않습니다.

## 공식 참고 문서

Notion은 [현재 버전](https://developers.notion.com/reference/versioning), [database 메타데이터](https://developers.notion.com/reference/retrieve-database), [data source 조회](https://developers.notion.com/reference/query-a-data-source), [페이지 생성](https://developers.notion.com/reference/post-page)을 참고했습니다. Discord는 [discord.py interactions](https://discordpy.readthedocs.io/en/stable/interactions/api.html), LLM은 [Groq Structured Outputs](https://console.groq.com/docs/structured-outputs), 일정은 [APScheduler 3.x User Guide](https://apscheduler.readthedocs.io/en/3.x/userguide.html)를 기준으로 구현했습니다.

## 로그인 확인 다음: 실제 추론 1회 검사

`PYTHONPATH="$PWD/src" .venv/bin/python -m ai_virtual_team codex-smoke`를 프로젝트 폴더에서 실행합니다. 가상 안건으로 PM 의제 생성만 한 번 요청하며 ChatGPT 계정 사용량이 발생합니다. DB나 GitHub·Discord·Notion 연결을 사용하지 않고, 프로젝트 생성·승인도 하지 않습니다. 성공 시 `inference_verified: true`, `scope: PM agenda only`를 출력합니다. 이 결과는 5단계 회의 전체나 실제 도구 차단 검증 완료를 뜻하지 않습니다. 실패하면 재시도나 Mock 대체 없이 종료합니다.

2026-10-27 작업 환경에서 실제 추론을 시도했으나 Codex 내부 app-server 초기화가 `Operation not permitted`로 실패했습니다. 로그인 확인 성공과 추론 성공을 구분해야 하며, 사용자 터미널에서의 결과는 아직 미확인입니다.
