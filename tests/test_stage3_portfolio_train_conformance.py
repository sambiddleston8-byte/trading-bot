from datetime import time
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import pytest

from core.features.pit_feature_contract import (
    build_technical_feature_matrices,
    campaign_observation_cutoff,
)
from core.orchestration.stage2_qualification import (
    _at,
    _bar_available,
    _bar_close,
    _sessions,
)
from core.research.sec_form4_insider_specialist import (
    OFFICIAL_SOURCE_URLS,
    SCHEMA_VERSION as FORM4_SCHEMA_VERSION,
)

from core.research.stage3_portfolio_train_conformance import (
    STATUS,
    evaluate_train_portfolio_conformance,
)
from core.research.stage4_train_insider_ensemble_evaluation import FORM4_ARTIFACT
from core.research.fundamental_valuation_specialist import build_fundamental_artifact
from core.research.stage4_train_fundamental_evaluation import (
    FUNDAMENTAL_ARTIFACT,
    STATUS as FUNDAMENTAL_STATUS,
    evaluate_train_fundamental_ablation,
)
from core.research.catalyst_event_specialist import build_catalyst_event_artifact
from core.research.stage4_train_catalyst_evaluation import (
    CATALYST_ARTIFACT,
    STATUS as CATALYST_STATUS,
    evaluate_train_catalyst_ablation,
)
from core.research.political_disclosure_specialist import (
    build_political_disclosure_artifact,
)
from core.research.stage4_train_political_evaluation import (
    POLITICAL_ARTIFACT,
    STATUS as POLITICAL_STATUS,
    evaluate_train_political_ablation,
)
from core.research.macro_cross_asset_specialist import (
    FACTOR_NAMES,
    build_macro_cross_asset_artifact,
)
from core.research.stage4_train_macro_evaluation import (
    MACRO_ARTIFACT,
    STATUS as MACRO_STATUS,
    evaluate_train_macro_ablation,
)


ROOT = "data/research/massive_campaign_v2_revision_2"
RETRIEVED_AT = "2026-08-16T20:00:00+00:00"


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _sha(payload):
    return hashlib.sha256(payload).hexdigest()


def _synthetic_admitted_train(repository_root):
    stage2 = repository_root / ROOT / "stage2"
    store = stage2 / "clean_feature_store"
    store.mkdir(parents=True)
    artifacts = {}
    cutoffs = {}
    for role, start, end in (
        ("TRAIN", "2024-10-01", "2025-02-28"),
        ("VALIDATION", "2025-03-01", "2025-04-30"),
    ):
        bars = []
        cutoffs[role] = {}
        for index, day in enumerate(_sessions(start, end)):
            cutoffs[role][day] = campaign_observation_cutoff(day)
            for offset, symbol in enumerate(("AAPL", "MSFT", "SPY")):
                base = Decimal(100 + index + offset)
                bars.append({
                    "symbol": symbol,
                    "session_date": day,
                    "open_at": _at(day, time(9, 30)),
                    "close_at": _at(day, _bar_close(day)),
                    "available_at": _bar_available(day),
                    "open": str(base),
                    "high": str(base + Decimal("2")),
                    "low": str(base - Decimal("1")),
                    "close": str(base + Decimal("1")),
                    "volume": "100000",
                    "source_payload_sha256": _sha(
                        f"{role}:{day}:{symbol}".encode()
                    ),
                })
        value = {
            "schema_version": "1.0",
            "role": role,
            "bars": bars,
            "corporate_actions": [],
            "quarantine_only": False,
            "clean_feature_store": True,
        }
        payload = _canonical(value) + b"\n"
        (store / f"{role.lower()}.json").write_bytes(payload)
        artifacts[role] = _sha(payload)
    qualification = {"artifacts": artifacts}
    qualification["qualification_sha256"] = _sha(_canonical(qualification))
    qualification_bytes = _canonical(qualification) + b"\n"
    (stage2 / "qualification_report.json").write_bytes(qualification_bytes)
    build_technical_feature_matrices(
        repository_root,
        retrieved_at=RETRIEVED_AT,
        observation_cutoffs=cutoffs,
        qualification_report_artifact_sha256=_sha(qualification_bytes),
    )
    matrix_path = repository_root / ROOT / "stage3/technical_features/train_matrix.json"
    matrix_sha256 = json.loads(matrix_path.read_text())["matrix_sha256"]

    form4 = {
        "schema_version": FORM4_SCHEMA_VERSION,
        "partition_role": "TRAIN",
        "window": {"start": "2024-10-01", "end": "2025-02-28"},
        "symbols": ["AAPL", "MSFT", "SPY"],
        "source_capture": {
            name: {"url": url, "sha256": _sha(name.encode())}
            for name, url in OFFICIAL_SOURCE_URLS.items()
        },
        "records": [],
        "reported_at_equals_available_at": True,
        "available_at_semantics": "EXACT_SEC_EDGAR_ACCEPTANCE_DATETIME",
        "validation_data_read": False,
        "untouched_test_included": False,
    }
    form4["artifact_sha256"] = _sha(_canonical(form4))
    form4_path = repository_root / FORM4_ARTIFACT
    form4_path.parent.mkdir(parents=True, exist_ok=True)
    form4_path.write_bytes(_canonical(form4) + b"\n")

    (store / "validation.json").unlink()
    (repository_root / ROOT / "stage3/technical_features/validation_matrix.json").unlink()
    sealed_test = store / "test.json"
    sealed_test.write_text("sealed TEST must not be read")
    return matrix_sha256, form4["artifact_sha256"], sealed_test


def test_train_portfolio_conformance_is_bounded_and_deterministic(tmp_path):
    matrix_sha256, form4_sha256, sealed_test = _synthetic_admitted_train(tmp_path)
    report = evaluate_train_portfolio_conformance(
        tmp_path,
        admitted_train_matrix_sha256=matrix_sha256,
        expected_form4_artifact_sha256=form4_sha256,
        write_output=False,
    )
    assert report["status"] == STATUS
    assert report["partition_role"] == "TRAIN"
    assert report["portfolio_wide_batching_complete"] is True
    assert report["shared_cash_reservation_complete"] is True
    assert report["cross_symbol_order_conformance_complete"] is True
    assert report["validation_data_read"] is False
    assert report["untouched_test_included"] is False
    assert report["promotion_allowed"] is False
    assert all(
        scenario["input_order_conformance"] is True
        for scenario in report["scenarios"].values()
    )
    repeated = evaluate_train_portfolio_conformance(
        tmp_path,
        admitted_train_matrix_sha256=matrix_sha256,
        expected_form4_artifact_sha256=form4_sha256,
        write_output=False,
    )
    assert repeated["report_sha256"] == report["report_sha256"]
    assert sealed_test.read_text() == "sealed TEST must not be read"


def test_admitted_train_conformance_matches_its_sealed_report_hash():
    repository_root = Path.cwd()
    required = (
        repository_root / ROOT / "stage2/clean_feature_store/train.json",
        repository_root / ROOT / "stage3/technical_features/train_matrix.json",
        repository_root / FORM4_ARTIFACT,
    )
    if not all(path.exists() for path in required):
        pytest.skip("private admitted TRAIN artifacts are not present")
    report = evaluate_train_portfolio_conformance(
        repository_root, write_output=False
    )
    assert report["report_sha256"] == (
        "84b60115a60691aa1fea0ac938b5f0cb28b27c718632409f2f884e872914b442"
    )
    assert set(report["scenarios"]) == {"BASE", "PESSIMISTIC"}
    assert all(
        scenario["intent_trace_count"] == 159
        and scenario["execution_count"] == 0
        for scenario in report["scenarios"].values()
    )


def test_synthetic_fundamental_ablation_runs_both_cost_models_and_stays_research_only(tmp_path):
    matrix_sha256, form4_sha256, sealed_test = _synthetic_admitted_train(tmp_path)
    rows = []
    for symbol, dispersion in (("AAPL", "1"), ("MSFT", "0.5"), ("SPY", "-0.5")):
        rows.append({
            "symbol": symbol,
            "fiscal_period": "2024Q3",
            "effective_at": "2024-09-28T00:00:00+00:00",
            "reported_at": "2024-11-01T21:00:00+00:00",
            "available_at": "2024-11-01T21:00:00+00:00",
            "revision": 1,
            "metrics": {
                "earnings_yield": "0.07",
                "fcf_yield": "0.06",
                "roic": "0.18",
                "estimate_revision": "0.04",
                "valuation_dispersion": dispersion,
            },
            "source_payload_sha256": _sha(f"fundamental:{symbol}".encode()),
            "source_locator": f"synthetic://fundamental/{symbol}/2024Q3",
        })
    fundamental = build_fundamental_artifact(
        rows, retrieved_at="2025-03-01T00:00:00+00:00"
    )
    path = tmp_path / FUNDAMENTAL_ARTIFACT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(fundamental) + b"\n")
    report = evaluate_train_fundamental_ablation(
        tmp_path,
        admitted_train_matrix_sha256=matrix_sha256,
        expected_form4_artifact_sha256=form4_sha256,
        expected_fundamental_artifact_sha256=fundamental["artifact_sha256"],
    )
    assert report["status"] == FUNDAMENTAL_STATUS
    assert set(report["policies"]) == {
        "TECHNICAL_INSIDER_BASELINE",
        "TECHNICAL_INSIDER_FUNDAMENTAL",
    }
    assert all(
        set(policy["aggregate"]) == {"BASE", "PESSIMISTIC"}
        for policy in report["policies"].values()
    )
    assert report["evaluation_metadata"]["registration_decision"] == "RESEARCH_ONLY"
    assert report["promotion_allowed"] is False
    assert sealed_test.read_text() == "sealed TEST must not be read"


def test_synthetic_catalyst_ablation_runs_both_cost_models_and_stays_research_only(tmp_path):
    matrix_sha256, form4_sha256, sealed_test = _synthetic_admitted_train(tmp_path)
    rows = []
    for symbol, impact in (("AAPL", "0.8"), ("MSFT", "0.4"), ("SPY", "-0.3")):
        rows.append({
            "symbol": symbol,
            "event_id": f"{symbol}-SYNTHETIC-Q3",
            "event_type": "EARNINGS_RESULT",
            "effective_at": "2024-10-31T20:00:00+00:00",
            "reported_at": "2024-11-01T21:00:00+00:00",
            "available_at": "2024-11-01T21:00:00+00:00",
            "revision": 1,
            "directional_impact": impact,
            "confidence": "0.75",
            "source_payload_sha256": _sha(f"catalyst:{symbol}".encode()),
            "source_locator": f"synthetic://catalyst/{symbol}/2024Q3",
        })
    catalyst = build_catalyst_event_artifact(rows, retrieved_at="2025-03-01T00:00:00+00:00")
    path = tmp_path / CATALYST_ARTIFACT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(catalyst) + b"\n")
    report = evaluate_train_catalyst_ablation(
        tmp_path,
        admitted_train_matrix_sha256=matrix_sha256,
        expected_form4_artifact_sha256=form4_sha256,
        expected_catalyst_artifact_sha256=catalyst["artifact_sha256"],
    )
    assert report["status"] == CATALYST_STATUS
    assert set(report["policies"]) == {
        "TECHNICAL_INSIDER_BASELINE", "TECHNICAL_INSIDER_CATALYST",
    }
    assert all(set(policy["aggregate"]) == {"BASE", "PESSIMISTIC"} for policy in report["policies"].values())
    metadata = report["evaluation_metadata"]
    assert metadata["registration_decision"] == "RESEARCH_ONLY"
    assert metadata["fixture_limitation"] == "AAPL/MSFT/SPY is short, narrow, and non-promotable"
    assert report["promotion_allowed"] is False
    assert sealed_test.read_text() == "sealed TEST must not be read"


def test_synthetic_political_ablation_runs_both_cost_models_and_stays_research_only(tmp_path):
    matrix_sha256, form4_sha256, sealed_test = _synthetic_admitted_train(tmp_path)
    rows = []
    for symbol, transaction_type in (("AAPL", "PURCHASE"), ("MSFT", "PURCHASE"), ("SPY", "SALE")):
        key = f"{symbol}-SYNTHETIC-DISCLOSURE"
        rows.append({
            "symbol": symbol,
            "transaction_key": key,
            "disclosure_id": f"DISC-{key}-1",
            "source": "OFFICIAL_HOUSE",
            "effective_at": "2024-10-01T00:00:00+00:00",
            "reported_at": "2024-10-20T14:00:00+00:00",
            "available_at": "2024-11-01T14:00:00+00:00",
            "revision": 1,
            "transaction_type": transaction_type,
            "amount_min_usd": "10000",
            "amount_max_usd": "10000",
            "raw_document_sha256": _sha(f"political:raw:{symbol}".encode()),
            "availability_evidence_sha256": _sha(f"political:available:{symbol}".encode()),
            "source_locator": f"synthetic://political/{symbol}/1",
        })
    political = build_political_disclosure_artifact(
        rows, retrieved_at="2025-03-01T00:00:00+00:00"
    )
    path = tmp_path / POLITICAL_ARTIFACT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(political) + b"\n")
    report = evaluate_train_political_ablation(
        tmp_path,
        admitted_train_matrix_sha256=matrix_sha256,
        expected_form4_artifact_sha256=form4_sha256,
        expected_political_artifact_sha256=political["artifact_sha256"],
    )
    assert report["status"] == POLITICAL_STATUS
    assert set(report["policies"]) == {
        "TECHNICAL_INSIDER_BASELINE", "TECHNICAL_INSIDER_POLITICAL",
    }
    assert all(
        set(policy["aggregate"]) == {"BASE", "PESSIMISTIC"}
        for policy in report["policies"].values()
    )
    metadata = report["evaluation_metadata"]
    assert metadata["publication_delay_enforced"] is True
    assert metadata["copy_trade_allowed"] is False
    assert metadata["registration_decision"] == "RESEARCH_ONLY"
    assert metadata["fixture_limitation"] == "AAPL/MSFT/SPY is short, narrow, and non-promotable"
    assert report["promotion_allowed"] is False
    assert sealed_test.read_text() == "sealed TEST must not be read"


def test_synthetic_macro_ablation_runs_both_cost_models_and_stays_research_only(tmp_path):
    matrix_sha256, form4_sha256, sealed_test = _synthetic_admitted_train(tmp_path)
    rows = []
    for symbol, sensitivity in (("AAPL", "1"), ("MSFT", "0.5"), ("SPY", "-0.5")):
        snapshot_id = f"{symbol}-SYNTHETIC-MACRO"
        rows.append({
            "symbol": symbol,
            "snapshot_id": snapshot_id,
            "effective_at": "2024-10-01T00:00:00+00:00",
            "reported_at": "2024-11-01T14:00:00+00:00",
            "available_at": "2024-11-01T14:00:00+00:00",
            "revision": 1,
            "factors": {name: "0.8" for name in FACTOR_NAMES},
            "symbol_sensitivities": {name: sensitivity for name in FACTOR_NAMES},
            "series_payload_sha256": {
                name: _sha(f"macro:{symbol}:{name}".encode())
                for name in FACTOR_NAMES
            },
            "source_locator": f"synthetic://macro/{symbol}/2024-10",
        })
    macro = build_macro_cross_asset_artifact(
        rows, retrieved_at="2025-03-01T00:00:00+00:00"
    )
    path = tmp_path / MACRO_ARTIFACT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(macro) + b"\n")
    report = evaluate_train_macro_ablation(
        tmp_path,
        admitted_train_matrix_sha256=matrix_sha256,
        expected_form4_artifact_sha256=form4_sha256,
        expected_macro_artifact_sha256=macro["artifact_sha256"],
    )
    assert report["status"] == MACRO_STATUS
    assert set(report["policies"]) == {
        "TECHNICAL_INSIDER_BASELINE", "TECHNICAL_INSIDER_MACRO",
    }
    assert all(
        set(policy["aggregate"]) == {"BASE", "PESSIMISTIC"}
        for policy in report["policies"].values()
    )
    metadata = report["evaluation_metadata"]
    assert metadata["macro_alpha_only"] is True
    assert metadata["macro_risk_authority"] is False
    assert metadata["registration_decision"] == "RESEARCH_ONLY"
    assert metadata["fixture_limitation"] == "AAPL/MSFT/SPY is short, narrow, and non-promotable"
    assert report["promotion_allowed"] is False
    assert sealed_test.read_text() == "sealed TEST must not be read"
