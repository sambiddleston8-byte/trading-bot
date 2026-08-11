# Cost-controlled test strategy

The test suite is a safety control, not a workload to run indiscriminately.
Use the smallest relevant layer during development and increase coverage only at
promotion checkpoints.

## Development loop

Run the directly affected test module first. This gives fast feedback without
calling unrelated systems or paid AI services.

## Local checkpoint

Run all tests except those marked `live_data`:

```bash
python -m pytest -q -m "not live_data"
```

These tests must be deterministic, must use temporary paths, and must not write
to the repository's research history.

## Provider or release checkpoint

Run the complete suite, including `live_data`, only when provider integration is
changed or before a release/promotion checkpoint:

```bash
python -m pytest -q
```

A live-data failure must be classified as either a provider/network outage or a
code defect before any code is changed.

## Current assessment

- 120 deterministic local tests cover portfolio construction, risk gates,
  research contracts, evidence quality, monitoring, paper-only behavior and the
  immutable decision ledger.
- 1 live-data integration test exercises the NVDA analysis path through Yahoo
  Finance. It is useful at provider/release checkpoints but unnecessary after
  every source edit.
- No test is authorised to place an order or use real money.
- Expensive frontier models are not part of the automated test loop. They are
  reserved for design challenges, adversarial review and difficult debugging.
