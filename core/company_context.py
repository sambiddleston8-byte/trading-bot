from dataclasses import dataclass


@dataclass
class CompanyContext:

    symbol: str

    info: dict

    financials: object

    balance_sheet: object

    cashflow: object

    history: object

    news: list | None = None

    validated_financial_data: dict | None = None
