"""Serve the built single-page frontend from the API process (production image).

Vite serves it in development; in the container `FRONTEND_DIST=/app/dist` and the
API answers everything that is not `/api/…`: hashed assets are immutable, every
other path falls back to `index.html` so client-side routes deep-link, and an
unknown `/api/…` path stays a JSON 404.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope


class SpaStaticFiles(StaticFiles):
    # The API prefix this app serves; its unknown paths stay JSON 404s.
    api_prefix = "api"

    async def get_response(self, path: str, scope: Scope) -> Response:
        prefixes = ("api", "admin-api", self.api_prefix)
        if any(path == p or path.startswith(f"{p}/") for p in prefixes):
            raise HTTPException(status_code=404)
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            path = "index.html"
            response = await super().get_response(path, scope)
        # iOS universal links: the association file has no extension but must be JSON.
        if path == ".well-known/apple-app-site-association":
            response.headers["Content-Type"] = "application/json"
        # Vite names bundles by content hash; index.html must always be re-validated.
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable" if path.startswith("assets/") else "no-cache"
        return response


def mount_frontend(app: FastAPI, dist: str, *, api_prefix: str = "api") -> bool:
    directory = Path(dist).resolve() if dist else None
    if directory is None or not (directory / "index.html").is_file():
        return False
    static = SpaStaticFiles(directory=str(directory), html=True)
    static.api_prefix = api_prefix
    app.mount("/", static, name="frontend")
    return True
