# Openfootball timezone revision — original 15 fixtures only

Result: **15 confirmed and imported, 0 unresolved**. All **15 original quarantine
rows remain unchanged**, including original raw scores, reasons and observation
clocks. Historical quarantine count is not the unresolved-game count.

## What the time fields mean here

The simple JSON records have bare clock strings. The pinned README documents a
05:00 UTC *update job*, not a universal kickoff timezone. The Football.TXT
specification's simple example also has no offset. No global timezone contract
for those bare strings was established, and none is assumed by this adapter.

The provider's **full match records** explicitly include `UTC+0`, `UTC+1` or
`UTC+2` per fixture. These primary records resolve the original ambiguity without
inferring a zone from country, league or stadium. Examples: Liverpool–Bournemouth
is `19:00 UTC+0`, Aston Villa–Newcastle is `12:30 UTC+1`, Sassuolo–Napoli is
`16:30 UTC+0`, and Genoa–Lecce is `18:30 UTC+2`. A single offset for a league would
be wrong. Normalization uses only each literal offset, never a seasonal guess.

**Verification level: primary-provider explicit-offset evidence.** Openfootball
country repositories are NOT counted as independent providers. No independent
third-party confirmation is claimed. The primary full records themselves supply
the missing offsets, so no external-source substitute was needed. This is not a
claim that every bare Openfootball `time` field always uses the same convention.

Primary references: the [simple format example](https://openfootball.github.io/spec/)
and the pinned match-specific links below. All evidence concerns only the original
15 games in 2025/26, not new games or a full-season census.

## Rights, provenance and joins

`fixtures/openfootball/timezone/collection.json` contains only selected literal
match excerpts and source titles, pinned repository/commit/path/blob references,
line numbers, source URLs, per-excerpt SHA-256, derived UTC and actual local
receipt time. Excerpt checksums do not pretend to verify whole source-file bytes.

`upstream-rights.json` preserves the new source repositories' README public-domain
declarations and CC0 LICENSE texts, pinned URLs and matching Git blob hashes.
The original football.json CC0 review remains unchanged. Rights were checked
before use; no scraper license or public accessibility substitutes for data rights.

`identity-map.json` contains reviewed exact name correspondences. Every accepted
record also matches league, season, calendar date and FT score. No fuzzy matching,
suffix stripping, inferred country timezone or stadium lookup. `review.json`
checksums all three files. All evidence remains in the revision ingestion batch,
including rights snapshots and exact original quarantine row IDs/checksums.

## Confirmed per-game UTC

| Original teams | Confirmed UTC | Literal primary-source clock and link |
| --- | --- | --- |
| Liverpool FC — AFC Bournemouth | 2025-08-15 19:00:00 UTC | [19:00 UTC+0](https://github.com/openfootball/football.json/blob/e6744429ee395bc86f247348c6184bb08d4eb361/2025-26/en.1-full.json#L3) |
| Aston Villa FC — Newcastle United FC | 2025-08-16 11:30:00 UTC | [12:30 UTC+1](https://github.com/openfootball/football.json/blob/e6744429ee395bc86f247348c6184bb08d4eb361/2025-26/en.1-full.json#L78) |
| Brighton & Hove Albion FC — Fulham FC | 2025-08-16 14:00:00 UTC | [15:00 UTC+1](https://github.com/openfootball/football.json/blob/e6744429ee395bc86f247348c6184bb08d4eb361/2025-26/en.1-full.json#L192) |
| Girona FC — Rayo Vallecano de Madrid | 2025-08-15 17:00:00 UTC | [19:00 UTC+2](https://github.com/openfootball/espana/blob/80035a93dd4920985af52aa097a54a6fb888563b/2025-26/1-liga-full.txt#L8) |
| Villarreal CF — Real Oviedo | 2025-08-15 19:30:00 UTC | [21:30 UTC+2](https://github.com/openfootball/espana/blob/80035a93dd4920985af52aa097a54a6fb888563b/2025-26/1-liga-full.txt#L28) |
| RCD Mallorca — FC Barcelona | 2025-08-16 17:30:00 UTC | [19:30 UTC+2](https://github.com/openfootball/espana/blob/80035a93dd4920985af52aa097a54a6fb888563b/2025-26/1-liga-full.txt#L48) |
| Genoa CFC — US Lecce | 2025-08-23 16:30:00 UTC | [18:30 UTC+2](https://github.com/openfootball/italy/blob/48954d163c312aa705efacb64f771267031fe6eb/2025-26/1-seriea-full.txt#L28) |
| US Sassuolo Calcio — SSC Napoli | 2025-08-23 16:30:00 UTC | [16:30 UTC+0](https://github.com/openfootball/italy/blob/48954d163c312aa705efacb64f771267031fe6eb/2025-26/1-seriea-full.txt#L8) |
| AC Milan — US Cremonese | 2025-08-23 18:45:00 UTC | [20:45 UTC+2](https://github.com/openfootball/italy/blob/48954d163c312aa705efacb64f771267031fe6eb/2025-26/1-seriea-full.txt#L66) |
| FC Bayern München — RB Leipzig | 2025-08-22 18:30:00 UTC | [20:30 UTC+2](https://github.com/openfootball/football.json/blob/e6744429ee395bc86f247348c6184bb08d4eb361/2025-26/de.1-full.json#L3) |
| Eintracht Frankfurt — SV Werder Bremen | 2025-08-23 13:30:00 UTC | [15:30 UTC+2](https://github.com/openfootball/football.json/blob/e6744429ee395bc86f247348c6184bb08d4eb361/2025-26/de.1-full.json#L156) |
| Bayer 04 Leverkusen — TSG 1899 Hoffenheim | 2025-08-23 13:30:00 UTC | [15:30 UTC+2](https://github.com/openfootball/football.json/blob/e6744429ee395bc86f247348c6184bb08d4eb361/2025-26/de.1-full.json#L300) |
| Stade Rennais FC 1901 — Olympique de Marseille | 2025-08-15 18:45:00 UTC | [20:45 UTC+2](https://github.com/openfootball/europe/blob/63df627f3c87ce71708f016522ea236cf74332c3/france/2025-26_fr1-full.txt#L8) |
| Racing Club de Lens — Olympique Lyonnais | 2025-08-16 15:00:00 UTC | [17:00 UTC+2](https://github.com/openfootball/europe/blob/63df627f3c87ce71708f016522ea236cf74332c3/france/2025-26_fr1-full.txt#L30) |
| AS Monaco FC — Le Havre AC | 2025-08-16 17:00:00 UTC | [19:00 UTC+2](https://github.com/openfootball/europe/blob/63df627f3c87ce71708f016522ea236cf74332c3/france/2025-26_fr1-full.txt#L48) |

Evidence first recorded locally: `2026-10-21T11:06:49+00:00`.
Actual revision ingestion: `2026-10-21T11:13:18.009786+00:00`.
These are real current receipts, never backdated to the 2025 matches. CI records
its own later SQLite ingestion time; it does not claim the local timestamp as
its first observation of a forecast.

## Append-only revision and checks

The original source manifest, permission evidence, first ingestion batch and
quarantine are unchanged. The original simple sample/registry/evidence files are
also unchanged. The same source identity receives a **new ingestion batch** with
15 match/result observations. New generic `provenance` metadata is immutable
batch evidence only and cannot override importer clocks or accepted data fields.
No schema change and no automatic quarantine promotion are introduced.

The resolver operates only on the original 15 reviewed target references. Missing
proofs, altered identities/dates/scores, absent original quarantine, unverified
offsets and conflicting existing match identities remain unresolved with reasons.
An existing accepted kickoff is never overwritten to fit new evidence. Identical
reimport changes no table and preserves original first-observation timestamps.

After the real import, all original quarantine rows and permissions were also
compared with the previous pilot backup and match exactly. New Backup/Restore
passes SQLite integrity/FK checks and comparison of every table, including all
original evidence plus the new revision batch.

## Offline reproduction and artifacts

```sh
python scripts/timezone_pilot.py --output reports/openfootball-timezone-new-run
```

Default isolated DB: `foundation-data/openfootball-pilot/football_foundation.db`.
A fresh CI DB first imports the same original quarantine using CI's real receipt
clock, then appends the resolution. The existing local DB retains its original
history. Existing artifact/backup folders are never overwritten.

Reports for this actual local run are in `reports/openfootball-timezone/`.
CI uses committed fixtures only, denies external Python sockets and performs no
football downloads or Odds API calls. Its current revision backup/report/restored
DB are under **`openfootball-timezone-revision/`** in the
`foundation-and-regression-reports` artifact. The older
`openfootball-real-pilot/` subfolder is the quarantine-only checkpoint, and the
root demo DB remains fictional; these are deliberately distinct artifacts.

## Unchanged scope and remaining gaps

UI/UX/CSS/cards/four tabs and protected app hashes remain unchanged. No A/B/C,
Poisson, EV or settlement formula changes; no bill access, paid service, merge or
deployment. Historical rows remain `research`, never pre-match observed history.
No xG, shots or odds are invented. Fifteen first-round results alone still lack
sufficient team history, xG/xGA and actual AH/OU price snapshots for model/EV,
calibration, ROI, Yield or CLV conclusions. No expansion beyond these games is
included; further work awaits approval.
