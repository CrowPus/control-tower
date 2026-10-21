"""Write the admin API contract to admin/openapi.json (`npm run api`).

The contract is the product's, not one install's: it is written the same whatever the install's settings (its company
name, for one), so CI can check that the committed copy matches the routes.
"""

import json
import os
from pathlib import Path

from .main import app

if __name__ == "__main__":
    default = Path(__file__).resolve().parents[2] / "admin" / "openapi.json"
    destination = Path(os.environ.get("CT_ADMIN_OPENAPI_OUT") or default)
    contract = app.openapi()
    contract["info"]["title"] = "Control Tower API"
    destination.write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n")
