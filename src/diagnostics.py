"""예외 본문·소스 코드·지역변수 없이 실패 위치만 기록한다."""
import json
import logging
from contextlib import contextmanager
from pathlib import Path

from groq import APIConnectionError, APIStatusError, APITimeoutError

LOGGER = logging.getLogger("rfp.diagnostics")
ROOT = Path(__file__).resolve().parent.parent
MESSAGES = {
    "missing_api_key": "Groq API 키가 설정되지 않았습니다. 실행 환경의 GROQ_API_KEY 설정을 확인하세요.",
    "no_pages": "분석할 페이지가 없습니다.",
    "no_text": "선택한 페이지에서 분석할 텍스트를 추출하지 못했습니다.",
    "empty_response": "AI 응답이 비어 있습니다. 분석 결과를 저장하지 않았습니다.",
    "truncated_response": "AI 응답이 길이 제한으로 중단되었습니다. 분석 결과를 저장하지 않았습니다.",
    "invalid_json": "AI 응답을 JSON으로 읽지 못했습니다. 분석 결과를 저장하지 않았습니다.",
    "invalid_schema": "AI 응답 구조가 예상 형식과 다릅니다. 분석 결과를 저장하지 않았습니다.",
    "authentication": "Groq API 인증에 실패했습니다. 서버의 API 키 설정을 확인하세요.",
    "permission": "Groq API 접근 권한이 없습니다. 키와 모델 사용 권한을 확인하세요.",
    "rate_limit": "Groq API 요청 한도에 도달했습니다. 잠시 후 다시 시도하세요.",
    "context_limit": "Groq API가 입력 길이 초과를 반환했습니다. 선택한 페이지 범위를 확인하세요.",
    "input_size": "Groq API가 요청 본문 크기 초과를 반환했습니다. 입력 페이지 범위를 줄여 확인하세요.",
    "bad_request": "Groq API가 요청을 거부했습니다. 서버 로그에서 요청 형식·모델 설정을 확인하세요.",
    "api_server": "Groq API 서버 오류입니다. 잠시 후 다시 시도하세요.",
    "api_status": "Groq API 요청이 실패했습니다. 서버 로그의 HTTP 상태를 확인하세요.",
    "timeout": "Groq API 응답 시간이 초과됐습니다. 잠시 후 다시 시도하세요.",
    "connection": "Groq API에 연결하지 못했습니다. 서버 네트워크를 확인하세요.",
    "unexpected": "처리 중 오류가 발생했습니다. 서버의 rfp.diagnostics 로그를 확인하세요.",
}


class AnalysisError(ValueError):
    def __init__(self, code):
        if code not in MESSAGES:
            raise ValueError("Unknown diagnostic code")
        self.code = code
        super().__init__(MESSAGES[code])


def error_code(error):
    if isinstance(error, json.JSONDecodeError):
        return "invalid_json"
    if isinstance(error, AnalysisError):
        return error.code
    if isinstance(error, APITimeoutError):
        return "timeout"
    if isinstance(error, APIConnectionError):
        return "connection"
    if isinstance(error, APIStatusError):
        status = error.status_code
        if status == 401:
            return "authentication"
        if status == 403:
            return "permission"
        if status == 429:
            return "rate_limit"
        if status == 413:
            return "input_size"
        if status in (400, 422):
            body = error.body
            detail = body.get("error", body) if isinstance(body, dict) else {}
            code = detail.get("code") if isinstance(detail, dict) else None
            if code in ("context_length_exceeded", "context_window_exceeded"):
                return "context_limit"
            return "bad_request"
        if status >= 500:
            return "api_server"
        return "api_status"
    return "unexpected"


def user_message(error):
    return MESSAGES[error_code(error)]


def log_failure(stage, error, **metadata):
    if getattr(error, "_rfp_logged", False):
        return
    payload = {
        "stage": stage, "failed": True,
        "exception": type(error).__name__, "error_code": error_code(error),
    }
    for key in ("page_count", "group_number", "page_start", "page_end", "input_chars"):
        if type(metadata.get(key)) is int:
            payload[key] = metadata[key]
    if isinstance(error, APIStatusError):
        payload["http_status"] = error.status_code
    # traceback.format_exc / logger.exception는 예외 메시지와 문서 내용을 노출할 수 있다.
    locations = []
    traceback = error.__traceback__
    while traceback:
        frame = traceback.tb_frame
        path = Path(frame.f_code.co_filename).resolve()
        if path.is_relative_to(ROOT) and ".venv" not in path.parts:
            locations.append({"file": str(path.relative_to(ROOT)),
                              "function": frame.f_code.co_name, "line": traceback.tb_lineno})
        traceback = traceback.tb_next
    payload["locations"] = locations
    LOGGER.error(json.dumps(payload, ensure_ascii=False))
    error._rfp_logged = True


@contextmanager
def analysis_stage(stage, **metadata):
    try:
        yield
    except Exception as error:
        log_failure(stage, error, **metadata)
        raise
