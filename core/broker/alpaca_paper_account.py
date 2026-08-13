from __future__ import annotations

"""Read-only Alpaca paper-account adapter with no order submission method."""

import hashlib
import json
import os
from typing import Any

import requests

from core.broker.alpaca_paper import AlpacaPaperConfiguration
from core.broker.paper_account_snapshot import PaperBrokerAccountSnapshotLedger
from core.data_sources.provider_configuration import ProviderConfiguration


class AlpacaPaperAccountError(RuntimeError):
    """Secret-safe paper account read failure."""


class AlpacaPaperAccountReader:
    """Fetch and normalize `/v2/account`; cannot create, cancel or replace orders."""

    KEY_ENV = "ALPACA_PAPER_API_KEY"
    SECRET_ENV = "ALPACA_PAPER_API_SECRET"

    def __init__(
        self,
        configuration: AlpacaPaperConfiguration | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.configuration = configuration or AlpacaPaperConfiguration.from_environment()
        self.session = session or requests.Session()

    @property
    def configured(self) -> bool:
        ProviderConfiguration.load_local_environment()
        return bool(os.getenv(self.KEY_ENV) and os.getenv(self.SECRET_ENV))

    def read(self) -> dict[str, Any]:
        if not self.configured:
            return {
                "status": "NOT_CONFIGURED",
                "broker": "Alpaca",
                "broker_environment": "PAPER",
                "required_environment_variables": [self.KEY_ENV, self.SECRET_ENV],
                "account_read": False,
                "order_submitted": False,
                "live_trading_enabled": False,
            }
        headers = {
            "APCA-API-KEY-ID": str(os.getenv(self.KEY_ENV)),
            "APCA-API-SECRET-KEY": str(os.getenv(self.SECRET_ENV)),
        }
        try:
            response = self.session.get(
                f"{self.configuration.endpoint}/v2/account",
                headers=headers,
                timeout=20,
            )
        except requests.RequestException as error:
            raise AlpacaPaperAccountError("Alpaca paper account could not be reached.") from error
        if not response.ok:
            raise AlpacaPaperAccountError(
                f"Alpaca paper account rejected the read (HTTP {response.status_code})."
            )
        try:
            payload = response.json()
        except ValueError as error:
            raise AlpacaPaperAccountError("Alpaca paper account returned unreadable data.") from error
        if not isinstance(payload, dict):
            raise AlpacaPaperAccountError("Alpaca paper account response is not an object.")
        try:
            account_id = str(payload["id"]).strip()
            status = str(payload["status"]).upper()
            currency = str(payload["currency"]).upper()
            cash = str(payload["cash"])
            buying_power = str(payload["buying_power"])
            equity = str(payload["equity"])
        except KeyError as error:
            raise AlpacaPaperAccountError("Alpaca paper account response is incomplete.") from error
        if not account_id or status != "ACTIVE" or currency != "USD":
            raise AlpacaPaperAccountError("Alpaca paper account identity or status is unsupported.")
        # The account endpoint does not provide a universal settled/unsettled
        # cash decomposition.  Do not invent one: mark those fields unavailable
        # until activity/reconciliation evidence supplies them.
        canonical_payload = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        return {
            "status": "COMPLETE",
            "broker": "Alpaca",
            "broker_environment": "PAPER",
            "account_reference_sha256": hashlib.sha256(account_id.encode("utf-8")).hexdigest(),
            "source_payload_sha256": hashlib.sha256(canonical_payload).hexdigest(),
            "currency": currency,
            "cash": cash,
            "settled_cash": None,
            "unsettled_cash": None,
            "buying_power": buying_power,
            "equity": equity,
            "account_status": status,
            "settlement_breakdown_available": False,
            "raw_payload_returned": False,
            "account_read": True,
            "order_submitted": False,
            "live_trading_enabled": False,
        }

    def record_snapshot(
        self,
        ledger: PaperBrokerAccountSnapshotLedger,
        *,
        observed_at: str,
        settled_cash: Any | None = None,
        unsettled_cash: Any | None = None,
        recorded_at: str | None = None,
    ) -> dict[str, Any]:
        result = self.read()
        if result.get("status") != "COMPLETE":
            return result
        if settled_cash is None or unsettled_cash is None:
            return {
                **result,
                "status": "SETTLEMENT_EVIDENCE_REQUIRED",
                "snapshot_recorded": False,
            }
        snapshot = ledger.record(
            broker="Alpaca",
            account_reference_sha256=result["account_reference_sha256"],
            observed_at=observed_at,
            recorded_at=recorded_at,
            cash=result["cash"],
            settled_cash=settled_cash,
            unsettled_cash=unsettled_cash,
            buying_power=result["buying_power"],
            equity=result["equity"],
            source_payload_sha256=result["source_payload_sha256"],
            paper_account_confirmed=True,
        )
        return {"status": "RECORDED", "snapshot": snapshot}
