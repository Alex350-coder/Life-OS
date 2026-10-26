"""Jerarquía de errores de Life OS."""


class LifeOSError(Exception):
    """Raíz de todos los errores propios de la aplicación."""


class DomainError(LifeOSError):
    """Violación de una regla del dominio."""


class ValidationError(DomainError):
    """Un valor no cumple las reglas de su campo."""

    def __init__(self, field: str, value: object, reason: str) -> None:
        super().__init__(f"{field}: valor {value!r} no válido: {reason}")
        self.field = field
        self.value = value
        self.reason = reason
