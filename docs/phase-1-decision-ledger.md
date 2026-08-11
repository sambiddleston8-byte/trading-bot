# Phase 1: decision ledger and version tracking

## Safety boundary

The ledger records investment recommendations only. Every entry has
`execution_mode: RECORD_ONLY`; the component contains no broker integration,
credentials, order type, or order-routing method. Existing paper-portfolio
behavior remains unchanged.

## Record contract

Each JSONL entry contains:

- a unique decision ID and UTC decision timestamp;
- the timestamp of the newest data available to the decision;
- ticker, decision, optional portfolio version, and the complete decision payload;
- the Git commit used to produce the decision;
- a list of component/model versions, including provider, model, prompt version,
  and relevant parameters where applicable;
- the preceding record's hash and the current record's SHA-256 hash.

The canonical JSON representation and hash chain make edits and reordering
detectable. The application verifies the entire existing chain before every
append. Local filesystem ownership can still permit deletion or truncation, so
the AWS phase should add object-lock retention or an externally stored chain
anchor for stronger immutability.

## Usage

```python
from core.decision_ledger import InvestmentDecisionLedger, ModelVersion

ledger = InvestmentDecisionLedger("data/decision_ledger.jsonl")
ledger.append(
    ticker="NVDA",
    decision="BUY",
    decision_payload={"confidence": 72, "expected_return_12m": 0.18},
    data_as_of="2026-08-11T12:00:00+00:00",
    portfolio_version="PORT-20260811-001",
    model_versions=[
        ModelVersion(component="portfolio", version="0.7"),
        ModelVersion(
            component="investment_committee",
            version="1.0",
            provider="openai",
            model="frontier-model-name",
            prompt_version="committee-v1",
        ),
    ],
)
```

Callers must provide `data_as_of`; a decision timestamp is not a substitute for
the data cutoff. AI-backed components must provide their exact provider, model,
and prompt version rather than a generic label such as `latest`.

## Branching workflow

- `main` remains releasable and receives changes through reviewed pull requests.
- Work is developed on short-lived `codex/<topic>` or `claude/<topic>` branches.
- Codex implements a bounded issue; Claude challenges the diff for correctness,
  investment-methodology risk, data leakage, and missing tests.
- The author addresses review findings and the full test suite must pass before
  merge. Neither agent autonomously merges or changes real-money settings.
- Generated research data is kept separate from source changes when staging and
  reviewing a pull request.
