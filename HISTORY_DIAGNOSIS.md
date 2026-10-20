# Oxford United / Wigan Athletic historical form diagnosis

Investigated against the supplied fixture date 2026-10-21. The full API names
come from the user's fixture report; no fresh Odds API call or production
database access was performed.

## Direct CSV evidence

GitHub Actions run 39432169259 downloaded these source files directly on
2026-10-20. The workspace cannot make outbound requests and the web reader
cannot parse CSV, so the live HTTP/schema/content check ran on the CI runner.

| E2 season | HTTP | Parsed valid rows | Invalid dates | CSV target names |
| --- | --- | --- | --- | --- |
| [2627](https://www.football-data.co.uk/mmz4281/2627/E2.csv) | 200 | 114 | 0 | Oxford, Wigan |
| [2526](https://www.football-data.co.uk/mmz4281/2526/E2.csv) | 200 | 552 | 0 | Wigan |
| [2425](https://www.football-data.co.uk/mmz4281/2425/E2.csv) | 200 | 552 | 0 | Wigan |
| [2324](https://www.football-data.co.uk/mmz4281/2324/E2.csv) | 200 | 552 | 0 | Oxford, Wigan |

All four have Date, HomeTeam, AwayTeam, FTHG and FTAG. All 1,770 parsed rows
were before the supplied fixture day. The original date parser succeeded for
these live files; date parsing failure was **not** observed as the actual cause.
Neither was HTTP failure/empty CSV observed in this check. That does not prove
the prior Streamlit request succeeded; the production logs were not available.

The current map_team returned None for both full API names against the observed
CSV names Oxford and Wigan. Its normalized-string fuzzy cutoff of .82 did not
bridge the full/short names. Explicit aliases now map Oxford United ↔ Oxford
and Wigan Athletic ↔ Wigan. Their source is recorded in history_data.py and the
diagnostic output. Approximate similarity matching is removed rather than
risking Oxford City, U21 teams or other clubs. Other unsupported variants are
reported as unmatched and require separately verified aliases.

In 2627 alone, Oxford has 8 completed games (3 home, 5 away), and Wigan has 9
(5 home, 4 away), before 2026-10-21. Oxford also has 23 home results in 2324;
Wigan has 23 away results in each of 2526/2425/2324. A five-game venue sample
can therefore span seasons, particularly Oxford's. The UI records sample dates,
per-season counts, and flags a five-game span over 180 days as including old
games. It never fabricates extra games to fill a window. That warning does not
change the existing A scoring formula or invent a recency adjustment.

## Scope and safeguards

- HTTP status, URL, bytes, checked time, schema, valid/invalid row counts and
  exception type are retained in DataFrame attrs and saved A profile diagnostics.
- Errors distinguish failed downloads, HTTP failures, empty files, missing
  columns, bad dates/scores, unmatched/ambiguous names and insufficient pre-game
  venue results. Mobile form tiles show the short cause; full provenance is in
  the details expander. Details are HTML escaped.
- Date parsing explicitly supports dd/mm/yyyy, dd/mm/yy and ISO date rows without
  inference dropping a mixed-year format. Only valid nonnegative integer scores
  and named teams are retained. Same-day/future results are excluded using the
  fixture's UTC day, matching the prior conservative cutoff policy.
- The CSV cache TTL is reduced from six hours to fifteen minutes so failed source
  responses do not remain cached for six hours. Fetching still occurs on explicit
  scan/result actions; rendering and view toggles never request CSV or Odds API.
- E2 is not configured in UNDERSTAT_LEAGUES. Fixing score-based form is separate
  from B model availability. The 2627 CSV contains HxG/AxG column headers, but
  coverage, definitions and model integration have not been designed/validated.
  They are **not** automatically used in B. No actual goals substitute for xG.
- SQLite schema, existing bills, settled results and settlement formulas are
  unchanged. Existing saved form JSON is not rewritten on render; explicitly
  rescan the fixture in the separate test app to save updated diagnostics.

scripts/check_e2_history.py runs only on PR CI, calls four CSV URLs and no Odds
API, and uploads e2-live-history-audit.json. It also runs the corrected parser
and alias/cutoff pipeline against live files, reporting the post-fix sample
counts. Network failures are recorded in the audit rather than mistaken for
application regression failures. Deterministic unit tests use mocked downloads.
