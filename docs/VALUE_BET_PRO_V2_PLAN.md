# Value Bet Pro V2 — Evidence First

สถานะ: Design baseline only. This document does not change the production app, model formulas, database, Streamlit deployment, API settings, or betting recommendations.

## Mission

Build a football-market research system that can determine whether a pre-match model has measurable predictive value and market-timing value. Profitability is an empirical question, not a product promise.

## What the repository audit established

- The existing repository and Streamlit app remain available on `main`.
- The app is a Python/Streamlit + SQLite system using The Odds API and historical xG sources.
- The repository contains extensive regression tests, backtest tooling, observation-archive tooling, and operational documentation.
- The committed-data audit reports **zero qualified real pre-match AH/OU backtest observations (n=0)** in the audited material. This is not a claim that the production database is empty; production SQLite and live provider data were not inspected.
- Operational acceptance documentation says live end-to-end provider validation and real iPhone/Safari acceptance were not completed in that audit.
- Synthetic fixtures and passing software tests are not evidence of model accuracy or profitability.

## Non-negotiable rules

1. Do not edit or deploy `main` as part of V2 design work.
2. Do not delete, overwrite, migrate, or inspect production betting slips as part of research.
3. Never fabricate historical decision timestamps, odds, xG, results, or source receipts.
4. Keep `observed` data (captured before kickoff with verifiable receipt evidence) separate from `research` data retrieved later.
5. A missing or stale feature means unavailable; never silently substitute goals for xG or manufacture EV.
6. Separate software correctness, predictive performance, market value, and realized profitability.
7. No real-money betting recommendation until the validation protocol is passed and independently reviewed.

## V2 scope — deliberately small

### Phase 0: Read-only recovery audit
- Map current entry points, data flow, SQLite schemas/migrations, model functions, price capture, result settlement, and Streamlit deployment assumptions.
- Inspect the existing test suite and reports; preserve useful components rather than rewriting them.
- Record unresolved production questions separately because the committed repository cannot prove what is inside a deployed app's local SQLite database.

### Phase 1: Evidence contract
Every decision record must include:
- stable event identity, competition, season, home/away teams, kickoff in UTC;
- decision timestamp and model version;
- exact input observation IDs and source payload hashes;
- feature values available at decision time, with source and actual receipt time;
- quote identity: bookmaker, market, side, line, decimal odds, market update time, capture/receipt time;
- explicit data-quality and model-readiness status.

The decision must be strictly before kickoff. Closing prices and match results are outcome/evaluation data only; they must never enter the prediction features.

### Phase 2: One transparent baseline
- Start with a transparent goals/xG Poisson baseline using only eligible pre-match team observations.
- Keep home and away attack/defence estimates separate and document shrinkage/sample-size handling.
- Do not convert a heuristic score into a probability.
- Treat unsupported leagues and missing xG as unavailable, not zero.
- Keep AH and OU evaluation separate.
- Calculate Asian handicap and totals expected return using full quarter-line settlement states; do not use a binary win/loss shortcut.
- Benchmark against de-vigged market probabilities on the same timestamp and market.

Advanced models (Dixon–Coles, hierarchical strengths, calibration models) are candidates only after the baseline has a clean evidence trail and a time-based holdout.

### Phase 3: Shadow capture
- Capture upcoming fixtures and exact AH/OU quotes without placing bets.
- Store immutable raw payload evidence and append-only normalized observations.
- Log the model decision at capture time.
- Capture later price observations, including a closing snapshot when available.
- Record final results separately after settlement.
- Show missing coverage and API-credit consumption explicitly.

### Phase 4: Validation
Use chronological walk-forward evaluation, not random train/test splitting. Freeze the model and thresholds before each holdout period.

Report at minimum:
- number of unique fixtures, decisions, and settled selections separately;
- coverage/missingness by league, market, and source;
- Brier score and log loss for probability quality;
- calibration curve/reliability by probability bucket with uncertainty;
- ROI/Yield and profit by market and settlement type, with uncertainty and all settled stakes accounted for;
- CLV only when bookmaker, market, side, and line are exactly comparable; line changes are reported separately, not converted into invented equivalent prices;
- comparison to a de-vigged market baseline;
- performance by season/league and sensitivity to odds range.

No fixed sample count alone proves an edge. Require a pre-registered minimum cohort, confidence intervals, multiple-testing controls, and a genuinely untouched holdout before making claims.

## Initial operating constraints

- Preserve The Odds API allowance as a scarce resource. The previously stated allowance is 500 credits/month; verify the current account allowance before scheduling scans.
- Begin with one or two selected leagues and one daily scan window. Cache responses and never spend credits on UI rerenders.
- Keep secrets in Streamlit Secrets or environment variables; never commit API keys.
- Keep the current mobile-first Streamlit workflow as a later acceptance target; do not redesign the UI before the evidence pipeline works.

## First implementation milestone

Deliver a read-only architecture map and a testable evidence schema. Do not implement new prediction features yet. The next milestone is a shadow-capture vertical slice for one league, proving the complete chain:

`fixture → pre-match features → exact quote → timestamped decision → later closing quote → FT result → auditable evaluation row`

Acceptance requires every link to be traceable, with missing links visibly marked and no fabricated values. Only then begin model comparison.
