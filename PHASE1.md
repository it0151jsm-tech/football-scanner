# Phase 1: analysis C and mobile view

This branch starts from `fix/slip-settlement-and-model-integrity` (PR #1),
not from main. The new draft PR targets that branch. It does not merge or deploy
the production app.

## Existing behavior retained

Streamlit, The Odds API, SQLite, all four tabs, saved bills, quote freezing,
legacy JSON guards, final-score settlement, and full-history ROI are retained.
Only two nullable TEXT columns (`analysis_a_json`, `analysis_b_json`) are added
to picks using the existing additive migration mechanism. No database files or
existing rows are deleted and finalized results are not rewritten.

## Analysis systems

- **A:** a 0–100 evidence ranking from historical line-fit rates (5/10/all data)
  with a disagreement penalty and a small-sample cap. Venue-specific goals for
  and against, opponent quality, observed xG/xGA, and price movement are shown
  as supporting evidence, without arbitrary score bonuses. Opponent quality is
  mean opponent points per game from up to ten games available strictly before
  each sampled match, with at least three opponent games required. This is a
  descriptive schedule-strength measure, not a trained strength adjustment.
- **B:** the existing independent xG Poisson model and AH/OU expected-return
  calculation. It does not consume System A scores, form rates, price movement,
  or opponent PPG. Expected goals are lambdas, not a predicted exact score.
  Probabilities are model-derived and **not calibrated**. Missing, stale, or
  insufficient xG produces no substitute probabilities or EV.
- **C:** displays the two results together. Evidence is never presented as a
  win probability. The prominent-option label requires both A >= 65 and
  positive B EV; form alone, unknown EV, and nonpositive EV cannot earn it.

The existing Poisson independence and 12-goal tail approximation are unchanged.
Actual xG evidence may be shown in A even when the B minimum sample is not met.
For European cups, domestic xG remains context only; B cup EV stays unavailable.
The 120-day freshness rule is retained. Old score-only form may still be shown
as historical evidence; it is not a claim about current betting value.

## Prices and mobile presentation

Cards are the default in the analysis tab. The original table is available via
the view selector, and detailed history/alternative bookmaker tables remain in
expanders or their existing tabs. Main card text wraps vertically using standard
Streamlit elements; it does not require a horizontally scrolling data grid.

Cards show fixture/time, venue form and goals, actual xG/xGA, opponent PPG,
first/current price and line, A evidence, B expected goals and probabilities,
EV, combined C interpretation, and explicit data/calibration status.

`First Observed Price` means the first stored snapshot per event/market/side/
bookmaker. The source does not provide verified opening odds. When the line
changes, raw odds deltas are suppressed. Probability of Over/Under on an AH card
uses a clearly labeled 2.5 model reference; it is not a bookmaker quote. At
quarter lines, full-win Over/Under probabilities leave a separate half-result/
push category. These categories must not be interpreted as binary probabilities.

Views read saved JSON only. They do not call odds/history APIs, and do not
recompute or backfill legacy recommendations on render. Scan remains an explicit
button action with the existing five-minute odds cache. The first-price lookup
is local SQLite. Legacy scores remain stored but are not relabeled as A; rescan
upcoming fixtures to generate independent A/B records. Existing bills keep
their original quote and settlement semantics.

## Testing and Streamlit Cloud

Run the commands in TESTING.md. Automated coverage includes independent A/B,
complete/missing/stale data, AH/OU and 92 EV cases, source-price comparisons,
view switches with all HTTP requests forbidden, old schema upgrades, damaged
legacy bills, finalized-result preservation, and exact four-tab labels.

AppTest verifies rendering and interaction, not Safari pixel layout. A real
iPhone acceptance check is still required; live data coverage and calibration
are not established by offline tests.

For a test deployment, create a separate Streamlit Community Cloud app using:

- Repository: `it0151jsm-tech/football-scanner`
- Branch: `feat/phase1-analysis-c-mobile`
- Entry point: `app.py`
- A separate test URL; do not change the main app's branch or settings.
- Set the Odds API key in Secrets or use the existing in-app key field.

A separate app creates a fresh local SQLite database automatically. Do not upload
the production backup for initial testing. Scan one league, check both card/table
views on iPhone, confirm switching does not consume API quota, then test a saved
bill and its result workflow. SQLite on Community Cloud is not durable across
rebuilds; download test backups when needed. No production deployment or merge
has been performed by this change.
