# Openfootball real-data pilot — 2026-10-21

This document preserves the **original quarantine-only checkpoint**. The later
[timezone revision](TIMEZONE_REVISION.md) confirms 15/15 using explicit offsets
in primary full records, while retaining all original quarantine rows unchanged.

**15 real source records retained in quarantine; 0 accepted matches; 30 exact-name
team entries.** All 15 dates have naive clock times without an evidenced timezone.
No xG, shots, odds, forecasts or accuracy claims. This is a backend-only pilot;
app/UI/CSS/mobile/four tabs, A/B/C, Poisson, EV, settlement and bill storage are
unchanged. No Odds API requests, scheduler, paid service, merge or deployment.

## Rights and source evidence

Provider: https://github.com/openfootball/football.json

Pinned commit: `e6744429ee395bc86f247348c6184bb08d4eb361`.

- [Public-domain declaration](https://github.com/openfootball/football.json/blob/e6744429ee395bc86f247348c6184bb08d4eb361/README.md#license)
  expressly covers the schema, **data**, and scripts.
- [CC0-1.0](https://github.com/openfootball/football.json/blob/e6744429ee395bc86f247348c6184bb08d4eb361/LICENSE.md)
  permits storage, research and model-development uses; no model is trained here.
- Exact UTF-8 snapshots: `fixtures/openfootball/evidence/README.md` and
  `LICENSE.md`. Their Git blob hashes match upstream metadata:
  `81a70d4a7df63ec019c77b33e98fff8b51b4196a` and
  `670154e3538863b2d9891fd5483160fbdfc89164`, respectively.
- `rights-review.json` records scope, URLs, real review time and SHA-256 checksums
  for evidence, sample and registry. A different sample needs explicit new review.
- `rights.txt` combines the full source declaration/license and references and
  is copied into `source_permissions`; backups retain it. CC0 does not guarantee
  source data accuracy. No scraper license substitutes for the data license.

The adapter has no network client. No bulk download or automatic refresh is
implemented. Only selected original records are shipped, not entire seasons.

## Actual scope

`fixtures/openfootball/sample.json` contains the first three original match rows
from each listed 2025/26 document, with source URL, commit, upstream blob
reference and row indexes. Local checksums identify these excerpts, not a
purported independently verified checksum of all full source files.

| League / source file in `2025-26/` | Raw games | Accepted | Quarantined | Teams |
| --- | ---: | ---: | ---: | ---: |
| Premier League / `en.1.json` | 3 | 0 | 3 | 6 |
| La Liga / `es.1.json` | 3 | 0 | 3 | 6 |
| Serie A / `it.1.json` | 3 | 0 | 3 | 6 |
| Bundesliga / `de.1.json` | 3 | 0 | 3 | 6 |
| Ligue 1 / `fr.1.json` | 3 | 0 | 3 | 6 |
| Total | **15** | **0** | **15** | **30** |

All rejection codes: `timezone_unverified`. Preserved source results include
Liverpool FC–AFC Bournemouth 4–2, FC Bayern München–RB Leipzig 6–0 and Genoa
CFC–US Lecce 0–0. These are **source-reported**, not independently verified
against an official league feed.

Local source snapshot recorded: `2026-10-21T10:08:28+00:00`.
Actual SQLite ingestion: `2026-10-21T10:16:09.268735+00:00`.
These are separate real events; neither is backdated to the 2025 fixtures.

## Safeguards and limitations

- Names match `teams.json` exactly, retaining accents. No fuzzy aliases or
  cross-provider merges. Openfootball supplies no team IDs here: `of-local-*`
  hashes are explicitly league-scoped internal IDs, not official/global club IDs.
  They do not automatically identify clubs across leagues or promotions.
- League/season membership is explicit. Club country is `unknown`, since these
  rows do not supply it; Monaco is not assigned France from league membership.
- Calendar, season, self-matches, source title/path, duplicates and conflicting
  results are checked. Both score arrays and `score.ft` objects are supported.
  Missing, negative, boolean, fractional and contradictory scores are rejected.
- Date-only rows receive `kickoff_time_missing`; naive clocks receive
  `timezone_unverified`. Country timezone and midnight UTC are never inferred.
  Only an explicit source offset can enter the current normalization path.
  A future verified timezone mapping needs separate review, not automatic release.
- Quarantine preserves raw row/result, source URL/commit/blob, original row index,
  checksum, reason and actual observation time. No incomplete row enters matches.
- Every historical import is `research`. Repeat import is idempotent and keeps
  the original receipt time. No xG, shots or odds are filled from missing values.
- Generic `accepted=30` counts **team entities**, not games. Pilot fields report
  `accepted_matches=0`, `quarantined_matches=15`. Generic quality counts accepted
  matches only, so its zero does not mean the source rows were discarded.

## Reproduce offline from repository root

```sh
python scripts/openfootball_pilot.py --output reports/openfootball-pilot-new-run
```

Default DB: `foundation-data/openfootball-pilot/football_foundation.db`, separate
from the fictional demo DB and every bill database. Choose a fresh output folder;
artifacts/backups are never overwritten. The lower-level initialized-DB command:

```sh
python -m foundation --db foundation-data/openfootball-pilot/football_foundation.db import-openfootball fixtures/openfootball/sample.json
```

Outputs: `import-report.json`, `quality-report.json`,
`openfootball.foundation-backup.db` and `restored/football_foundation.db`.
SQLite online backup, integrity/FK checks and comparison of **every table** pass,
including quarantine, results payloads and rights evidence. Original pilot JSON
reports are in `reports/openfootball-pilot/`; all DB files remain git-ignored.

CI uses committed licensed fixtures only, with external Python sockets denied.
Its separate real-pilot backup/restore is under `openfootball-real-pilot/` in the
existing `foundation-and-regression-reports` artifact. The artifact's root demo
DB remains fictional. CI timestamps are its actual receipt times, not the local
pilot time. Download a backup outside this workspace for durable storage.

## Still missing for AH/OU

This first-round sample is not a league census or sufficient form history.
Missing: verified kickoff timezone, enough dated team history, provider xG/xGA,
shots, actual pre-match AH/OU prices/bookmaker/line/capture time and same-contract
closing snapshots. No calibration, EV, ROI, Yield or CLV conclusion is possible.
Further collection, quarantine release, source expansion and live integration
require approval. This pilot changes no analysis formula.
