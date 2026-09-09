import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError

logger = logging.getLogger(__name__)


class DomainError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}


def error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    retryable: bool,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    trace_id = getattr(request.state, "trace_id", str(uuid.uuid4()))
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "retryable": retryable,
                "trace_id": trace_id,
                "details": details or {},
            }
        },
        headers={"X-Trace-ID": trace_id},
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            {"path": ".".join(str(part) for part in error["loc"]), "message": error["msg"]}
            for error in exc.errors()
        ]
        return error_response(
            request,
            422,
            "VALIDATION_ERROR",
            "Request validation failed.",
            False,
            {"fields": fields},
        )

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError) -> JSONResponse:
        return error_response(
            request, exc.status_code, exc.code, exc.message, exc.retryable, exc.details
        )

    @app.exception_handler(OperationalError)
    async def unavailable(request: Request, exc: OperationalError) -> JSONResponse:
        logger.exception("database unavailable trace_id=%s", request.state.trace_id)
        return error_response(
            request,
            503,
            "DATABASE_UNAVAILABLE",
            "Database is unavailable; retry after connectivity is restored.",
            True,
        )

    @app.exception_handler(IntegrityError)
    async def constraint_conflict(request: Request, exc: IntegrityError) -> JSONResponse:
        sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
        logger.info("database constraint conflict trace_id=%s", request.state.trace_id)
        return error_response(
            request,
            409,
            "CONSTRAINT_CONFLICT",
            "A concurrent request created a conflicting record; refresh before retrying.",
            False,
            {"sqlstate": sqlstate} if sqlstate else {},
        )

    @app.exception_handler(DBAPIError)
    async def db_error(request: Request, exc: DBAPIError) -> JSONResponse:
        sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
        if sqlstate in {"40001", "40P01"}:
            return error_response(
                request,
                409,
                "TRANSACTION_CONFLICT",
                "A concurrent update won; refresh and retry.",
                True,
                {"sqlstate": sqlstate},
            )
        logger.exception("database operation failed trace_id=%s", request.state.trace_id)
        return error_response(
            request,
            500,
            "DATABASE_ERROR",
            "The database rejected the operation.",
            False,
            {"sqlstate": sqlstate} if sqlstate else {},
        )
