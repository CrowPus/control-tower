"""Where the worker proves it is alive. Imports nothing from the app, so the
container's healthcheck (`python -m app.worker --check`) answers in milliseconds
instead of spending seconds loading every agent first."""

import os
from pathlib import Path

HEARTBEAT = Path(os.environ.get("WORKER_HEARTBEAT_FILE", "/tmp/worker-heartbeat"))  # noqa: S108 - overridable, container-local
