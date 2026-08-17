"""PIT-safe SEC Form 4 normalization and independent insider specialist."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence
import zipfile

import pandas as pd

from core.research.specialist_signals import SpecialistSignal, _decimal, _time


SYMBOLS = ("AAPL", "MSFT", "SPY")
ISSUER_CIKS = {"AAPL": "0000320193", "MSFT": "0000789019"}
TRAIN_START = "2024-10-01"
TRAIN_END = "2025-02-28"
LOOKBACK_DAYS = 60
LOOKBACK_COMPLETE_FROM = "2024-11-30T00:00:00+00:00"
SCHEMA_VERSION = "sec-form4-pit-train-v2"
LEGACY_ADMITTED_SCHEMA_VERSION = "sec-form4-pit-train-v1"
SPECIALIST_VERSION = "sec-form4-cluster-role-intensity-v2"
ROLE_TAXONOMY_VERSION = "whole-token-executive-role-v2"
MAX_ARCHIVE_FILES = 16
MAX_MEMBER_BYTES = 120 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 350 * 1024 * 1024
REQUIRED_TABLES = ("SUBMISSION.tsv", "REPORTINGOWNER.tsv", "NONDERIV_TRANS.tsv")
OFFICIAL_SOURCE_URLS = {
    "2024Q4": "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/2024q4_form345.zip",
    "2025Q1": "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/2025q1_form345.zip",
    "AAPL": "https://data.sec.gov/submissions/CIK0000320193.json",
    "MSFT": "https://data.sec.gov/submissions/CIK0000789019.json",
}


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _parse_date(value: str, name: str) -> datetime:
    months = {
        "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
        "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
    }
    try:
        text = value.strip()
        if len(text) == 10 and text[4] == text[7] == "-":
            return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
        day, month, year = text.upper().split("-")
        return datetime(int(year), months[month], int(day), tzinfo=timezone.utc)
    except (AttributeError, KeyError, TypeError, ValueError):
        pass
    raise ValueError(f"{name} must be a supported SEC date")


def _archive_tables(payload: bytes) -> dict[str, list[dict[str, str]]]:
    if not payload or len(payload) > 25 * 1024 * 1024:
        raise ValueError("SEC Form 4 archive is empty or above the compressed limit")
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as error:
        raise ValueError("SEC Form 4 archive is invalid") from error
    infos = archive.infolist()
    if not infos or len(infos) > MAX_ARCHIVE_FILES:
        raise ValueError("SEC Form 4 archive member count is invalid")
    if any(
        info.is_dir()
        or info.file_size > MAX_MEMBER_BYTES
        or PurePosixPath(info.filename).name != info.filename
        for info in infos
    ):
        raise ValueError("SEC Form 4 archive contains an unsafe member")
    if sum(info.file_size for info in infos) > MAX_UNCOMPRESSED_BYTES:
        raise ValueError("SEC Form 4 archive exceeds the uncompressed limit")
    upper_names = [info.filename.upper() for info in infos]
    if len(set(upper_names)) != len(upper_names):
        raise ValueError("SEC Form 4 archive contains duplicate member names")
    by_name = {info.filename.upper(): info for info in infos}
    if not {name.upper() for name in REQUIRED_TABLES} <= set(by_name):
        raise ValueError("SEC Form 4 archive lacks a required table")
    tables: dict[str, list[dict[str, str]]] = {}
    for name in REQUIRED_TABLES:
        with archive.open(by_name[name.upper()]) as source:
            bounded = source.read(MAX_MEMBER_BYTES + 1)
            if len(bounded) > MAX_MEMBER_BYTES:
                raise ValueError(f"{name} exceeds the decompressed member limit")
            wrapper = io.StringIO(bounded.decode("utf-8-sig"), newline="")
            reader = csv.DictReader(wrapper, delimiter="\t")
            if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
                raise ValueError(f"{name} header is missing or duplicated")
            rows: list[dict[str, str]] = []
            try:
                for row in reader:
                    if None in row or any(value is None for value in row.values()):
                        raise ValueError(f"{name} contains a ragged row")
                    rows.append(dict(row))
            except csv.Error as error:
                raise ValueError(f"{name} contains invalid TSV") from error
            tables[name] = rows
    return tables


def _submission_acceptance(payload: bytes, *, expected_cik: str) -> dict[str, str]:
    if not payload or len(payload) > 25 * 1024 * 1024:
        raise ValueError("SEC submissions JSON is empty or above the byte limit")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate SEC submissions JSON key")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"invalid JSON constant {value}")

    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("SEC submissions JSON is invalid") from error
    if not isinstance(value, dict) or str(value.get("cik", "")).zfill(10) != expected_cik:
        raise ValueError("SEC submissions JSON issuer identity differs from its pin")
    recent = value.get("filings", {}).get("recent", {})
    if not isinstance(recent, dict):
        raise ValueError("SEC submissions JSON lacks recent filing metadata")
    required = ("accessionNumber", "form", "acceptanceDateTime")
    columns = [recent.get(name) for name in required]
    if any(not isinstance(column, list) for column in columns):
        raise ValueError("SEC submissions JSON lacks a required filing column")
    if len({len(column) for column in columns}) != 1:
        raise ValueError("SEC submissions JSON filing columns are misaligned")
    result: dict[str, str] = {}
    for accession, form, accepted in zip(*columns):
        if form != "4":
            continue
        parsed = _time(accepted, "SEC acceptanceDateTime").isoformat()
        if accession in result and result[accession] != parsed:
            raise ValueError("SEC accession has conflicting acceptance timestamps")
        result[accession] = parsed
    return result


def _role_category(relationship: str, title: str) -> tuple[str, Decimal]:
    normalized = str(title).upper().replace(".", "")
    for punctuation in "-,&/()":
        normalized = normalized.replace(punctuation, " ")
    normalized = " ".join(normalized.split())
    tokens = set(normalized.split())
    is_vice = bool(tokens & {"VICE", "VP", "SVP", "EVP"})
    executive_phrases = (
        "CHIEF EXECUTIVE", "CHIEF FINANCIAL", "CHIEF OPERATING"
    )
    if (
        tokens & {"CEO", "CFO", "COO"}
        or any(phrase in normalized for phrase in executive_phrases)
        or (
            (
                {"PRESIDENT", "PRES"} & tokens
                or any(token.startswith("CHAIR") for token in tokens)
            )
            and not is_vice
        )
    ):
        return "SENIOR_EXECUTIVE", Decimal("1.5")
    relation = "".join(
        character
        for character in str(relationship).upper().replace("%", "PERCENT")
        if character.isalnum()
    )
    if "OFFICER" in relation:
        return "OTHER_OFFICER", Decimal("1.25")
    if "DIRECTOR" in relation:
        return "DIRECTOR", Decimal("1")
    if "TENPERCENTOWNER" in relation or "10PERCENTOWNER" in relation:
        return "TEN_PERCENT_OWNER", Decimal("0.75")
    return "OTHER", Decimal("0.5")


def normalize_form4_train_sources(
    *,
    quarter_archives: Mapping[str, bytes],
    issuer_submissions: Mapping[str, bytes],
    retrieved_at: str,
    source_urls: Mapping[str, str],
) -> dict[str, Any]:
    """Join as-filed Form 4 rows to exact EDGAR acceptance timestamps."""
    retrieved = _time(retrieved_at, "retrieved_at")
    if set(quarter_archives) != {"2024Q4", "2025Q1"}:
        raise ValueError("exactly the fixed TRAIN quarter archives are required")
    if set(issuer_submissions) != set(ISSUER_CIKS):
        raise ValueError("exactly the fixed issuer submissions are required")
    if dict(source_urls) != OFFICIAL_SOURCE_URLS:
        raise ValueError("source URL pins differ from the fixed official SEC targets")

    acceptances = {
        symbol: _submission_acceptance(
            issuer_submissions[symbol], expected_cik=ISSUER_CIKS[symbol]
        )
        for symbol in ISSUER_CIKS
    }
    submissions: dict[str, dict[str, str]] = {}
    owners: dict[str, list[dict[str, str]]] = {}
    transactions: dict[str, list[dict[str, str]]] = {}
    for quarter, payload in sorted(quarter_archives.items()):
        tables = _archive_tables(payload)
        for row in tables["SUBMISSION.tsv"]:
            accession = row.get("ACCESSION_NUMBER", "")
            symbol = row.get("ISSUERTRADINGSYMBOL", "").strip().upper()
            if symbol not in ISSUER_CIKS or row.get("DOCUMENT_TYPE") != "4":
                continue
            if row.get("ISSUERCIK", "").zfill(10) != ISSUER_CIKS[symbol]:
                raise ValueError("SEC Form 4 issuer CIK differs from its ticker pin")
            if accession in submissions:
                raise ValueError("SEC Form 4 accession repeats across quarter archives")
            submissions[accession] = {**row, "_quarter": quarter, "_symbol": symbol}
        for row in tables["REPORTINGOWNER.tsv"]:
            owners.setdefault(row.get("ACCESSION_NUMBER", ""), []).append(row)
        for row in tables["NONDERIV_TRANS.tsv"]:
            transactions.setdefault(row.get("ACCESSION_NUMBER", ""), []).append(row)

    records: list[dict[str, Any]] = []
    for accession, submission in sorted(submissions.items()):
        symbol = submission["_symbol"]
        filing_date = _parse_date(submission.get("FILING_DATE", ""), "FILING_DATE")
        if not TRAIN_START <= filing_date.date().isoformat() <= TRAIN_END:
            continue
        accepted_text = acceptances[symbol].get(accession)
        if accepted_text is None:
            raise ValueError("Form 4 accession lacks exact EDGAR acceptanceDateTime")
        accepted = _time(accepted_text, "available_at")
        accepted_date = accepted.date().isoformat()
        if accepted_date < TRAIN_START:
            continue
        if accepted_date > TRAIN_END:
            raise ValueError("in-window Form 4 filing was accepted after TRAIN")
        filing_owners = owners.get(accession, [])
        if len(filing_owners) != 1:
            # Transaction rows have no owner foreign key, so multi-owner filings are
            # deliberately excluded instead of assigning transactions by guesswork.
            continue
        qualifying = [
            row
            for row in transactions.get(accession, [])
            if row.get("TRANS_FORM_TYPE") == "4"
            and row.get("TRANS_CODE") in {"P", "S"}
        ]
        directions = {
            row["TRANS_CODE"]
            for row in qualifying
            if (row["TRANS_CODE"] == "P" and row.get("TRANS_ACQUIRED_DISP_CD") == "A")
            or (row["TRANS_CODE"] == "S" and row.get("TRANS_ACQUIRED_DISP_CD") == "D")
        }
        if len(directions) != 1:
            continue
        transaction_dates = {
            _parse_date(row.get("TRANS_DATE", ""), "TRANS_DATE")
            for row in qualifying
            if row.get("TRANS_CODE") in directions
        }
        if len(transaction_dates) != 1:
            continue
        effective = next(iter(transaction_dates))
        if effective > accepted:
            raise ValueError("Form 4 transaction date follows EDGAR acceptance")
        owner = filing_owners[0]
        role, weight = _role_category(
            owner.get("RPTOWNER_RELATIONSHIP", ""),
            owner.get("RPTOWNER_TITLE", ""),
        )
        owner_cik = owner.get("RPTOWNERCIK", "").zfill(10)
        if len(owner_cik) != 10 or not owner_cik.isdigit():
            raise ValueError("Form 4 reporting-owner CIK is invalid")
        material: dict[str, Any] = {
            "observation_id": "FORM4-" + hashlib.sha256(accession.encode()).hexdigest()[:32].upper(),
            "accession_number": accession,
            "symbol": symbol,
            # The CIK is public SEC data. This stable pseudonym avoids retaining the
            # owner name; it is not represented as anonymization.
            "owner_id_sha256": hashlib.sha256(owner_cik.encode()).hexdigest(),
            "direction": "BUY" if next(iter(directions)) == "P" else "SELL",
            "role_category": role,
            "role_weight": _decimal(weight),
            "effective_at": effective.isoformat(),
            "reported_at": accepted.isoformat(),
            "available_at": accepted.isoformat(),
            "retrieved_at": retrieved.isoformat(),
            "observation_cutoff_at": accepted.isoformat(),
            "revision": 1,
            "prior_revision_sha256": None,
            "provenance": {
                "quarter": submission["_quarter"],
                "quarter_source_sha256": _sha(quarter_archives[submission["_quarter"]]),
                "issuer_submissions_sha256": _sha(issuer_submissions[symbol]),
                "acceptance_field": "filings.recent.acceptanceDateTime",
                "transaction_scope": "NONDERIV_TRANS code P/S only",
                "role_taxonomy_version": ROLE_TAXONOMY_VERSION,
            },
        }
        material["record_sha256"] = _sha(_canonical(material))
        records.append(material)

    artifact: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "partition_role": "TRAIN",
        "window": {"start": TRAIN_START, "end": TRAIN_END},
        "symbols": list(SYMBOLS),
        "source_capture": {
            name: {"url": source_urls[name], "sha256": _sha(payload)}
            for name, payload in sorted({**quarter_archives, **issuer_submissions}.items())
        },
        "records": records,
        "reported_at_equals_available_at": True,
        "available_at_semantics": "EXACT_SEC_EDGAR_ACCEPTANCE_DATETIME",
        "validation_data_read": False,
        "untouched_test_included": False,
    }
    artifact["artifact_sha256"] = _sha(_canonical(artifact))
    return artifact


@dataclass(frozen=True)
class Form4Observation:
    observation_id: str
    accession_number: str
    symbol: str
    owner_id_sha256: str
    direction: str
    role_category: str
    role_weight: str
    effective_at: str
    reported_at: str
    available_at: str
    retrieved_at: str
    observation_cutoff_at: str
    revision: int
    prior_revision_sha256: str | None
    provenance: Mapping[str, Any]
    record_sha256: str

    def __post_init__(self) -> None:
        effective = _time(self.effective_at, "effective_at")
        reported = _time(self.reported_at, "reported_at")
        available = _time(self.available_at, "available_at")
        retrieved = _time(self.retrieved_at, "retrieved_at")
        cutoff = _time(self.observation_cutoff_at, "observation_cutoff_at")
        if not effective <= reported == available <= retrieved:
            raise ValueError("Form 4 timestamps violate the exact PIT contract")
        if cutoff != available:
            raise ValueError("Form 4 source cutoff must equal official availability")
        if self.direction not in {"BUY", "SELL"}:
            raise ValueError("Form 4 direction is unsupported")
        if self.symbol not in ISSUER_CIKS:
            raise ValueError("Form 4 observation symbol is outside the issuer pins")
        if self.observation_id != "FORM4-" + hashlib.sha256(
            self.accession_number.encode()
        ).hexdigest()[:32].upper():
            raise ValueError("Form 4 observation identity differs from its accession")
        if (
            len(self.owner_id_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.owner_id_sha256)
        ):
            raise ValueError("Form 4 owner identity hash is invalid")
        weight = Decimal(self.role_weight)
        if not weight.is_finite() or weight <= 0:
            raise ValueError("Form 4 role weight must be finite and positive")
        if self.revision != 1 or self.prior_revision_sha256 is not None:
            raise ValueError("Form 4 v1 observations cannot invent revisions")
        if (
            self.provenance.get("quarter") not in {"2024Q4", "2025Q1"}
            or self.provenance.get("acceptance_field")
            != "filings.recent.acceptanceDateTime"
            or self.provenance.get("transaction_scope")
            != "NONDERIV_TRANS code P/S only"
        ):
            raise ValueError("Form 4 provenance contract is invalid")
        material = {name: getattr(self, name) for name in self.__dataclass_fields__ if name != "record_sha256"}
        if _sha(_canonical(material)) != self.record_sha256:
            raise ValueError("Form 4 observation hash is invalid")


class SECForm4InsiderSpecialistBot:
    """Independent P/S cluster intensity with fixed executive role weights."""

    specialist_id = "SEC_FORM4_INSIDER"
    version = SPECIALIST_VERSION

    def __init__(self, artifact: Mapping[str, Any], *, expected_sha256: str) -> None:
        material = dict(artifact)
        embedded = material.pop("artifact_sha256", None)
        if embedded != _sha(_canonical(material)) or embedded != expected_sha256:
            raise ValueError("Form 4 artifact differs from its admitted SHA-256")
        if (
            artifact.get("schema_version")
            not in {SCHEMA_VERSION, LEGACY_ADMITTED_SCHEMA_VERSION}
            or artifact.get("partition_role") != "TRAIN"
            or artifact.get("window") != {"start": TRAIN_START, "end": TRAIN_END}
            or artifact.get("symbols") != list(SYMBOLS)
            or artifact.get("reported_at_equals_available_at") is not True
            or artifact.get("validation_data_read") is not False
            or artifact.get("untouched_test_included") is not False
            or artifact.get("available_at_semantics") != "EXACT_SEC_EDGAR_ACCEPTANCE_DATETIME"
        ):
            raise ValueError("Form 4 specialist accepts only exact-PIT TRAIN evidence")
        captures = artifact.get("source_capture")
        if not isinstance(captures, dict) or set(captures) != set(OFFICIAL_SOURCE_URLS):
            raise ValueError("Form 4 artifact source capture is incomplete")
        for name, url in OFFICIAL_SOURCE_URLS.items():
            capture = captures.get(name)
            if (
                not isinstance(capture, dict)
                or capture.get("url") != url
                or not isinstance(capture.get("sha256"), str)
                or len(capture["sha256"]) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in capture["sha256"]
                )
            ):
                raise ValueError("Form 4 artifact source capture differs from its pins")
        records = tuple(Form4Observation(**row) for row in artifact.get("records", ()))
        if len({row.observation_id for row in records}) != len(records):
            raise ValueError("Form 4 artifact contains duplicate observations")
        if any(
            not TRAIN_START <= row.available_at[:10] <= TRAIN_END
            or
            row.provenance["quarter_source_sha256"]
            != captures[row.provenance["quarter"]]["sha256"]
            or row.provenance["issuer_submissions_sha256"]
            != captures[row.symbol]["sha256"]
            for row in records
        ):
            raise ValueError("Form 4 record provenance differs from source capture")
        if artifact.get("schema_version") == SCHEMA_VERSION and any(
            row.provenance.get("role_taxonomy_version") != ROLE_TAXONOMY_VERSION
            for row in records
        ):
            raise ValueError("Form 4 v2 record lacks its role-taxonomy version")
        if artifact.get("schema_version") == LEGACY_ADMITTED_SCHEMA_VERSION and any(
            "role_taxonomy_version" in row.provenance for row in records
        ):
            raise ValueError("legacy Form 4 evidence cannot claim the v2 taxonomy")
        self.artifact_sha256 = embedded
        self._records = records

    @staticmethod
    def _cluster_score(records: Sequence[Form4Observation]) -> Decimal:
        if not records:
            return Decimal("0")
        owner_directions = {(row.owner_id_sha256, row.direction) for row in records}
        buy_owners = {owner for owner, direction in owner_directions if direction == "BUY"}
        sell_owners = {owner for owner, direction in owner_directions if direction == "SELL"}
        buy_weight = sum((Decimal(row.role_weight) for row in records if row.direction == "BUY"), Decimal("0"))
        sell_weight = sum((Decimal(row.role_weight) for row in records if row.direction == "SELL"), Decimal("0"))
        buy_multiplier = min(Decimal("1.5"), Decimal("1") + Decimal("0.25") * max(0, len(buy_owners) - 1))
        sell_multiplier = min(Decimal("1.5"), Decimal("1") + Decimal("0.25") * max(0, len(sell_owners) - 1))
        with localcontext() as context:
            context.prec = 34
            intensity = buy_weight * buy_multiplier - sell_weight * sell_multiplier
            return intensity / (Decimal("1") + abs(intensity))

    def score_tick(self, symbol: str, *, decision_at: str | datetime) -> SpecialistSignal:
        resolved_symbol = str(symbol).strip().upper()
        if resolved_symbol not in SYMBOLS:
            raise ValueError("insider specialist symbol is outside the campaign basket")
        decision = _time(decision_at, "decision_at")
        if resolved_symbol not in ISSUER_CIKS:
            return SpecialistSignal(
                specialist_id=self.specialist_id,
                specialist_version=self.version,
                symbol=resolved_symbol,
                decision_at=decision.isoformat(),
                score=Decimal("0"),
                evidence_count=0,
                evidence_sha256=_sha(_canonical([])),
                reason="NO_INSIDER_COVERAGE_FOR_SYMBOL",
            )
        if decision < _time(LOOKBACK_COMPLETE_FROM, "lookback_complete_from"):
            return SpecialistSignal(
                specialist_id=self.specialist_id,
                specialist_version=self.version,
                symbol=resolved_symbol,
                decision_at=decision.isoformat(),
                score=Decimal("-1"),
                evidence_count=0,
                evidence_sha256=_sha(_canonical([])),
                reason="INSUFFICIENT_TRAILING_LOOKBACK",
            )
        lower = decision - timedelta(days=LOOKBACK_DAYS)
        evidence = tuple(
            row
            for row in self._records
            if row.symbol == resolved_symbol
            and lower < _time(row.available_at, "available_at") <= decision
            and _time(row.effective_at, "effective_at") <= decision
        )
        evidence_hash = _sha(_canonical([row.record_sha256 for row in evidence]))
        score = self._cluster_score(evidence)
        return SpecialistSignal(
            specialist_id=self.specialist_id,
            specialist_version=self.version,
            symbol=resolved_symbol,
            decision_at=decision.isoformat(),
            score=score,
            maximum_input_available_at=(
                max(row.available_at for row in evidence)
                if evidence
                else decision.isoformat()
            ),
            evidence_count=len(evidence),
            evidence_sha256=evidence_hash,
            reason="TRAILING_60_DAY_P_S_CLUSTER_ROLE_INTENSITY" if evidence else "NO_QUALIFYING_FORM4_EVIDENCE",
        )

    def score_frame(self, decisions: pd.DataFrame) -> pd.DataFrame:
        """Batch interface implemented through the authoritative tick rule."""
        required = {"symbol", "decision_at"}
        if set(decisions.columns) != required:
            raise ValueError("decision frame requires exactly symbol and decision_at")
        frame = decisions.copy()
        frame["symbol"] = frame["symbol"].astype(str).str.strip().str.upper()
        if not frame["symbol"].isin(SYMBOLS).all():
            raise ValueError("decision frame contains a symbol outside the campaign basket")
        frame["decision_at"] = pd.to_datetime(
            frame["decision_at"], utc=True, errors="raise"
        )
        frame["score"] = [
            _decimal(self.score_tick(row.symbol, decision_at=row.decision_at).score)
            for row in frame.itertuples(index=False)
        ]
        return frame[["symbol", "decision_at", "score"]]


def write_form4_artifact(path: Path, artifact: Mapping[str, Any]) -> str:
    """Write one immutable private normalized artifact."""
    payload = _canonical(dict(artifact)) + b"\n"
    try:
        return write_immutable_private_bytes(path, payload)
    except ValueError as error:
        raise ValueError(
            "Form 4 artifact conflicts with existing immutable bytes"
        ) from error


def write_immutable_private_bytes(path: Path, payload: bytes) -> str:
    """Persist content-addressed raw source bytes without following symlinks."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    try:
        stat = path.lstat()
    except FileNotFoundError:
        stat = None
    if stat is not None:
        if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
            raise ValueError("immutable raw SEC source path conflicts with existing bytes")
        return _sha(payload)
    fd = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(fd, payload[offset:])
            if written <= 0:
                raise OSError("raw SEC source write made no progress")
            offset += written
        os.fsync(fd)
    finally:
        os.close(fd)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return _sha(payload)
