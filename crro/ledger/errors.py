"""Typed service errors, mapped to HTTP status codes by the API layer."""


class ServiceError(Exception):
    status = 500
    code = "internal_error"

    def __init__(self, message: str, **details):
        super().__init__(message)
        self.message = message
        self.details = details


class ValidationFailed(ServiceError):
    status, code = 422, "validation_failed"


class BadRequest(ServiceError):
    status, code = 400, "bad_request"


class Unauthorized(ServiceError):
    status, code = 401, "unauthorized"


class Forbidden(ServiceError):
    status, code = 403, "forbidden"


class NotFound(ServiceError):
    status, code = 404, "not_found"


class Conflict(ServiceError):
    status, code = 409, "conflict"


class IdempotencyConflict(Conflict):
    code = "idempotency_key_reused_with_different_payload"


class PayloadTooLarge(ServiceError):
    status, code = 413, "payload_too_large"


class RateLimited(ServiceError):
    status, code = 429, "rate_limited"


class MethodNotAllowed(ServiceError):
    status, code = 405, "method_not_allowed"
