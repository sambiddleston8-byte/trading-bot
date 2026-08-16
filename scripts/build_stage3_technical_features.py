#!/usr/bin/env python3
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from core.features.pit_feature_contract import build_technical_feature_matrices, campaign_observation_cutoff
from core.orchestration.stage2_qualification import _sessions
parser=argparse.ArgumentParser()
parser.add_argument("--retrieved-at",required=True,help="explicit timezone-aware instant when admitted inputs were retrieved for derivation")
arguments=parser.parse_args()
cutoffs={role:{day:campaign_observation_cutoff(day) for day in _sessions(start,end)} for role,start,end in (("TRAIN","2024-10-01","2025-02-28"),("VALIDATION","2025-03-01","2025-04-30"))}
print(json.dumps(build_technical_feature_matrices(ROOT,retrieved_at=arguments.retrieved_at,observation_cutoffs=cutoffs,qualification_report_artifact_sha256="dcfc62e0fa9d8747a1d5852021b65f32fc07595046ce2b922382cb771582982d"),indent=2,sort_keys=True))
