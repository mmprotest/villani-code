import json, sys
from pathlib import Path
print(json.loads(Path(sys.argv[1]).read_text()))
