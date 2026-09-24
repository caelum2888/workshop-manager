"""Utilitários de período mensal."""

import calendar
import datetime as dt

MONTH_NAMES = [
    "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]


def month_bounds(year: int, month: int) -> tuple[dt.date, dt.date]:
    last_day = calendar.monthrange(year, month)[1]
    return dt.date(year, month, 1), dt.date(year, month, last_day)


def period_dict(year: int, month: int) -> dict:
    first, last = month_bounds(year, month)
    return {
        "year": year,
        "month": month,
        "month_name": MONTH_NAMES[month - 1],
        "label": f"{MONTH_NAMES[month - 1]}/{year}",
        "start": first,
        "end": last,
    }
