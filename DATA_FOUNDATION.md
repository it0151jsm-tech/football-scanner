# Value Bet Pro — Data Foundation experimental lab

This is a backend-only experiment based on PR #2 commit
`fac7ea0d389e72c171ae0fd154d1870cc4ec8b56`. It is **not connected to Streamlit**.
`app.py`, all dashboard/presentation files, A/B/C, Poisson/EV and settlement are
byte-for-byte unchanged. No production/bill database is read or migrated.

## What exists

- A separate, marked `football_foundation.db`, created only by the explicit CLI.
- Five competition keys: EPL, La Liga, Serie A, Bundesliga and Ligue 1.
- Local JSON import, rights-evidence snapshot, immutable provenance and actual
  first ingestion timestamps. No download command, network client or scheduler.
- Team identity/category and explicit competition-season membership checks,
  quarter-line/price/period validation, duplicate detection and quarantined rows.
- Offline replay of the unchanged xG estimator, Poisson, EV and settlement.
- Quality, sample, research and observed-mode JSON reports; backup/restore checks.

The original CC0 demonstration dataset is **fictional test
input**: 20 fictional teams, 65 matches, 60 xG observations, 60 quotes, five
competitions and five target fixtures. It does not establish actual league
coverage, model accuracy, calibration, CLV or profitability. The 60 evaluated
contracts are correlated observations on only five fictional target fixtures.

An approved Openfootball pilot now ships 15 real CC0 source excerpts from
2025/26, three per main league. All 15 are quarantined because clock times lack
a verified timezone; accepted real matches remain zero. It uses its own database,
provenance, exact-name registry and backup/restore reports. See
[OPENFOOTBALL_PILOT.md](OPENFOOTBALL_PILOT.md); these excerpts are not model-ready
history and do not change the fictional demonstration above.

## Run locally (from repository root)

Use the project's existing requirements. These commands do not start/import the
Streamlit application and require no API key:

```sh
python -m foundation init
python -m foundation import fixtures/foundation/demo.json
python -m foundation quality
python -m foundation validate --mode sample
python -m foundation validate --mode research
python -m foundation validate --mode observed
python -m foundation backup foundation-data/demo.foundation-backup.db
python -m foundation --db foundation-data/restored/football_foundation.db restore foundation-data/demo.foundation-backup.db
```

Default database: `foundation-data/football_foundation.db`. The database and its
journals are ignored by git. Reimporting an identical batch is idempotent and
preserves the actual first-observation timestamp. An initialized database can be
reopened, but `init` will never turn an unrelated existing SQLite file into a
foundation database.

Every command checks the application marker/schema before obtaining a writable
connection; legacy `picks`/`slips` tables and the configured `VALUE_BET_DB` path
are refused. Backup uses SQLite's online backup API, checks integrity/foreign
keys and compares **all tables** after restore. Backup/restore refuses to
overwrite any existing file. A backup filename must end `.foundation-backup.db`;
a restored/new database must be named `football_foundation.db`. Restore to a
new directory, review it, and do not replace an app database.

This local file is not durable hosting by itself. Store backups outside the
machine before any real-data experiment. No server or backup service has been
rented, and nothing is deployed.

## Schema and lineage

`foundation/schema.sql` defines:

| Tables | Meaning |
| --- | --- |
| `sources`, `source_permissions` | Versioned source manifest, permitted uses, license-evidence text/hash, review declaration and actual registration time |
| `ingestion_runs` | Full accepted local input batch, checksum, real ingestion time and accepted/quarantine counts |
| `teams`, `team_season_memberships` | Source-specific team IDs, names, country/category and explicitly supplied league-season membership |
| `matches` | Source match IDs, competition, season, UTC kickoff, teams and first observation |
| `observations` | Append-only match-data revisions linked to input batch, source and actual observation time |
| `team_stats`, `xg_stats`, `match_results` | Per-observation stats, xG method/period, final FT90 score; missing fields remain absent |
| `odds_snapshots` | Immutable quote IDs, bookmaker, contract, quarter-goal integer, source capture time and actual local observation time |
| `quarantine` | Rejected row, reason, batch, source row ID and actual recording time |
| `model_predictions`, `validation_runs` | Baseline hash, replay mode/time, feature-observation IDs, exact quote/result/closing-quote provenance and report |

Quality/source/time on statistic/result rows is inherited through the mandatory
observation foreign key. No goals-to-xG substitution is performed. `team_stats`
stores only supplied stats; it does not manufacture shots, possession or zeros.

Source IDs stay separate across providers. There is no fuzzy or automatic
cross-provider merge, nor an auto-approved alias list. Same provider team IDs
with conflicting identity are quarantined, as are their dependent matches and
quotes in that batch. Membership is explicit, not inferred from geography:
cross-border clubs are possible, and promoted teams need their new season's
membership declared without changing club identity. Team names, when supplied
on a match, must match the source team's name exactly.

All five configured leagues currently use an explicit `YYYY-YY` season with a
conservative July-to-June boundary. This is an adapter rule, not a universal
football calendar. Reschedules or conflicting match IDs require review; this
phase does not silently merge or rewrite them. A new observation preserves the
old result/xG rather than replacing it. Automatic promotion of quarantined rows
and cross-provider entity resolution are future work.

## Import rights and local input contract

See `fixtures/foundation/demo.json` for the exact JSON shape. A manifest must
identify the source/version, `test_sample` or `research`, a license, a local
UTF-8 evidence file and its SHA-256, a reviewer/time and explicit allowed uses
`storage`, `research`, `model_training`. The evidence is copied into the database
so restore retains it. Unknown/unverified permissions, future review dates and
missing/mismatched evidence reject the entire import before storing the payload.

**`verified` is a supplied rights-review declaration, not legal permission
obtained or independently authenticated by this program.** The authored CC0
fixture and pinned Openfootball CC0 pilot are reviewed/shipped here.
Other third-party input needs a genuine license
review first. Permission for display/redistribution would need separate review
before any app integration; this lab does not expose raw datasets publicly.

Current provider decisions are unchanged:

- Football-Data: no automatic CSV downloads in this experiment. Its published
  personal/research and light-download restrictions do not grant blanket rights
  for a commercial/training pipeline. Existing live-audit scripts remain dormant.
- Understat: no downloads or assumed rights; automated collection/training rights
  remain unverified for this use case.
- The Odds API: no requests, account changes, historical-data purchases or key
  use. No existing API snapshots/bills are accessed by the lab.

References reviewed during the plan: https://football-data.co.uk/contact.php,
https://understat.com/, https://the-odds-api.com/terms-and-conditions.html.
An open-source scraper's license does not itself license upstream data.

Unknown payload fields such as `available_at`, `observed_at` or `ingested_at`
cannot override the clock. Optional `source_updated_at` is retained as source
metadata and **never backdates ingestion**. Rows claiming future source updates,
post-match data on future games, invalid dates/seasons, wrong membership,
unsupported periods, invalid xG/stats or in-play/future quotes are quarantined.
One bad optional field rolls the entire row back through a savepoint.

## Three validation modes

The CLI reads only pinned pure functions from `app.py` through AST. It does not
execute module-level code (`init_db`, Streamlit or API fetching). A manifest pins
all app/model/UI file hashes and the extracted function hash to PR #2. Any drift
fails closed and needs explicit review; the lab never patches a formula.

- `sample`: authored `test_sample` rows only. Software demonstration metrics,
  not real accuracy. Research rows cannot enter these metrics.
- `research`: rights-attested `research` rows only; reconstructed historical
  inputs, **not proof they were available at prediction time**. Samples cannot
  enter these metrics. There are no qualifying real records shipped, so the
  initial report has zero evaluations and null accuracy/ROI values.
- `observed` (default): samples excluded; match, quote and every feature revision
  must actually have been ingested before the replay cutoff. Historical rows
  imported now are excluded from earlier cutoffs, even when the source update is
  older. This is an observation-gated replay, not an archived live forecast.

The fixed experimental cutoff is one hour before kickoff. Features exclude the
entire target UTC day (matching the existing estimator's conservative date-only
rule) and the target match; data must also precede prediction time. League
averages, venue samples and the 120-day freshness rule remain the unchanged
baseline. Inputs with inconsistent xG methods abstain instead of being pooled.
Labels use final FT90 results observed by evaluation time, never as features.

Poisson/HDA scoring can use qualified result/xG fixtures even without a price;
EV/ROI requires an eligible actual quote and does not invent one. Each report
includes separate Poisson fixture counts and per-league and AH/OU counts, skip reasons, five-outcome log
loss and full-win calibration bins/Brier score, HDA Brier, and baseline 12+ score
bucket log loss. It records every input observation, standalone Poisson prediction and evaluated
contract. With multiple eligible source versions, use `--source SOURCE_ID`; the
validator refuses to pool providers and count the same real fixture twice.
The offline financial illustration has a **fixed separate policy**: positive
baseline EV, max one bet per fixture/market, highest EV, exact EV tie abstains,
flat one-unit stake. It is not a replacement for live A/C ranking. ROI and Yield
use the same total-stake denominator and are not independent indicators.

CLV is an explicitly labeled decimal-odds ratio using the same bookmaker,
market, side, line and FT90 period, with a snapshot in the final five minutes
before kickoff. Observed mode additionally requires actual pre-kickoff local
receipt. Different lines are never substituted. It is not de-vig/probability
CLV; missing same-contract closing snapshots produce null, not invented values.

`calibrated` and `accuracy_claim_permitted` remain false. No calibrator is fitted,
no random split/tuning is performed, and no profitability confidence interval
is claimed. Fixtures have correlated quotes and the report exposes independent
fixture counts. Future train/validation/test seasonal folds, independent final
holdout, bootstrap intervals and preregistered sample sufficiency thresholds
require enough licensed, dated real observations first.

## Offline CI and artifacts

```sh
python scripts/run_offline_tests.py
python scripts/foundation_smoke.py
```

Live E2/all-league audit steps have been removed from CI on this experimental
branch. Existing fixture/mocked tests remain; the runner denies external Python
socket connections (local browser control only). Dependency/browser installation
still uses the network; football/Odds APIs do not. There is no cron workflow.

The `foundation-and-regression-reports` artifact contains existing dashboard
regression previews plus `foundation-quality.json`, `foundation-sample.json`,
`foundation-research.json`, `foundation-observed.json`, `foundation-smoke.json`
and a **fictional sample-only** `football_foundation.db`. The exported database
comes from a fresh temporary fixture import, never a user/app database.

## Before the next phase

Still missing: licensed real match/xG datasets with independently checked team
identities/coverage; actual source reception history for prospective evaluation;
real pre-match and same-contract closing odds; enough independent fixtures for
calibration/ROI/CLV conclusions. Audit counts from PR #2 are not model-ready
training data and have not been imported.

Approval is needed before new source collection, automatic scheduling, paid
services/hosting, production integration, any bill storage change, model changes
or UI changes. This phase does none of those. Main and PR #2 remain separate;
no merge or deployment is performed.
