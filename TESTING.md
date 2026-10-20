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
