"""Start Flow using the Python runtime included with this Windows package."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "app"))
from idm.desktop import main

raise SystemExit(main())
