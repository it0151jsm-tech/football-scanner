# Private pilot readiness — backend only

Base: PR #2 `fac7ea0d389e72c171ae0fd154d1870cc4ec8b56`.
Dependency: main ← PR #1 ← PR #2 ← this PR. This PR targets PR #2's branch.
PR #3 / Foundation is neither imported nor modified. No merge/deploy is authorized.

## Operating policy

- The analysis card, table and new slips use the selected leagues and strictly future
  kickoffs no more than 24 hours away. The existing UI (including its 48-hour control)
  is unchanged; the backend safety policy caps that setting at 24 hours for this pilot.
- No selected leagues means no current analysis/slip suggestions. Historical tabs
  keep their original data. Nothing is deleted.
- New API quotes preserve response `received_at`, market-level `source_updated_at`
  (`markets[].last_update`) and `bookmaker_key`. No fallback from country, bookmaker
  timezone, bookmaker-level deprecated timestamp, or scan click time is invented.
- A cached response retains its original receipt. Identical prices from new responses
  are separate snapshots; a repeat of the same response is deduplicated.
- Both timestamps must be aware, no more than 15 minutes old and no more than 30 seconds
  ahead of the observing clock; provider update cannot be after receipt beyond that
  tolerance. The age limit can be tightened, not relaxed above 900 seconds.
- Historical quotes lacking this evidence remain stored and may show form/trend/No Bet.
  Their EV is suppressed in recommendation presentation, not erased from the DB.
- Removed bookmakers/lines from older saved event responses cannot re-enter current
  analysis. A latest API response with no valid prices produces the existing no-price
  row; that response's receipt also invalidates older saved quotes for that event.
- AH and OU both require supported A (>=55, >=5 samples), coherent real B and strictly
  positive, matching EV. OU also requires its existing paired main-line assessment.
  New slips recheck the canonical stored data under a transaction at click time.
- Ranking of qualified slip candidates prioritizes EV before A, with at most one leg
  per event. Actual stake accounting, calibration and profitability are not established.
- No probability/model/settlement formulas were changed. The old UI captions/layout
  are kept verbatim; the scope and policy above take precedence over legacy captions.

## API controls — one shared ledger, not a global service

Root Streamlit secrets / environment settings (no new UI):

```toml
VALUE_BET_DB = "data/private_pilot.db"
VALUE_BET_API_LEDGER = "data/private_pilot_api_budget.db"
VALUE_BET_API_DAILY_REQUESTS = "5"
VALUE_BET_API_DAILY_CREDITS = "10"
VALUE_BET_API_CREDIT_RESERVE = "10"
VALUE_BET_API_COOLDOWN_SECONDS = "300"
VALUE_BET_QUOTE_MAX_AGE_SECONDS = "900"
# THE_ODDS_API_KEY: owner-managed secret; never commit a real key.
```

Code defaults are 10 requests / 20 credits per UTC day, 10-credit reserve and >=300s
per-league/region/key cooldown. Suggested pilot values above are deliberately tighter;
choose them against the actual subscription and days remaining, not these examples alone.
Two markets/one region reserves 2 credits before every uncached request. SQLite
`BEGIN IMMEDIATE` makes reservations atomic between processes sharing that ledger.
Timeouts/crashes/unknown header costs retain the reservation. Response headers
`x-requests-last`, `x-requests-remaining`, `x-requests-used` reconcile known costs/balance.
HTTP 401/429 and an unexpectedly larger cost stop the remaining league scan and block
that key in the ledger. Other HTTP/network/malformed-payload errors stop the scan too.
Cache hits do not consume reservations. Credential values and URLs/provider error
bodies are excluded from UI errors; registered keys are redacted before Python log
handlers receive messages/tracebacks. Raw HTTP debugging is disabled for these requests.

Limits are **not guaranteed across separate apps, separate ledger files, a lost ledger,
external use of the key, or multiple independent instances/filesystems**. The first
request cannot know the provider's current balance before receiving headers. Unexpected
provider charges can already have occurred when discovered. Do not share this pilot
key with another app; disable other consumers or allocate independent keys/budgets.
A ledger reset is not a quota reset. After 401/429 the owner must verify provider
permission/reset/balance before an intentional ledger recovery. There is no automatic
retry, automatic reset of the blocked key, scheduler, or background scanning.

## Result safety

Date-only results must have exact mapped home/away teams, a valid prior kickoff,
a unique candidate in the existing +/-1-day search, an exact UTC kickoff date, and
nonnegative integer full-time scores. Adjacent-day or multiple candidates wait for
manual review; no nearest-row tie breaker remains. The review queue stores evidence
append-only, separately from picks/slips. Existing manual-result controls are reused
for queued records; their original labels remain unchanged. The owner must independently
verify official finality, teams, date and score before checking the existing confirmation.
Date-only CSV evidence still does not prove exact result publication time or handle
all postponed/abandoned matches automatically. Uncertain cases must stay pending.

Future/invalid kickoff records cannot enter the manual queue or be submitted through the manual handler. Empty sources and unmatched names for past fixtures enter the same review queue with reasons. Boolean scores are rejected.

Updates to finalized picks/slip results are blocked by SQLite triggers. Rescans and new-slip saves take the same write lock before reading immutable references. The update
function also uses a pending-only condition, so a second writer cannot overwrite a
first settlement. No historical rows are repaired or re-settled by this patch.

## Backup and restore — test database only

The existing download button uses SQLite Backup API with a read-only source connection,
then `PRAGMA integrity_check`, and produces a single self-contained SQLite copy including
committed WAL data. `restore_new()` validates a copy and refuses an existing destination;
there is no app restore button or automatic restore.

For the private pilot, download backups to the owner's device and keep a second copy
in an owner-controlled encrypted Files/iCloud Drive folder or equivalent private storage.
Keep the DB out of GitHub, public URLs and chat uploads. Restrict access to the owner,
record download time and SHA-256 on the device, and verify an offline restored copy.
No cloud-storage service is connected by this PR. Local backups/ledgers on Streamlit
Cloud do not establish persistence; Community Cloud local files are not guaranteed.

**No-Go for real-money bill storage until off-instance retention, recovery and ownership
have been verified.** All automated backup/restore tests use disposable databases only.

## Owner acceptance — Safari/iPhone and Streamlit Cloud

No deployment was performed. Before authorizing a separate test app:

1. Verify the selected branch is `fix/private-pilot-readiness`, entry `app.py`, Python
   3.11, and private Sharing / only the owner can view. Never point the main app here.
2. Confirm a NEW pilot DB path and a separate ledger. Do not upload, move or test against
   the real bill DB. Confirm secrets are private and the key is not shared elsewhere.
3. Review actual source permissions and available CSV/xG/name matches for enabled leagues.
   The stored audit supports initial investigation of E0/SP1/I1/D1/F1 only; it is not
   live proof that every club or fixture is ready. No alias was guessed by this patch.
4. Without pressing Scan, check all 4 tabs, cards/table and expanders on Safari at
   320/375/390/430 CSS px, light/dark, portrait/landscape, rotation, long club names,
   text zoom, page reload and return from background. No API activity should occur.
5. Check zero selected leagues, one selected league, only <24h future fixtures, fresh /
   stale / missing-provenance quotes, no-EV / negative-EV cases and a page left open
   until its quotes expire. Attempting a stale new slip must be refused.
6. Inspect a test backup/restore with committed pending and finalized mock bills.
   Confirm the restored copy preserves counts/results and passes integrity_check.
7. Review quota configuration and owner-only access before separately authorizing any
   real Odds API acceptance request. This work did not call that API.
8. Test an ambiguous mock result, manual review, a repeated settlement click, and no
   changes to already-finalized mock results. Keep real bills outside the pilot.

## Evidence and Go / No-Go

`fixtures/readiness/pr2-contract.json` records baseline UI call literals/layout flags,
mobile renderer/CSS hash, and AST hashes of unchanged model/settlement functions.
The contract test compares them to this patch; browser tests check rendering and escaping,
not betting accuracy. Mobile templates are byte-identical to PR #2. Dependencies are
pinned to the direct versions already used by PR #2's successful CI run 39449438019.
CI runs offline fixtures with Python socket access denied to external hosts; source
crawlers were removed from automatic PR CI. Playwright/dependency installation still
needs package/browser downloads during CI setup; tests do not download football data.

Conditional Go for an owner-only, isolated, disposable-data pilot only after CI and owner
Safari/Cloud/privacy/backup acceptance. Until those checks, operational readiness is
pending. No-Go for public use, real-money bill storage or claims of calibrated/profitable
predictions. Long-term persistent storage/auth redesign and full 38-league validation
require separate approval and are not implemented here.

## Verification for this change

34 new acceptance/regression tests cover the P0 paths. Local Python 3.12 runner reported: `Ran 129 tests; OK (skipped=1)`.
The skipped item is the browser class because of sandbox restrictions. The separately run
Chromium mobile suite passed 3 tests at all four widths in light/dark; final GitHub CI
uses Python 3.11 with Chromium and must pass all tests without skips. No Odds API calls
were made. Refer to this PR Actions run for the authoritative final CI result.
