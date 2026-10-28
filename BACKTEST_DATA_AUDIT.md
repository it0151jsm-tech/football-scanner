# Backtest data availability audit

Audit date: 2026-10-24 UTC. Scope: committed files in the PR #6 based pilot
worktree, plus the published documentation of the separate Draft PR #3. This is
an availability audit, not an execution of live scans or a measurement of model
skill. No app/bill/production SQLite file was opened, and no provider dataset,
Odds API response or paid service was requested.

## Result

**Qualified real, reproducible pre-match AH/OU backtest cohort: n = 0.**

This means zero joined records with the required feature/prediction/quote/timing/
result evidence were found in the audited material. It does **not** mean no real
football results exist, no source works, no app has saved picks, or that the
model has a zero win rate. Production databases were outside the audit scope.

| Audited material | What it proves | Why it is not a qualified real backtest cohort |
| --- | --- | --- |
| `reports/league-source-audit.json` | Dated source-roster/count/mapping observations for 38 configured leagues; 2026-10-20 audit | No row-level historical predictions, price snapshots or feature payloads; roster matching does not prove model readiness |
| `fixtures/team_identity_evidence.json` | Four reported API labels, three saved Understat labels, and checksums/receipt metadata for small prior E0/D1 CSV checks | Explicitly identity-label evidence, not model data; the 70/54 CSV row counts are metadata, not committed backtest rows or proof of pre-match availability |
| `fixtures/issue15_identity_evidence.json` | Access errors, limited historical name evidence and official name references | `source_payloads_observed` is 0 for its source-access batch; the historical mirror's 380-row count is explicitly identity-only, with no match rows committed or supplied to production |
| `fixtures/readiness/pr2-contract.json` | UI/control/formula regression contract and source hashes | Not a football dataset and not stored pre-match predictions |
| `tests/test_pilot_acceptance.py` | Offline acceptance of three mock matches/six teams, A available for three/B for two; cup boundary and provider calls | Explicit `synthetic_test_fixture`, real provider calls 0, live coverage counts null; cannot be counted as live outcomes or profitability evidence |
| `tests/test_dashboard.py`, `tests/test_market_recommendations.py` | Display and formula behavior for constructed profiles/Poisson means/prices | Fixtures manufacture test inputs and receipt times; using the real Poisson function does not make input observations real historical evidence |
| Other fixture/mock tests | Error handling, quarter-line settlement, freshness, grouping and data-quality invariants | Software verification only; no permission to relabel these values as real performance observations |
| Separate Draft PR #3, documented Openfootball pilot | 15 real FT results with reviewed per-game kickoff offsets: three per major league, season 2025/26 | Research observations received on 2026-10-21, after the matches; no xG/xGA, shot statistics, pre-match AH/OU quote snapshots or archived pre-match predictions; not merged/imported by this task |

The presence of prices or timestamps inside a test constructor is not evidence
that those prices existed at that instant. Likewise, a receipt/checksum for a
source file, a club name or an error message is not a receipt for a model-ready
historical match/feature/quote row.

## Separate Foundation branch: evidence boundary

Draft PR #3 currently documents head
`d4744b8ceaf3f9d3dfe8561d28d6e25fa50c59d4` on
`feat/data-foundation-lab`. Its accepted cohort is 15 original Openfootball
matches, not new price/model data. Per its published report:

- Evidence recorded locally at `2026-10-21T11:06:49+00:00`.
- Timezone revision imported at `2026-10-21T11:13:18.009786+00:00`.
- All 15 timezone identities were resolved; the original 15 quarantine rows
  remain as immutable history and are not 15 currently unresolved games.
- Data is labeled `research`; corrected kickoff UTC does not backdate actual
  ingestion or turn the cohort into observed-before-kickoff evidence.
- Real results are present, but the report explicitly says no xG/xGA, shots or
  AH/OU snapshots were created. Three matches per league are not sufficient to
  demonstrate the baseline's per-team venue-sample coverage.

References (read-only; this task does not import/merge the branch):

- [Draft PR #3](https://github.com/it0151jsm-tech/football-scanner/pull/3)
- [Timezone review](https://github.com/it0151jsm-tech/football-scanner/blob/d4744b8ceaf3f9d3dfe8561d28d6e25fa50c59d4/TIMEZONE_REVISION.md)
- [Revision report](https://github.com/it0151jsm-tech/football-scanner/blob/d4744b8ceaf3f9d3dfe8561d28d6e25fa50c59d4/reports/openfootball-timezone/revision-report.json)
- [Foundation quality report](https://github.com/it0151jsm-tech/football-scanner/blob/d4744b8ceaf3f9d3dfe8561d28d6e25fa50c59d4/reports/openfootball-timezone/quality-report.json)

## Minimum evidence for a real cohort

A qualifying row needs a consistent source/season/team/event identity, reviewed
rights and traceable original payload/checksum. For a strict pre-match replay it
also needs:

1. Timezone-aware kickoff, prediction cutoff, actual feature receipt and
   actual quote receipt. The prediction and all inputs used must precede kickoff;
   each feature/quote must be available no later than the prediction cutoff.
   Provider `last_update` alone cannot replace actual local receipt evidence.
2. The actual feature observations and model version used at that cutoff.
   Current form summaries, corrected xG downloaded later or a new prediction
   computed from retrospective data cannot be presented as an archived live
   forecast. Unknown historical availability remains unknown/research-only.
3. A real AH/OU contract: event, bookmaker identity, FT90 period, side, signed
   quarter-goal line, decimal price and source batch/time. An OU Main Line
   comparison requires the actual Over/Under pair of that line/bookmaker/batch;
   alternatives cannot silently replace the original contract.
4. A subsequently confirmed FT90 result with its own provenance. No score from
   the target match, future match or post-cutoff feature revision may enter the
   prediction inputs. Final results are labels, never features.
5. For CLV, an actual qualifying pre-kickoff closing quote for the same contract.
   If the line/side/period/bookmaker differs, or closing evidence is absent, CLV
   is unavailable. A match result or First Observed Price is not a closing quote.

Missing evidence must remain excluded with a reason; synthetic values, score-
to-xG substitution, guessed capture times and retrospective timestamp overrides
must not be used to fill the cohort. A separate research replay may be useful
only when labeled as such and never pooled with strict observed evidence.

## Metric interpretation and current report boundary

- With n=0, accuracy, Brier/log loss, calibration error, ROI, Yield and CLV are
  **not estimable**. Use null/unavailable and skip reasons, not 0%, a positive
  example result, a profit claim or a fitted calibration curve.
- AH/OU calibration of the existing three probability fields is **full win /
  partial-or-push / full loss**, not home win / draw / away win. Quarter lines
  require the unchanged settlement logic to identify half wins/losses and pushes;
  a half outcome is not a full win or a 1X2 draw. Financial evaluation must retain
  the actual five settlement outcomes/returns and the exact quote.
- Multiple prices, sides or lines for one fixture are correlated observations,
  not independent matches. Report both contract count and distinct fixture count
  and freeze the selection policy before evaluating financial performance.
- Software tests, mock acceptance and formula-parity checks remain useful but
  do not establish calibration, live forecast accuracy or profitable betting.

## What is still needed

A reviewed, rights-compatible real feature/odds/prediction/result export with
actual receipt timestamps and sufficient per-team prior samples is needed before
an honest real AH/OU baseline performance report can be produced. Importing new
provider data, inspecting user bill databases, contacting providers, spending API
credits and connecting Foundation to the running app are outside this task.
No real-data import, model/UI change, settlement change, merge or deployment is
performed by this audit.
