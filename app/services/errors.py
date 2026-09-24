"""Erros de negócio independentes de HTTP.

Os services lançam estes erros; a camada web (main.py) converte em
respostas JSON. Um agente que chame os services diretamente pode
tratá-los sem conhecer FastAPI.
"""


class ServiceError(Exception):
    status_code = 400

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFound(ServiceError):
    status_code = 404


class Conflict(ServiceError):
    status_code = 409


class InvalidData(ServiceError):
    status_code = 422


class Unauthenticated(ServiceError):
    status_code = 401


class Forbidden(ServiceError):
    status_code = 403
