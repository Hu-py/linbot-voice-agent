"""Validate source seeds, then copy into the Python package for installed demos."""
import json
import shutil
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from linbot_voice_agent.simulator import SyntheticEnvironment
for profile in json.loads((ROOT/'seeds/patient_profiles.json').read_text())['profiles']:
    SyntheticEnvironment(ROOT/'seeds',profile['id'])
for source in (ROOT/'seeds').glob('*.json'):
    shutil.copy2(source,ROOT/'linbot_voice_agent/data/seeds'/source.name)
print('Seeds validated and package copies synchronized.')
