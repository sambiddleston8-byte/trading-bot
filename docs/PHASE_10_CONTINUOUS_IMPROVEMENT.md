# Phase 10 controlled continuous improvement

The Phase 10 audit found that the Phase 6 evidence chain implemented hypothesis,
preregistered experiment, future out-of-sample validation, shadow evidence,
review and human-gated promotion packaging, but did not yet contain the Master
Roadmap's explicit multidimensional robustness gate.

Issue #137 adds an immutable plan after a passing out-of-sample result and before
shadow eligibility. It requires at least two preregistered slices for each of:
historical periods, sectors and market regimes. The passing fraction is fixed in
advance. Every slice requires transaction costs, point-in-time data, a
survivorship-safe universe and no leakage.

Planning executes nothing and cannot grant shadow eligibility, promote, change
production rules, deploy or trade. A separate result ledger must evaluate every
preregistered slice before the strategy registry can advance a candidate.

Issue #139 implements that enforcement. Every planned slice requires a pinned
evidence hash and affirmative proof that transaction costs, point-in-time data,
a survivorship-safe universe and leakage checks were used. The preregistered
pass fraction must be met and every dimension must contain passing evidence.
Each slice must use a distinct evidence artifact, preventing one favourable
backtest from masquerading as multiple periods, sectors or regimes.

The strategy registry no longer accepts a plain out-of-sample result. It accepts
only a verified robustness result; failures are rejected and passes are merely
eligible for the separately preregistered shadow stage. This closes the bypass
between historical validation and forward paper evidence.

## Disposable experiment storage

Issue #141 adds an executable local data-sandbox control. An experiment
workspace can be created only inside a non-symlink directory bearing the exact
sandbox-root safety marker. It receives a private manifest and isolated SQLite
database. The container runner now consumes only a verified preregistered run
manifest and a directly-contained sealed input under its own exact safety
marker. The image digest, input hash, trial count, resources and timeout must
match that manifest. Docker runs without networking, Linux capabilities or
privilege escalation, on a read-only filesystem as an unprivileged user, with
bounded memory, CPU, processes and temporary storage. The experiment receives
no production or workspace write mount; the host validates a one-MiB JSON
result and writes it to the disposable SQLite database. An immutable attempt is
reserved before execution and cannot be repeated after success or failure,
preventing optional reruns and result shopping. Capturing a result cannot
promote, deploy, submit an order or enable trading.
Each workspace retains a maximum 30-day lifetime and immutable denials for
network access, authoritative writes, AWS, brokers, promotion and trading.

Expiry removes only the two exact managed files and their direct child folder.
Early deletion, path traversal, symlinks, tampered manifests and unexpected
files all fail closed. This code is tested only in temporary directories; no
repository data or current research output is copied or removed.
