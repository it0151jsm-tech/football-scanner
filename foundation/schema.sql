PRAGMA application_id = 1447186502;
PRAGMA user_version = 1;
CREATE TABLE foundation_meta(kind TEXT PRIMARY KEY CHECK(kind='value-bet-foundation'), schema_version INTEGER NOT NULL);
INSERT INTO foundation_meta VALUES ('value-bet-foundation',1);
CREATE TABLE sources(
 id TEXT PRIMARY KEY, manifest_json TEXT NOT NULL, manifest_sha256 TEXT NOT NULL,
 dataset_kind TEXT NOT NULL CHECK(dataset_kind IN ('test_sample','research')),
 rights_status TEXT NOT NULL CHECK(rights_status='verified'), registered_at TEXT NOT NULL);
CREATE TABLE source_permissions(
 source_id TEXT PRIMARY KEY REFERENCES sources(id), evidence_sha256 TEXT NOT NULL,
 evidence_text TEXT NOT NULL, reviewed_by TEXT NOT NULL, reviewed_at TEXT NOT NULL);
CREATE TABLE ingestion_runs(
 id INTEGER PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), sha256 TEXT NOT NULL,
 ingested_at TEXT NOT NULL, raw_json TEXT NOT NULL, accepted INTEGER NOT NULL DEFAULT 0,
 quarantined INTEGER NOT NULL DEFAULT 0, UNIQUE(source_id,sha256));
CREATE TABLE teams(
 id INTEGER PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), source_team_id TEXT NOT NULL,
 name TEXT NOT NULL, country TEXT NOT NULL, category TEXT NOT NULL,
 observed_at TEXT NOT NULL, quality TEXT NOT NULL CHECK(quality='accepted'),
 UNIQUE(source_id,source_team_id));
CREATE TABLE team_season_memberships(
 team_id INTEGER NOT NULL REFERENCES teams(id), league TEXT NOT NULL, season TEXT NOT NULL,
 run_id INTEGER NOT NULL REFERENCES ingestion_runs(id), observed_at TEXT NOT NULL,
 PRIMARY KEY(team_id,league,season));
CREATE TABLE matches(
 id INTEGER PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), source_match_id TEXT NOT NULL,
 league TEXT NOT NULL, season TEXT NOT NULL, kickoff_utc TEXT NOT NULL,
 home_id INTEGER NOT NULL REFERENCES teams(id), away_id INTEGER NOT NULL REFERENCES teams(id),
 observed_at TEXT NOT NULL, quality TEXT NOT NULL CHECK(quality='accepted'),
 CHECK(home_id != away_id), UNIQUE(source_id,source_match_id));
CREATE TABLE observations(
 id INTEGER PRIMARY KEY, match_id INTEGER NOT NULL REFERENCES matches(id),
 run_id INTEGER NOT NULL REFERENCES ingestion_runs(id), source_id TEXT NOT NULL REFERENCES sources(id),
 observed_at TEXT NOT NULL, source_updated_at TEXT,
 payload_sha256 TEXT NOT NULL, quality TEXT NOT NULL CHECK(quality='accepted'),
 UNIQUE(match_id,payload_sha256));
CREATE TABLE team_stats(
 observation_id INTEGER NOT NULL REFERENCES observations(id), team_id INTEGER NOT NULL REFERENCES teams(id),
 period TEXT NOT NULL CHECK(period='FT90'), stats_json TEXT NOT NULL,
 PRIMARY KEY(observation_id,team_id));
CREATE TABLE xg_stats(
 observation_id INTEGER PRIMARY KEY REFERENCES observations(id), home_xg REAL NOT NULL CHECK(home_xg>=0),
 away_xg REAL NOT NULL CHECK(away_xg>=0), method TEXT NOT NULL, period TEXT NOT NULL CHECK(period='FT90'));
CREATE TABLE match_results(
 observation_id INTEGER PRIMARY KEY REFERENCES observations(id), home_goals INTEGER NOT NULL CHECK(home_goals>=0),
 away_goals INTEGER NOT NULL CHECK(away_goals>=0), period TEXT NOT NULL CHECK(period='FT90'),
 status TEXT NOT NULL CHECK(status='final'));
CREATE TABLE odds_snapshots(
 id INTEGER PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), run_id INTEGER NOT NULL REFERENCES ingestion_runs(id),
 source_quote_id TEXT NOT NULL, match_id INTEGER NOT NULL REFERENCES matches(id), bookmaker TEXT NOT NULL,
 market TEXT NOT NULL CHECK(market IN ('Asian Handicap','Over/Under')),
 side TEXT NOT NULL CHECK(side IN ('home','away','over','under')), line_quarters INTEGER NOT NULL,
 odds REAL NOT NULL CHECK(odds>1), period TEXT NOT NULL CHECK(period='FT90'),
 captured_at TEXT NOT NULL, observed_at TEXT NOT NULL, quality TEXT NOT NULL CHECK(quality='accepted'),
 UNIQUE(source_id,source_quote_id));
CREATE TABLE quarantine(
 id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES ingestion_runs(id), entity TEXT NOT NULL,
 source_row_id TEXT, reason TEXT NOT NULL, payload_json TEXT NOT NULL, observed_at TEXT NOT NULL);
CREATE TABLE model_predictions(
 id INTEGER PRIMARY KEY, validation_id TEXT NOT NULL, match_id INTEGER NOT NULL REFERENCES matches(id),
 quote_id INTEGER REFERENCES odds_snapshots(id), model_hash TEXT NOT NULL,
 mode TEXT NOT NULL CHECK(mode IN ('research','observed','sample')), prediction_at TEXT NOT NULL,
 evaluated_at TEXT NOT NULL, input_observation_ids_json TEXT NOT NULL,
 prediction_json TEXT NOT NULL, quality TEXT NOT NULL, UNIQUE(validation_id,quote_id));
CREATE TABLE validation_runs(
 id TEXT PRIMARY KEY, created_at TEXT NOT NULL, mode TEXT NOT NULL, dataset_sha256 TEXT NOT NULL,
 model_hash TEXT NOT NULL, report_json TEXT NOT NULL);
CREATE INDEX idx_matches_league_time ON matches(league,kickoff_utc);
CREATE INDEX idx_observations_match_time ON observations(match_id,observed_at);
CREATE INDEX idx_odds_match_time ON odds_snapshots(match_id,captured_at);
