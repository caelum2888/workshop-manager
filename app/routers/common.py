import datetime as dt

from app.services.errors import InvalidData


def resolve_month(year: int | None, month: int | None) -> tuple[int, int]:
    """Ano/mês informados ou, na falta, o mês atual."""
    today = dt.date.today()
    resolved_year, resolved_month = year or today.year, month or today.month
    if not 2000 <= resolved_year <= 2100:
        raise InvalidData("Ano inválido. Informe um valor entre 2000 e 2100.")
    if not 1 <= resolved_month <= 12:
        raise InvalidData("Mês inválido. Informe um valor entre 1 e 12.")
    return resolved_year, resolved_month
