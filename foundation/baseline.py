"""Load pinned pure app functions by AST, never execute app/module-level DB/UI."""
import ast
import hashlib
import json
import math
from datetime import timezone
from pathlib import Path

import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
FUNCTIONS=('map_team','estimate_goals','poisson_probs','score_matrix','split_line','settle_leg',
           'evaluate_market_poisson','settle_pick')


def load_baseline():
    manifest=json.loads(Path(__file__).with_name('baseline_manifest.json').read_text())
    for name,expected in manifest['protected_files'].items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=expected:
            raise ValueError('protected UI/model source drift: '+name)
    source=(ROOT/'app.py').read_text()
    nodes={n.name:n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef)}
    selected=[nodes[name] for name in FUNCTIONS]
    canonical='\n'.join(ast.get_source_segment(source,n) for n in selected)
    model_hash=hashlib.sha256(canonical.encode()).hexdigest()
    if model_hash!=manifest['baseline_functions_sha256']:
        raise ValueError('baseline function hash mismatch')
    ns=dict(pd=pd,math=math,UTC=timezone.utc,MAX_XG_AGE_DAYS=120)
    exec(compile(ast.Module(body=selected,type_ignores=[]),str(ROOT/'app.py'),'exec'),ns)
    return ns,model_hash
