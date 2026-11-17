"""Errors the gateway returns as JSON. Nothing here echoes a token or a command."""

from __future__ import annotations

from fastapi.responses import JSONResponse


class GatewayError(Exception):
    def __init__(self, status: int, code: str, message: str, error_type: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.error_type = error_type

    def response(self) -> JSONResponse:
        headers = {"Cache-Control": "no-store"}
        if self.status == 401:
            headers["WWW-Authenticate"] = "Bearer"
        return JSONResponse(
            status_code=self.status,
            content={
                "error": {
                    "message": self.message,
                    "type": self.error_type,
                    "code": self.code,
                }
            },
            headers=headers,
        )


def authentication_required() -> GatewayError:
    return GatewayError(401, "missing_token", "Missing bearer token.", "authentication_error")


def invalid_token() -> GatewayError:
    return GatewayError(401, "invalid_token", "Invalid bearer token.", "authentication_error")


def query_credential() -> GatewayError:
    return GatewayError(
        401,
        "query_credential",
        "Credentials in the query string are not accepted.",
        "authentication_error",
    )


def insufficient_scope() -> GatewayError:
    return GatewayError(
        403,
        "insufficient_scope",
        "Token is not allowed to call this endpoint.",
        "permission_error",
    )
