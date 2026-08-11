BEGIN;

CREATE TABLE schema_migrations (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE job_runs (
    run_id text PRIMARY KEY,
    command text NOT NULL,
    git_revision text NOT NULL,
    image_revision text NOT NULL,
    state text NOT NULL CHECK (state IN ('STARTED', 'SUCCEEDED', 'FAILED', 'TIMED_OUT')),
    started_at timestamptz NOT NULL,
    heartbeat_at timestamptz NOT NULL,
    finished_at timestamptz,
    error text,
    CHECK (finished_at IS NULL OR finished_at >= started_at)
);

CREATE TABLE research_artifacts (
    artifact_id text PRIMARY KEY,
    ticker text NOT NULL,
    data_as_of timestamptz NOT NULL,
    source_versions jsonb NOT NULL,
    model_versions jsonb NOT NULL,
    object_key text NOT NULL UNIQUE,
    content_sha256 char(64) NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE portfolio_versions (
    portfolio_id text PRIMARY KEY,
    previous_portfolio_id text REFERENCES portfolio_versions(portfolio_id),
    change_type text NOT NULL CHECK (change_type IN ('CONSTRUCTION', 'REALLOCATION')),
    execution_mode text NOT NULL DEFAULT 'RECORD_ONLY'
        CHECK (execution_mode IN ('RECORD_ONLY', 'PAPER_ONLY')),
    git_revision text NOT NULL,
    model_versions jsonb NOT NULL,
    data_as_of timestamptz NOT NULL,
    decided_at timestamptz NOT NULL,
    payload jsonb NOT NULL,
    CHECK (data_as_of <= decided_at)
);

CREATE TABLE portfolio_holdings (
    portfolio_id text NOT NULL REFERENCES portfolio_versions(portfolio_id),
    ticker text NOT NULL,
    weight numeric(12, 10) NOT NULL CHECK (weight >= 0 AND weight <= 1),
    payload jsonb NOT NULL,
    PRIMARY KEY (portfolio_id, ticker)
);

CREATE TABLE ledger_state (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    record_hash char(64) NOT NULL CHECK (record_hash ~ '^[0-9a-f]{64}$'),
    record_count bigint NOT NULL CHECK (record_count >= 0)
);

INSERT INTO ledger_state (singleton, record_hash, record_count)
VALUES (true, repeat('0', 64), 0);

CREATE TABLE investment_decisions (
    sequence_number bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    decision_id text PRIMARY KEY,
    portfolio_id text NOT NULL REFERENCES portfolio_versions(portfolio_id),
    supersedes_decision_id text REFERENCES investment_decisions(decision_id),
    ticker text NOT NULL,
    decision text NOT NULL,
    execution_mode text NOT NULL CHECK (execution_mode IN ('RECORD_ONLY', 'PAPER_ONLY')),
    data_as_of timestamptz NOT NULL,
    decided_at timestamptz NOT NULL,
    payload jsonb NOT NULL,
    previous_hash char(64) NOT NULL CHECK (previous_hash ~ '^[0-9a-f]{64}$'),
    record_hash char(64) NOT NULL UNIQUE CHECK (record_hash ~ '^[0-9a-f]{64}$'),
    CHECK (data_as_of <= decided_at)
);

CREATE TABLE decision_model_versions (
    decision_id text NOT NULL REFERENCES investment_decisions(decision_id),
    component text NOT NULL,
    version text NOT NULL,
    parameters jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (decision_id, component)
);

CREATE TABLE outbox_events (
    event_id text PRIMARY KEY,
    aggregate_type text NOT NULL,
    aggregate_id text NOT NULL,
    event_type text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    delivered_at timestamptz,
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0)
);

CREATE TABLE ledger_anchors (
    anchor_id text PRIMARY KEY,
    record_hash char(64) NOT NULL CHECK (record_hash ~ '^[0-9a-f]{64}$'),
    record_count bigint NOT NULL CHECK (record_count >= 0),
    object_key text NOT NULL UNIQUE,
    anchored_at timestamptz NOT NULL DEFAULT now()
);

CREATE FUNCTION reject_historical_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'historical investment records are append-only';
END;
$$;

CREATE TRIGGER investment_decisions_are_immutable
BEFORE UPDATE OR DELETE ON investment_decisions
FOR EACH ROW EXECUTE FUNCTION reject_historical_mutation();

CREATE TRIGGER decision_versions_are_immutable
BEFORE UPDATE OR DELETE ON decision_model_versions
FOR EACH ROW EXECUTE FUNCTION reject_historical_mutation();

CREATE TRIGGER portfolio_versions_are_immutable
BEFORE UPDATE OR DELETE ON portfolio_versions
FOR EACH ROW EXECUTE FUNCTION reject_historical_mutation();

CREATE TRIGGER portfolio_holdings_are_immutable
BEFORE UPDATE OR DELETE ON portfolio_holdings
FOR EACH ROW EXECUTE FUNCTION reject_historical_mutation();

CREATE TRIGGER research_artifacts_are_immutable
BEFORE UPDATE OR DELETE ON research_artifacts
FOR EACH ROW EXECUTE FUNCTION reject_historical_mutation();

CREATE TRIGGER ledger_anchors_are_immutable
BEFORE UPDATE OR DELETE ON ledger_anchors
FOR EACH ROW EXECUTE FUNCTION reject_historical_mutation();

INSERT INTO schema_migrations (version) VALUES ('0001_initial');

COMMIT;
