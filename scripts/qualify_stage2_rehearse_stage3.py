#!/usr/bin/env python3
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from core.orchestration.stage2_qualification import qualify,rehearse
q=qualify(ROOT); r=rehearse(ROOT,q); print(json.dumps({"qualification":q,"rehearsal":r},indent=2,sort_keys=True))
