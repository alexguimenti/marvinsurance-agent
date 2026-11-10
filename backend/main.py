"""The deployment's entry point: `backend.main.handler` on Lambda (behind a Function URL), `backend.main:app` on a web
server. The backend's own code imports `app` as a top-level package, so this puts the backend folder on the path first."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mangum import Mangum  # noqa: E402

from app.main import app  # noqa: E402,F401  (the web server's name for it)

# Lambda has no startup to hook: the app answers every request without its lifespan, and nothing seeds the database.
handler = Mangum(app, lifespan="off")
