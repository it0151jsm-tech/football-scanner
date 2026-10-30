"""Validated Football-Data scores and explicit diagnostics; never creates xG."""
import re
import hashlib
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from io import BytesIO

import pandas as pd


SEASONS = ('2627', '2526', '2425', '2324')
REQUIRED = {'Date', 'HomeTeam', 'AwayTeam', 'FTHG', 'FTAG'}
# Direct E2 CSV audit, 2026-10-20, Actions run 39432169259:
# 2627/2324 contain "Oxford" and all four seasons contain "Wigan".
# The full names are the supplied Odds API fixture labels, not guessed spellings.
ALIAS_SOURCE = 'https://www.football-data.co.uk/mmz4281/2627/E2.csv'
_ISSUE9_SOURCE = 'https://github.com/it0151jsm-tech/football-scanner/issues/9'
_UNDERSTAT_AUDIT = 'https://github.com/it0151jsm-tech/football-scanner/actions/runs/39448791254/artifacts/12275769505'
# These are reviewed provider labels, not suffix-removal or fuzzy-match rules.
# The four Issue #9 groups have current CSV HTTP/hash/receipt evidence in
# fixtures/team_identity_evidence.json. That file separately records the three
# evidenced Understat labels; Ipswich Understat coverage is not asserted. An
# alias establishes identity only: callers still check league/season membership.
TEAM_ALIAS_RECORDS = (
    {'id': 'oxford-united', 'aliases': ('Oxford United', 'Oxford'), 'csv_source': ALIAS_SOURCE},
    {'id': 'wigan-athletic', 'aliases': ('Wigan Athletic', 'Wigan'), 'csv_source': ALIAS_SOURCE},
    {'id': 'vfb-stuttgart', 'aliases': ('VfB Stuttgart', 'Stuttgart'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/D1.csv',
     'api_evidence': _ISSUE9_SOURCE, 'understat_evidence': _UNDERSTAT_AUDIT},
    {'id': 'borussia-monchengladbach',
     'aliases': ('Borussia Monchengladbach', "M'gladbach", 'Borussia M.Gladbach'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/D1.csv',
     'api_evidence': _ISSUE9_SOURCE, 'understat_evidence': _UNDERSTAT_AUDIT},
    {'id': 'ipswich-town', 'aliases': ('Ipswich Town', 'Ipswich'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/E0.csv',
     'api_evidence': _ISSUE9_SOURCE},
    {'id': 'nottingham-forest', 'aliases': ('Nottingham Forest', "Nott'm Forest"),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/E0.csv',
     'api_evidence': _ISSUE9_SOURCE, 'understat_evidence': _UNDERSTAT_AUDIT},
    # Historical CSV labels + official club naming evidence, reviewed for Issue15.
    # This establishes identity, never current league/season or Odds API coverage.
    {'id': 'manchester-city', 'aliases': ('Manchester City', 'Man City'),
     'csv_source': 'https://api.github.com/repos/datasets/football-datasets/git/blobs/080a33fc535bf75e4c3baeca32ce425760985758',
     'identity_evidence': 'https://www.mancity.com/terms-of-use',
     'evidence_file': 'fixtures/issue15_identity_evidence.json',
     'understat_evidence': _UNDERSTAT_AUDIT},
    {'id': 'manchester-united', 'aliases': ('Manchester United', 'Man United'),
     'csv_source': 'https://api.github.com/repos/datasets/football-datasets/git/blobs/080a33fc535bf75e4c3baeca32ce425760985758',
     'identity_evidence': 'https://www.manutd.com/en/news/focus-on-man-utd-supporters-clubs-in-china-seattle-switzerland',
     'evidence_file': 'fixtures/issue15_identity_evidence.json',
     'understat_evidence': _UNDERSTAT_AUDIT},
    # Added for scan coverage: widely used Odds API <-> football-data.co.uk label pairs.
    # review_status marks pairs NOT yet audited against live labels; run scripts/audit_leagues.py to confirm.
    {'id': 'brighton-and-hove-albion', 'aliases': ('Brighton and Hove Albion', 'Brighton'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/E0.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'tottenham-hotspur', 'aliases': ('Tottenham Hotspur', 'Tottenham'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/E0.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'newcastle-united', 'aliases': ('Newcastle United', 'Newcastle'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/E0.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'west-ham-united', 'aliases': ('West Ham United', 'West Ham'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/E0.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'wolverhampton-wanderers', 'aliases': ('Wolverhampton Wanderers', 'Wolves'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/E0.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'leicester-city', 'aliases': ('Leicester City', 'Leicester'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/E0.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'leeds-united', 'aliases': ('Leeds United', 'Leeds'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/E0.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'afc-bournemouth', 'aliases': ('AFC Bournemouth', 'Bournemouth'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/E0.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'luton-town', 'aliases': ('Luton Town', 'Luton'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/E0.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'norwich-city', 'aliases': ('Norwich City', 'Norwich'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/E0.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'coventry-city', 'aliases': ('Coventry City', 'Coventry'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/E0.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'hull-city', 'aliases': ('Hull City', 'Hull'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/E0.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'stoke-city', 'aliases': ('Stoke City', 'Stoke'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/E0.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'swansea-city', 'aliases': ('Swansea City', 'Swansea'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/E0.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'cardiff-city', 'aliases': ('Cardiff City', 'Cardiff'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/E0.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'west-bromwich-albion', 'aliases': ('West Bromwich Albion', 'West Brom'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/E0.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'sheffield-wednesday', 'aliases': ('Sheffield Wednesday', 'Sheffield Weds'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/E0.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'birmingham-city', 'aliases': ('Birmingham City', 'Birmingham'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/E0.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'blackburn-rovers', 'aliases': ('Blackburn Rovers', 'Blackburn'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/E0.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'preston-north-end', 'aliases': ('Preston North End', 'Preston'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/E0.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'queens-park-rangers', 'aliases': ('Queens Park Rangers', 'QPR'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/E0.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'derby-county', 'aliases': ('Derby County', 'Derby'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/E0.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'plymouth-argyle', 'aliases': ('Plymouth Argyle', 'Plymouth'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/E0.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'charlton-athletic', 'aliases': ('Charlton Athletic', 'Charlton'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/E0.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'atletico-madrid', 'aliases': ('Atlético Madrid', 'Ath Madrid', 'Atletico Madrid'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/SP1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'athletic-bilbao', 'aliases': ('Athletic Bilbao', 'Ath Bilbao'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/SP1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'real-betis', 'aliases': ('Real Betis', 'Betis'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/SP1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'real-sociedad', 'aliases': ('Real Sociedad', 'Sociedad'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/SP1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'celta-vigo', 'aliases': ('Celta Vigo', 'Celta'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/SP1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'rayo-vallecano', 'aliases': ('Rayo Vallecano', 'Vallecano'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/SP1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'deportivo-alaves', 'aliases': ('Deportivo Alavés', 'Alaves', 'Alavés'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/SP1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'real-valladolid', 'aliases': ('Real Valladolid', 'Valladolid'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/SP1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'rcd-mallorca', 'aliases': ('RCD Mallorca', 'Mallorca'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/SP1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'leganes', 'aliases': ('Leganés', 'Leganes'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/SP1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'cadiz', 'aliases': ('Cádiz', 'Cadiz'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/SP1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'almeria', 'aliases': ('Almería', 'Almeria'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/SP1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'real-oviedo', 'aliases': ('Real Oviedo', 'Oviedo'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/SP1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'borussia-dortmund', 'aliases': ('Borussia Dortmund', 'Dortmund'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/D1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'bayer-leverkusen', 'aliases': ('Bayer Leverkusen', 'Leverkusen'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/D1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'eintracht-frankfurt', 'aliases': ('Eintracht Frankfurt', 'Ein Frankfurt'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/D1.csv', 'review_status': 'unverified_label_pair'},
    {'id': '1-fc-union-berlin', 'aliases': ('1. FC Union Berlin', 'Union Berlin'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/D1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'fc-augsburg', 'aliases': ('FC Augsburg', 'Augsburg'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/D1.csv', 'review_status': 'unverified_label_pair'},
    {'id': '1-fsv-mainz-05', 'aliases': ('1. FSV Mainz 05', 'Mainz'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/D1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'sc-freiburg', 'aliases': ('SC Freiburg', 'Freiburg'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/D1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'tsg-hoffenheim', 'aliases': ('TSG Hoffenheim', 'Hoffenheim'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/D1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'vfl-wolfsburg', 'aliases': ('VfL Wolfsburg', 'Wolfsburg'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/D1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'vfl-bochum', 'aliases': ('VfL Bochum', 'Bochum'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/D1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'fc-st-pauli', 'aliases': ('FC St. Pauli', 'St Pauli'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/D1.csv', 'review_status': 'unverified_label_pair'},
    {'id': '1-fc-heidenheim', 'aliases': ('1. FC Heidenheim', 'Heidenheim'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/D1.csv', 'review_status': 'unverified_label_pair'},
    {'id': '1-fc-koln', 'aliases': ('1. FC Köln', 'FC Koln'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/D1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'hamburger-sv', 'aliases': ('Hamburger SV', 'Hamburg', 'Hamburg'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/D1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'inter-milan', 'aliases': ('Inter Milan', 'Inter', 'Internazionale', 'FC Internazionale'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/I1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'ac-milan', 'aliases': ('AC Milan', 'Milan'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/I1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'as-roma', 'aliases': ('AS Roma', 'Roma'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/I1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'ssc-napoli', 'aliases': ('SSC Napoli', 'Napoli'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/I1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'hellas-verona', 'aliases': ('Hellas Verona', 'Verona'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/I1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'atalanta-bc', 'aliases': ('Atalanta BC', 'Atalanta'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/I1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'bologna-fc', 'aliases': ('Bologna FC', 'Bologna'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/I1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'torino-fc', 'aliases': ('Torino FC', 'Torino'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/I1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'genoa-cfc', 'aliases': ('Genoa CFC', 'Genoa'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/I1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'paris-saint-germain', 'aliases': ('Paris Saint-Germain', 'Paris SG', 'Paris Saint Germain', 'PSG'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/F1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'olympique-marseille', 'aliases': ('Olympique Marseille', 'Marseille'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/F1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'olympique-lyonnais', 'aliases': ('Olympique Lyonnais', 'Lyon'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/F1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'as-monaco', 'aliases': ('AS Monaco', 'Monaco'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/F1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'losc-lille', 'aliases': ('LOSC Lille', 'Lille'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/F1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'ogc-nice', 'aliases': ('OGC Nice', 'Nice'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/F1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'stade-rennais', 'aliases': ('Stade Rennais', 'Rennes'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/F1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'rc-strasbourg', 'aliases': ('RC Strasbourg', 'Strasbourg'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/F1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'rc-lens', 'aliases': ('RC Lens', 'Lens'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/F1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'stade-brestois', 'aliases': ('Stade Brestois', 'Brest'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/F1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'fc-nantes', 'aliases': ('FC Nantes', 'Nantes'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/F1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'stade-de-reims', 'aliases': ('Stade de Reims', 'Reims'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/F1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'toulouse-fc', 'aliases': ('Toulouse FC', 'Toulouse'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/F1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'montpellier-hsc', 'aliases': ('Montpellier HSC', 'Montpellier'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/F1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'le-havre-ac', 'aliases': ('Le Havre AC', 'Le Havre'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/F1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'angers-sco', 'aliases': ('Angers SCO', 'Angers'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/F1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'aj-auxerre', 'aliases': ('AJ Auxerre', 'Auxerre'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/F1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'saint-etienne', 'aliases': ('Saint-Étienne', 'St Etienne'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/F1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'fc-lorient', 'aliases': ('FC Lorient', 'Lorient'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/F1.csv', 'review_status': 'unverified_label_pair'},
    {'id': 'fc-metz', 'aliases': ('FC Metz', 'Metz'),
     'csv_source': 'https://www.football-data.co.uk/mmz4281/2627/F1.csv', 'review_status': 'unverified_label_pair'},
)
MESSAGES = {
    'download_error': 'โหลดข้อมูลไม่สำเร็จ', 'http_error': 'แหล่ง CSV ตอบ HTTP ไม่สำเร็จ',
    'empty_file': 'ไฟล์ CSV ว่าง', 'empty_history': 'ไม่มีแถวผลย้อนหลังที่ใช้ได้',
    'missing_columns': 'ไฟล์ขาดคอลัมน์ที่จำเป็น', 'parse_error': 'อ่านไฟล์ CSV ไม่สำเร็จ',
    'invalid_dates': 'วันที่ผลย้อนหลังผิดรูปแบบ', 'invalid_scores': 'ผลประตูไม่ครบหรือไม่ถูกต้อง',
    'conflicting_scores': 'พบสกอร์ขัดแย้ง — แยกออกจากการคำนวณรอตรวจ',
    'invalid_season': 'วันที่ไม่ตรงฤดูกาลต้นทาง — แยกออกจากการคำนวณรอตรวจ',
    'name_unmatched': 'จับคู่ชื่อทีมไม่สำเร็จ', 'name_ambiguous': 'ชื่อทีมกำกวม — ไม่เดาจากชื่อคล้าย',
    'lossy_normalization': 'ชื่อทีมตรงกันเฉพาะหลังตัดอักขระ — ยังไม่ยืนยันว่าเป็นทีมเดียวกัน',
    'no_history_before_kickoff': 'ไม่มีผลก่อนวันแข่งขัน', 'insufficient_history': 'มีผลย้อนหลังตามสนามไม่ถึง 5 นัด',
    'invalid_kickoff': 'วันแข่งขันไม่ถูกต้องหรือไม่มีเขตเวลา', 'unsupported_source': 'ลีกนี้ไม่มีรหัสแหล่งผลย้อนหลัง',
    'ok': 'ข้อมูลฟอร์มพร้อมใช้',
}


def normalize(name):
    return re.sub(r'[^a-z0-9]', '', str(name or '').lower().replace('&', 'and'))


def _team_identity(name):
    """Keep accented letters: ASCII deletion is not identity evidence."""
    value = unicodedata.normalize('NFKC', str(name or '')).casefold().replace('&', 'and')
    return ''.join(char for char in value if char.isalnum())


# Kept for existing integrations; matching uses Unicode-preserving identities.
TEAM_ALIASES = tuple(frozenset(normalize(label) for label in record['aliases'])
                     for record in TEAM_ALIAS_RECORDS)


def alias_evidence(name, matched_name):
    """Return the reviewed club/source evidence, never another club's CSV URL."""
    first, second = _team_identity(name), _team_identity(matched_name)
    for record in TEAM_ALIAS_RECORDS:
        identities = {_team_identity(label) for label in record['aliases']}
        if first in identities and second in identities:
            evidence = dict(record)
            evidence['aliases'] = list(record['aliases'])
            if record.get('api_evidence') == _ISSUE9_SOURCE:
                evidence['evidence_file'] = 'fixtures/team_identity_evidence.json'
            return evidence
    return None


def match_team(name, candidates):
    identity = _team_identity(name)
    candidates = set(candidates)
    exact = [c for c in candidates if identity and _team_identity(c) == identity]
    # Multiple distinct provider labels in one history cannot be silently treated
    # as one team. This also rejects an exact label plus another reviewed alias.
    for record in TEAM_ALIAS_RECORDS:
        identities = {_team_identity(label) for label in record['aliases']}
        if identity in identities:
            matches = [c for c in candidates if _team_identity(c) in identities]
            if len(matches) > 1:
                return None, 'name_ambiguous'
            if len(matches) == 1:
                return matches[0], 'exact' if _team_identity(matches[0]) == identity else 'verified_alias'
    if len(exact) == 1:
        return exact[0], 'exact'
    if len(exact) > 1:
        return None, 'name_ambiguous'
    normalized = normalize(name)
    if normalized and any(normalize(c) == normalized for c in candidates):
        return None, 'lossy_normalization'
    return None, 'name_unmatched'


def parse_dates(values):
    """Explicit per-row formats avoid inference dropping mixed 2/4 digit years."""
    strings = values.astype('string').str.strip()
    result = pd.Series(pd.NaT, index=values.index, dtype='datetime64[ns]')
    for fmt in ('%d/%m/%Y', '%d/%m/%y', '%Y-%m-%d'):
        parsed = pd.to_datetime(strings, format=fmt, errors='coerce')
        result = result.fillna(parsed)
    return result


def validate_rows(frame):
    required = {'Date_dt', 'HomeTeam', 'AwayTeam', 'FTHG', 'FTAG'}
    if not required.issubset(frame.columns):
        return pd.DataFrame(), {'status': 'missing_columns', 'missing_columns': sorted(required-set(frame.columns))}
    out = frame.copy()
    out['Date_dt'] = pd.to_datetime(out['Date_dt'], utc=True, errors='coerce', format='mixed').dt.tz_convert(None)
    hg, ag = pd.to_numeric(out['FTHG'], errors='coerce'), pd.to_numeric(out['FTAG'], errors='coerce')
    bool_values = out['FTHG'].map(lambda v: isinstance(v, bool) or pd.api.types.is_bool_dtype(type(v))) | out['FTAG'].map(lambda v: isinstance(v, bool) or pd.api.types.is_bool_dtype(type(v)))
    scores = ~bool_values & hg.notna() & ag.notna() & (hg >= 0) & (ag >= 0) & (hg < float('inf')) & (ag < float('inf')) & (hg % 1 == 0) & (ag % 1 == 0)
    teams = out['HomeTeam'].notna() & out['AwayTeam'].notna() & out['HomeTeam'].astype(str).str.strip().ne('') & out['AwayTeam'].astype(str).str.strip().ne('')
    valid = out['Date_dt'].notna() & scores & teams
    info = {'raw_rows': len(out), 'invalid_dates': int(out['Date_dt'].isna().sum()), 'invalid_scores': int((~scores).sum()),
            'missing_teams': int((~teams).sum()), 'valid_rows': int(valid.sum()), 'status': 'ok' if valid.any() else 'empty_history'}
    out['FTHG'], out['FTAG'] = hg, ag
    return out[valid].sort_values('Date_dt').reset_index(drop=True), info


def parse_csv(content):
    if not content or not content.strip():
        return pd.DataFrame(), {'status': 'empty_file', 'valid_rows': 0}
    try:
        frame = pd.read_csv(BytesIO(content))
    except (ValueError, UnicodeError):
        return pd.DataFrame(), {'status': 'parse_error', 'valid_rows': 0}
    missing = sorted(REQUIRED-set(frame.columns))
    if missing:
        return pd.DataFrame(), {'status': 'missing_columns', 'missing_columns': missing, 'columns': frame.columns.tolist(), 'valid_rows': 0}
    frame['Date_dt'] = parse_dates(frame['Date'])
    clean, info = validate_rows(frame)
    info['columns'] = frame.columns.tolist()
    if frame.empty:
        info['status'] = 'empty_file'
    elif not len(clean) and info['invalid_dates']:
        info['status'] = 'invalid_dates'
    elif not len(clean) and info['invalid_scores']:
        info['status'] = 'invalid_scores'
    return clean, info


def consolidate_history(frame, code, detail_limit=50):
    """Quarantine contradictory source fixtures before exact deduplication.

    Store audit evidence before reducing rows; never choose an arbitrary score.
    Season/date bounds here apply only to the five established July-season sources.
    """
    quality = dict(raw_rows=len(frame), invalid_season_rows=0, conflicting_rows=0,
                   duplicate_rows=0, accepted_rows=0, details=[], details_truncated=False)
    if frame.empty:
        return frame.copy(), quality
    out = frame.copy()
    def note(kind, rejected):
        for _, row in rejected.iterrows():
            if len(quality['details']) >= detail_limit:
                quality['details_truncated']=True
                break
            quality['details'].append(dict(reason=kind,date=str(row.get('Date_dt')),
                home=str(row.get('HomeTeam')),away=str(row.get('AwayTeam')),
                home_goals=float(row['FTHG']),away_goals=float(row['FTAG']),
                season=str(row.get('_history_season')),source_url=row.get('_history_source_url')))
    if code in {'E0','SP1','I1','D1','F1'}:
        valid=pd.Series(False,index=out.index)
        for season in out['_history_season'].astype(str).unique():
            if re.fullmatch(r'\d{4}',season) and int(season[2:])==(int(season[:2])+1)%100:
                begin=pd.Timestamp(year=2000+int(season[:2]),month=7,day=1)
                valid |= out['_history_season'].astype(str).eq(season) & out.Date_dt.ge(begin) & out.Date_dt.lt(begin+pd.DateOffset(years=1))
        quality['invalid_season_rows']=int((~valid).sum());note('date_season_mismatch',out[~valid]);out=out[valid].copy()
    keys=['Date_dt','HomeTeam','AwayTeam']
    if not out.empty:
        conflicts=out.groupby(keys,dropna=False)[['FTHG','FTAG']].transform('nunique').gt(1).any(axis=1)
        quality['conflicting_rows']=int(conflicts.sum());note('conflicting_score',out[conflicts]);out=out[~conflicts].copy()
        duplicate=out.duplicated(keys,keep='first')
        quality['duplicate_rows']=int(duplicate.sum());note('exact_duplicate',out[duplicate]);out=out[~duplicate].copy()
    quality['accepted_rows']=len(out)
    return out.sort_values('Date_dt').reset_index(drop=True),quality


def load_history(code, get, timeout=(3, 8), seasons=SEASONS):
    if not code:
        frame = pd.DataFrame()
        frame.attrs['history_diagnostics'] = {'status': 'unsupported_source', 'sources': []}
        return frame
    def download(season):
        url = f'https://www.football-data.co.uk/mmz4281/{season}/{code}.csv'
        report = {'season': season, 'csv_code': code, 'url': url, 'checked_at': datetime.now(timezone.utc).isoformat()}
        try:
            response = get(url, timeout=timeout)
            report.update(http_status=response.status_code, bytes=len(response.content), final_url=getattr(response, 'url', url), received_at=datetime.now(timezone.utc).isoformat())
            if response.status_code != 200:
                report.update(status='http_error', valid_rows=0)
                return pd.DataFrame(), report
            frame, parsed = parse_csv(response.content)
            frame['_history_season'] = season
            report['payload_sha256']=hashlib.sha256(response.content).hexdigest()
            frame['_history_source_url']=report['final_url'] if isinstance(report['final_url'],str) else url
            frame['_history_received_at']=report['received_at']
            frame['_history_payload_sha256']=report['payload_sha256']
            report.update(parsed)
            return frame, report
        except Exception as exc:
            report.update(status='download_error', error=type(exc).__name__, valid_rows=0)
            return pd.DataFrame(), report
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(download, seasons))
    frames = [frame for frame, _ in results if not frame.empty]
    combined=pd.concat(frames,ignore_index=True) if frames else pd.DataFrame()
    out,quality=consolidate_history(combined,code)
    reports = [report for _, report in results]
    out.attrs['history_diagnostics'] = {'status': 'ok' if not out.empty else ('conflicting_scores' if quality['conflicting_rows'] else 'invalid_season' if quality['invalid_season_rows'] else (reports[0]['status'] if reports and len({r['status'] for r in reports}) == 1 else 'empty_history')),
                                       'sources': reports, 'valid_rows': len(out),'quality_audit':quality}
    return out


def team_history(df, name, kickoff, venue=None):
    source = df.attrs.get('history_diagnostics', {}) if df is not None else {}
    report = {'input_name': str(name), 'matched_name': None, 'mapping': None, 'venue': venue,
              'source': source, 'status': 'empty_history'}
    def done(frame, status):
        report['status'], report['message'] = status, MESSAGES.get(status, status)
        return frame, report
    if df is None or df.empty:
        return done(pd.DataFrame(), source.get('status', 'empty_history'))
    hist, validation = validate_rows(df)
    report['validation'] = validation
    if hist.empty:
        status = 'invalid_dates' if validation.get('invalid_dates') else 'invalid_scores' if validation.get('invalid_scores') else validation['status']
        return done(hist, status)
    try:
        stamp = pd.Timestamp(kickoff)
        if pd.isna(stamp) or stamp.tzinfo is None:
            return done(pd.DataFrame(), 'invalid_kickoff')
        cutoff = stamp.tz_convert('UTC').tz_localize(None).normalize()
    except (ValueError, TypeError):
        return done(pd.DataFrame(), 'invalid_kickoff')
    names = sorted(set(hist['HomeTeam']) | set(hist['AwayTeam']))
    team, method = match_team(name, names)
    report.update(matched_name=team, mapping=method, cutoff_date=str(cutoff.date()))
    if method == 'verified_alias':
        evidence = alias_evidence(name, team)
        report['alias_source'] = evidence['csv_source'] if evidence else None
        report['alias_evidence'] = evidence
    if team is None:
        return done(pd.DataFrame(), method)
    hist = hist[hist['Date_dt'] < cutoff]
    games = hist[(hist['HomeTeam'] == team) | (hist['AwayTeam'] == team)]
    report['all_before_count'] = len(games)
    if venue == 'home':
        games = games[games['HomeTeam'] == team]
    elif venue == 'away':
        games = games[games['AwayTeam'] == team]
    report['venue_before_count'] = len(games)
    if not games.empty:
        report.update(first_date=str(games['Date_dt'].min().date()), last_date=str(games['Date_dt'].max().date()))
        report['sample5_dates'] = [str(day.date()) for day in games.tail(5)['Date_dt']]
        report['sample5_span_days'] = int((games.tail(5)['Date_dt'].max()-games.tail(5)['Date_dt'].min()).days)
        if '_history_season' in games:
            report['season_counts'] = {str(k): int(v) for k, v in games['_history_season'].value_counts().items()}
    return done(games, 'ok' if len(games) >= 5 else 'insufficient_history' if len(games) else 'no_history_before_kickoff')
