"""Part of `npm run check`: the Copilot answers its health check through the file Runtime starts (`runtime/copilot.py`), with only the
`runtime` dependency group installed. Run from the staged package (runtime/ and agent/ side by side), as the deployment packs it. The
checkpoints go to a temporary folder, so nothing is left behind."""
import os
import sys
import tempfile

sys.path.insert(0, os.getcwd())  # the staged package: a script puts its own folder on the path, not this one

with tempfile.TemporaryDirectory() as folder:
    os.environ["DB_PATH"] = os.path.join(folder, "copilot.db")
    for name in ("DATABASE_URL", "UNDERCOVER_COPILOT_MEMORY_ID", "UNDERCOVER_GATEWAY_URL"):
        os.environ.pop(name, None)
    from starlette.testclient import TestClient

    from runtime.copilot import app

    with TestClient(app) as client:
        answer = client.post("/invocations", json={"operation": "health"})
    if answer.status_code != 200 or answer.json() != {"healthy": True}:
        sys.exit(f"the Copilot's health check answered {answer.status_code}: {answer.text}")
    print("the Copilot answers its health check through runtime/copilot.py")
