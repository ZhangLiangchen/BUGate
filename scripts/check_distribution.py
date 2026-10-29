#!/usr/bin/env python3
"""Verify current source and optional built release against the finite v2 surface."""
import argparse
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packaging'))
from distribution import check_source, check_bundle, resource_files
parser = argparse.ArgumentParser()
parser.add_argument('--bundle', type=Path)
args = parser.parse_args()
result = {'source': check_source(ROOT)}
if args.bundle:
    result['bundle'] = check_bundle(args.bundle, resource_files(ROOT))
print(json.dumps(result, indent=2))
