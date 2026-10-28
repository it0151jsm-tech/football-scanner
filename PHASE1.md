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
scoped HTML/CSS dashboard components; it does not require a horizontally
scrolling data grid. Light/dark theme colors, flexible two-column form tiles,
large evidence/goal figures, a labeled H/D/A bar, OU tiles, side-by-side price
comparison and a short C conclusion provide the primary hierarchy. PPG, full
statistics, 10-match details, timestamps and technical notes are in the native
Streamlit expander `ดูรายละเอียดเพิ่มเติม`. All external text is HTML-escaped;
only validated finite numeric model probabilities enter bar-width CSS.

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

## Grouped AH/OU recommendations

The mobile view now reads all saved upcoming picks and groups by `event_id`,
instead of feeding the legacy table's highest-price/highest-A reduction into
cards. The original table and bill-building rankings remain unchanged. Each
fixture has independent AH and OU sections, including separate quote, line,
bookmaker, A/B/C, first price, and snapshot provenance. Missing market IDs are
isolated rather than merged by fixture text; conflicting team/league identities
within one event ID are flagged and excluded from combination.

For each market/side/bookmaker only the latest saved quote is considered (scan
time, then row ID). Ranking tiers are: A >=55 with at least five form examples
and internally consistent positive B EV; supporting form with unavailable EV;
supporting form with nonpositive EV; and insufficient evidence. Within a tier,
sample size capped at ten, EV, snapshot recency, A score, and row ID break ties.
Odds are not a ranking key. This is a display-selection policy, not a change to
the A/B/C formulas or a guarantee of profit. Other candidates and their reasons
remain inspectable in the details expander.

Unknown EV with form is labeled `แนวโน้มจากสถิติ — ยังยืนยันความคุ้มค่าราคาไม่ได้`.
Negative EV or insufficient evidence receives `ยังไม่แนะนำ` with reasons. An
absent/invalid market receives `ไม่มีราคาตลาด`; no quote/line is generated.

OU output is used only from that OU row, with its saved model line exactly
matching the actual quoted line (including 2.75 and 3.0). Mismatched or incomplete
OU probabilities are withheld, not recalculated from a reference line. AH-only
fixtures no longer display reference OU 2.5 tiles in the grouped dashboard.
Saved A price/line and row EV must also agree with the quote before they can
support an EV-based recommendation. No probabilities or EV are recomputed by
the presentation/selection modules.

Snapshot lookup reads SQLite using event, market, side, bookmaker, line, odds,
and an observation time no later than the saved pick scan. If no matching
snapshot exists, the card explicitly reports this and shows the pick's saved
time instead of claiming a snapshot ID. First Observed Price remains the first
recorded observation, never verified opening odds. View rendering performs no
Odds API calls and makes no database writes.
