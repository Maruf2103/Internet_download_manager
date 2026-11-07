"""Run the real test suite and publish concise CI failure annotations."""
import os
from pathlib import Path
import sys
import unittest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'))
result = unittest.TextTestRunner(verbosity=2).run(suite)
if os.environ.get('GITHUB_ACTIONS') == 'true':
    for test, detail in result.failures + result.errors:
        message = detail[-7000:].replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
        print(f'::error title={test.id()}::{message}', flush=True)
if not result.testsRun:
    print('No tests were discovered.', file=sys.stderr)
raise SystemExit(0 if result.testsRun and result.wasSuccessful() else 1)
