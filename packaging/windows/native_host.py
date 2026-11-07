from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent / "app"))
from idm.chrome_host import main
raise SystemExit(main())
