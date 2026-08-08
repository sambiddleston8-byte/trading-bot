from dataclasses import dataclass


@dataclass
class CompanyContext:

    symbol: str

    info: dict

    financials: object

    balance_sheet: object

    cashflow: object

    history: object | None = None

    news: list | None = None