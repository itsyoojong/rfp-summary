# AI 분석 ValueError 진단 결과

2026-11-20. 커밋·Push·배포·실제 Groq 호출은 수행하지 않았다. 기존 미커밋 변경 위에 진단 변경만 추가했다.

## 확인된 사실과 미확인 원인

배포 오류의 실제 원인은 **미확정**이다. Cloud 서버 로그를 직접 읽지 못했으며 원래 실패 응답도 제공되지 않았다. 로컬 `GROQ_API_KEY`는 비어 있지 않지만 유효성·Cloud 설정·모델 접근 권한은 실요청 없이 확인할 수 없다.

수정 전 `src/rfp_analyzer.py`의 `analyze_rfp_document()`를 메모리에서 실행하고 SDK 응답만 Mock한 결과:

| 입력 조건 | 실제 예외 | 수정 전 발생 위치 |
| --- | --- | --- |
| 응답 content가 빈 문자열 | ValueError / 모델 응답이 비어 있습니다. | 162행 |
| content가 JSON 배열 `[]` | ValueError / JSON 객체가 반환되지 않았습니다. | 167행 |
| content가 유효한 JSON이 아님 | JSONDecodeError (ValueError 하위 클래스) | 164행 json.loads |

이들은 재현 가능한 오류 경로이며 사용자가 겪은 배포 오류와 동일한 원인이라고 단정할 수 없다. 수정 전 다른 명시적 ValueError는 페이지 없음(74행), 추출 텍스트 없음(84행), API 키 없음(90행)이다. 그룹 분석은 키 확인 → Groq 요청 → 응답 파싱 → 세션 저장 순서다. 공개 모드의 `persist_groups()`는 아무 파일도 쓰지 않으며 이 경로에서 그룹 키나 응답 구조를 변환하지 않는다.

Groq HTTP 실패는 기존에도 APIStatusError 경로로 처리되므로 `분석 오류: ValueError`만으로 토큰 제한을 판단할 수 없다. 429는 요청/토큰 한도 중 어느 것인지 이 상태 코드만으로 확정할 수 없다. 문자 수는 토큰 사용량이 아니다.

## 수정 내용

- `src/diagnostics.py`: 실패 단계, 예외 클래스, 안전한 고정 오류 코드, 프로젝트 내부 파일·함수·줄 번호, 페이지/그룹·문자 수, 실제 SDK HTTP 오류 상태만 기록한다. 예외 메시지 원문, traceback 코드 줄·지역변수, 키, 환경변수 값, 문서명·해시·원문, 프롬프트, 응답 본문, 요청 헤더는 기록하지 않는다. 별도 디스크 로그 파일은 생성하지 않는다.
- `src/rfp_analyzer.py`: 요청·응답 수신·JSON 파싱·구조 검증을 구분한다. 빈 응답, length 중단, 비객체 JSON, 5개 필수 필드와 value/sources/page/quote 자료형 오류를 세션 저장 전에 거절한다. 원래 `JSONDecodeError`의 클래스와 위치는 로그에 보존하되 화면에는 안전한 안내만 표시한다.
- `app.py`: 업로드 읽기, PDF 추출, 그룹 생성, 세션 저장, 후보 병합·대표값 선택, 사업비·원문 검증 실패 위치를 기록한다. 사용자에게 인증/권한/요청 제한/입력 크기/잘못된 요청/타임아웃/응답 형식 오류를 구분해 안내한다. 로컬 캐시 복원·저장 실패도 안전하게 기록한다.
- `tests/test_rfp_analyzer.py`, `tests/test_app.py`: Mock과 AppTest 검증 추가.

오류를 정상 결과로 바꾸거나 실패한 그룹 번호를 진행시키지 않는다. 정상 응답은 기존 dict 형태로 반환한다. 기존 프롬프트 두 개는 AST 비교로 변경이 없음을 확인했다. 모델·요청 파라미터·3페이지 그룹·후보 선택·원문 검증 알고리즘·캐시 정책·평가 데이터는 변경하지 않았다.

기존에는 통과하던 비정상 응답은 이제 저장하지 않는다. 이 경우 자동 재호출하거나 정상 데이터를 만들어 채우지 않으며, 사용자가 다시 시도할 수 있다. JSON 문법만 맞는 응답이 스키마까지 맞는다는 보장은 없다.

## 단계별 로그 확인

| stage | 확인할 위치 |
| --- | --- |
| upload_read | 업로드 파일 읽기·해시 계산 |
| pdf_extract | pypdf 로드·텍스트 추출 |
| group_creation | 3페이지 그룹·진행률 처리 |
| analysis_input | 분석 페이지·입력 텍스트 검사 |
| groq_request | 키 설정·SDK 생성·API 요청 |
| llm_response | choices/content 확인, 길이 제한 중단 |
| json_parse | JSON 디코딩 |
| response_validation | 필수 필드 및 자료형 |
| session_save | 검증 후 페이지/그룹 결과 세션 저장 |
| candidate_merge / candidate_resolve | 후보 병합·대표값 선택 |
| cache_restore / cache_save | 명시적 로컬 캐시 읽기·쓰기 |
| budget_validation / source_validation / result_display | 검증·표시 |

`error_code`는 안전한 고정 코드다. `locations`는 프로젝트 내부 호출 위치만 포함하며 실제 예외 메시지 전체를 출력하지 않는다. 안쪽 단계에서 기록한 오류는 바깥에서 중복 기록하지 않는다. `page_count`는 해당 단계에서 처리하는 페이지 수이며 API 단계에서는 요청에 포함된 페이지 수다. `input_chars`는 페이지 표기를 포함한 입력 텍스트 문자 수로, 실제 토큰 수나 프롬프트 전체 길이가 아니다.

| 오류 | 코드 |
| --- | --- |
| 키 미설정 | missing_api_key |
| 401 / 403 | authentication / permission |
| 429 | rate_limit |
| 413 | input_size |
| 400/422 + 명시적인 context 초과 코드 | context_limit |
| 그 밖의 400/422 | bad_request |
| 5xx | api_server |
| 빈 응답 / 길이 중단 | empty_response / truncated_response |
| JSON 문법 / 응답 구조 | invalid_json / invalid_schema |

명시적 코드가 없는 400 오류를 컨텍스트 초과로 추측하지 않는다. 일반적인 잘못된 요청 안내와 HTTP 상태만 남긴다. 알려지지 않은 예외는 원문 메시지 대신 `unexpected`로 표시하고 위치를 보존한다.

## 테스트 결과

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q app.py src tests
```

**38개 PASS, FAIL 0, ERROR 0.** 기존 22개에 분석기 검증 14개와 AppTest 2개를 추가했다. 각 테스트의 subTest/반복 입력은 별도 테스트 개수로 부풀리지 않았다.

정상 응답, 빈 응답·choices, JSON 문법/객체/필수 필드/자료형, 응답 길이 중단, 키 미설정, 401·403·400·413·429·500, 컨텍스트 초과 코드, 연결·타임아웃, 기존 단일 텍스트 분석 계약을 검증했다. 키·문서·응답을 닮은 가짜 비밀 문자열이 오류 로그와 화면 메시지에 포함되지 않는지 검사했다. 실제 Groq 호출은 없다.

AppTest에서 빈 응답 → 그룹 미저장 → 재시도 성공, 세션 쓰기 실패의 안전한 기록을 확인했다. 기존 로컬 캐시 저장·복원과 복원 실패 시 이전 문서 초기화 테스트도 통과했다. 문법 및 diff 공백 검사도 통과했다.

## Cloud에서 필요한 다음 확인

1. 변경 코드를 검토·배포한 뒤 실제 앱에서 실패하던 그룹을 한 번 실행한다. 이번 작업은 배포하지 않았다.
2. 사용자 화면에 표시된 분류와 서버의 `rfp.diagnostics` 로그에서 해당 실패 기록을 확인한다.
3. 원인 확정에 필요한 최소 정보는 `stage`, `error_code`, `exception`, `locations`, 존재할 경우 `http_status`, `group_number`, 페이지 범위와 `input_chars`다. 키·원문·응답 전체는 공유하지 않는다.
4. `missing_api_key`면 Cloud Secrets의 GROQ_API_KEY 설정 여부를, 401이면 키 유효성을, 403이면 권한을 확인한다. 값을 화면이나 로그에 출력하지 않는다.
5. 429면 Groq Console의 계정별 사용량·제한을 확인한다. `context_limit` 또는 `input_size`가 실제로 확인된 경우에만 입력 분할 조정을 검토한다. 현재는 원인을 추측해 그룹 크기나 모델을 바꾸지 않았다.
6. JSON/구조 오류면 실패 코드를 근거로 응답 처리를 후속 검토한다. 빈 응답·길이 중단은 JSON 오류와 구분한다.

실제 API 키 유효성·계정별 모델 접근·실제 문서 응답·Cloud 서버 실행·정상 분석 복구 여부는 이번 Mock 테스트만으로 보장할 수 없다.

공식 문서 확인: [GPT-OSS 20B 및 JSON Object Mode](https://console.groq.com/docs/model/openai/gpt-oss-20b), [GPT-OSS reasoning_effort low 지원](https://console.groq.com/docs/reasoning), [HTTP 오류 구분](https://console.groq.com/docs/errors). 현재 설정은 문서에 있는 조합이지만 계정별 실제 요청 성공 여부는 별도 확인이 필요하다.
