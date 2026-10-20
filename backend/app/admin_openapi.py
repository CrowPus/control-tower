"""Write the admin API contract to admin/openapi.json (`npm run admin:api`)."""

import json
import os
from pathlib import Path

from .main import app

if __name__ == "__main__":
    default = Path(__file__).resolve().parents[2] / "admin" / "openapi.json"
    destination = Path(os.environ.get("ZELYQ_ADMIN_OPENAPI_OUT") or default)
    destination.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n")
