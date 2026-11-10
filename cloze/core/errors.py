"""LLM 호출 경계에서 사용하는 공통 오류."""


class LLMTransientError(Exception):
    """제한된 재시도 후에도 지속되는 일시적 호출 오류."""


class LLMInvalidResponse(Exception):
    """JSON 파싱 또는 응답 스키마 검증 실패."""


class LLMPermanentError(Exception):
    """재시도로 해결되지 않는 인증 또는 설정 오류."""
