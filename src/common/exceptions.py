"""
src/common/exceptions.py — 숏폼 하네스 예외 계층

모든 예외는 HarnessError를 기반으로 하며, 사용자가 문제를 스스로 해결할 수 있도록
구체적인 안내 메시지를 포함한다.
"""

from __future__ import annotations


class HarnessError(Exception):
    """하네스의 모든 예외 기반 클래스.

    시스템이 복구 불가능한 상태에 도달했을 때 raise된다.
    서브클래스는 사용자가 문제를 해결할 수 있도록 명확한 안내를 제공해야 한다.

    Args:
        message: 사람이 읽을 수 있는 오류 설명 및 해결 안내.
        **context: 추가 컨텍스트 정보 (서브클래스가 속성으로 저장).
    """

    def __init__(self, message: str, **context: object) -> None:
        super().__init__(message)
        self.message = message
        for key, value in context.items():
            setattr(self, key, value)
        self._context = context

    def __str__(self) -> str:
        return self.message


class ConfigError(HarnessError):
    """환경 변수 또는 설정이 누락·잘못된 경우 raise된다.

    주로 `load_config()`에서 필수 환경 변수(예: GOOGLE_API_KEY)가
    설정되지 않았을 때 발생한다.

    Args:
        message: 누락된 변수와 해결 방법을 포함한 안내 메시지.
        var_name: 누락되거나 잘못된 환경 변수 이름 (선택).

    Example::

        raise ConfigError(
            "GOOGLE_API_KEY 환경 변수가 설정되지 않았습니다.\\n"
            "  발급처: https://aistudio.google.com\\n"
            "  용도: Gemini / Imagen / Veo / TTS API 호출\\n"
            "  설정 방법: .env 파일에 GOOGLE_API_KEY=<your-key> 를 추가하세요.",
            var_name="GOOGLE_API_KEY",
        )
    """

    def __init__(self, message: str, *, var_name: str | None = None, **context: object) -> None:
        super().__init__(message, **context)
        self.var_name = var_name


class UnprocessableRefError(HarnessError):
    """레퍼런스 파일이 존재하지 않거나 mp4 형식이 아닐 때 raise된다.

    `analyze/probe.py`의 `probe()` 함수에서 발생하며, 호출부는 이 예외를
    잡아 해당 파일을 건너뛰고 다른 유효한 파일의 처리를 계속한다.

    Args:
        message: 파일 경로와 문제 원인을 포함한 안내 메시지.
        path: 처리할 수 없는 파일 경로 (선택).

    Example::

        raise UnprocessableRefError(
            f"레퍼런스 파일을 처리할 수 없습니다: {path}\\n"
            f"  원인: 파일이 존재하지 않습니다.\\n"
            f"  해결: mp4 형식의 유효한 파일 경로를 --refs 인수로 전달하세요.",
            path=path,
        )
    """

    def __init__(self, message: str, *, path: str | None = None, **context: object) -> None:
        super().__init__(message, **context)
        self.path = path


class ProfileValidationError(HarnessError):
    """style_profile.json이 스키마 검증을 통과하지 못할 때 raise된다.

    `analyze/synthesize_profile.py`의 `save_profile()`에서 발생하며,
    검증에 실패한 경우 파일이 저장되지 않는다.

    Args:
        message: 위반된 필드 목록과 수정 방법을 포함한 안내 메시지.
        violations: 스키마 검증 위반 항목 목록 (선택).

    Example::

        raise ProfileValidationError(
            f"프로파일이 스키마 검증을 통과하지 못했습니다.\\n"
            f"  위반 항목: {violations}\\n"
            f"  스키마 파일: style_profile.schema.json\\n"
            f"  해결: 위반된 필드를 수정하거나 analyze 단계를 다시 실행하세요.",
            violations=violations,
        )
    """

    def __init__(
        self,
        message: str,
        *,
        violations: list[str] | None = None,
        **context: object,
    ) -> None:
        super().__init__(message, **context)
        self.violations: list[str] = violations if violations is not None else []


class InputError(HarnessError):
    """사용자 입력이 유효하지 않을 때 raise된다.

    `generate/brief.py`의 `build_brief()`에서 발생하며, 지원하지 않는
    파일 형식(jpg/png/mp4/mov 이외)이거나 파일이 존재하지 않을 때 발생한다.

    Args:
        message: 입력 오류와 지원 형식 안내를 포함한 메시지.
        path: 유효하지 않은 파일 경로 (선택).
        kind: 입력 종류 ("text", "image", "video" 등, 선택).

    Example::

        raise InputError(
            f"지원하지 않는 파일 형식입니다: {path}\\n"
            f"  지원 형식: 이미지(jpg, png), 영상(mp4, mov)\\n"
            f"  해결: 지원되는 형식의 파일로 다시 시도하세요.",
            path=path,
            kind="image",
        )
    """

    def __init__(
        self,
        message: str,
        *,
        path: str | None = None,
        kind: str | None = None,
        **context: object,
    ) -> None:
        super().__init__(message, **context)
        self.path = path
        self.kind = kind


class VendorError(HarnessError):
    """외부 API 호출이 재시도를 모두 소진한 후에도 실패할 때 raise된다.

    `common/vendor_client.py`에서 최대 재시도(3회, 지수 백오프) 후에도
    성공하지 못한 경우 발생한다.

    Args:
        message: 벤더·작업 이름과 해결 방법을 포함한 안내 메시지.
        vendor: API 제공자 이름 (예: "Gemini", "Imagen", 선택).
        operation: 실패한 작업 이름 (예: "analyze_video", 선택).

    Example::

        raise VendorError(
            f"Gemini API 호출이 3회 재시도 후에도 실패했습니다 (작업: analyze_video).\\n"
            f"  원인: {original_error}\\n"
            f"  해결: API 키를 확인하고 네트워크 상태를 점검하세요.\\n"
            f"       할당량 초과 시 https://aistudio.google.com 에서 사용량을 확인하세요.",
            vendor="Gemini",
            operation="analyze_video",
        )
    """

    def __init__(
        self,
        message: str,
        *,
        vendor: str | None = None,
        operation: str | None = None,
        **context: object,
    ) -> None:
        super().__init__(message, **context)
        self.vendor = vendor
        self.operation = operation
