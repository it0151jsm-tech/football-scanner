Run the offline regression suite and Streamlit smoke tests with:

```sh
pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m py_compile app.py
```

The function tests load definitions from `app.py` without running the Streamlit UI,
use temporary SQLite databases, and never call live odds/history APIs. Smoke tests
use Streamlit AppTest; they skip if Streamlit is unavailable. GitHub Actions installs
the application requirements so both suites run there.

Coverage includes saved and legacy bill quote isolation, rescans, idempotent
settlement, history beyond 5,000 rows, schema upgrades without deleting data, OU
fixture deduplication, unsupported result sources, manual legacy settlement, and
Asian quarter lines and 92 combinations of Poisson EV/settlement.

The xG freshness cutoff is 120 days from the fixture date for the most recent match
in each venue sample. This permits an off-season but rejects old-only feeds. This
is an explicit operational threshold, not a calibrated statistical parameter.

Existing picks, snapshots, and bills are retained. Pending legacy bills recover
their line from the saved selection text and use the final score; unreadable
legacy selections remain pending rather than guessing a line. Already finalized
bills are not automatically rewritten. New bills without a configured automatic
result source are disabled; existing bills in those leagues can be resolved with
a confirmed final score in the history tab.

Tests use temporary data only. Live API availability and prediction accuracy are
outside this regression suite.

Damaged legacy JSON, blank/NULL payloads, unexpected JSON shapes, missing IDs,
invalid ID types, and mixed valid/invalid legs are covered by regression tests.
Reading a damaged bill never rewrites its JSON or finalized result. Pending bills
with unreadable references remain pending. When a nonempty damaged payload may
hide references, scans append fresh quotes rather than overwrite existing picks;
this conservatively retains history and can increase the number of pick rows
until the legacy data is repaired. Valid empty lists and blank/NULL payloads have
no recoverable references and do not stop scans. Streamlit smoke coverage checks
the same four tab labels with damaged bills present and compares all original
bill columns before and after rendering.

Phase 1 adds tests in `tests/test_analysis.py` for independent A/B results,
model-only percentages, missing and stale xG, unknown/nonpositive EV, actual
opponent history, First Observed Price, and additive migrations retaining all
old pick/snapshot/bill fields. Streamlit smoke tests switch mobile/table views
while forbidding HTTP requests. See PHASE1.md for model limitations, mobile
acceptance checks, and separate Streamlit Cloud test deployment instructions.

The dashboard presentation module is tested independently of analysis. HTML tests
cover complete/missing/partial saved model data, real versus reference OU lines,
changed price lines, technical-detail placement, escaped API text and invalid
numeric inputs. Chromium tests check widths 320/375/390/430 in light and dark
themes, two-column form layout, expanded details, and hostile long team names.
CI installs `requirements-test.txt` and Chromium, runs every test, and uploads
`iphone-dashboard-previews` screenshots (test fixtures, not live matches).
The local sandbox may skip Chromium/AppTest when browser launch or Streamlit is
unavailable; these tests must pass in CI. Safari on an actual iPhone remains an
acceptance check after updating the separate test app.

Grouped-market coverage includes one event with multiple markets/bookmakers,
latest-per-book quote isolation, ranking independent of highest odds/A alone,
unknown/nonpositive EV, missing market prices, exact OU quarter/integer lines,
mismatched reference lines, and snapshot lookup across bookmaker/market/line/time.
AppTest switches between grouped cards and the original table with HTTP forbidden
and compares picks, slips and snapshots before/after. Chromium checks grouped
AH/OU layouts and uploads grouped light/dark screenshots alongside earlier UI
fixtures. Compile the additional read-only module with
`python -m py_compile app.py mobile_dashboard.py market_recommendations.py`.

Historical-data tests in test_history_data.py cover timeout/HTTP errors, empty or
malformed schemas, explicit verified aliases versus wrong/similar clubs, mixed
date formats, invalid/incomplete scores, UTC kickoff-day exclusion, old-season
sample warnings, diagnostic escaping and absence of goals-to-xG substitution.
The PR-only live E2 audit writes HTTP/schema/name/count evidence plus post-fix
sample diagnostics to e2-live-history-audit.json in the existing CI artifact.
See HISTORY_DIAGNOSIS.md for the verified root cause and limits of the live check.
