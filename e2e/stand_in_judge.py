"""A stand-in for the judge's endpoint (OpenRouter's Decisions API for Jev) on port 8005, so the end-to-end tests never
call the real one. It grades every explanation B, with the same grounds and a different section."""
from fastapi import FastAPI, Request

app = FastAPI(title="Stand-in for the judge")


@app.post("/api/alpha/decisions")
async def decisions(request: Request):
    """Answer the three questions the way Jev's response is shaped: a score with its probabilities, and two noul answers."""
    body = await request.json()
    answers = {}
    for name, question in body["questions"].items():
        if question["type"] == "score":
            answers[name] = {"type": "score", "score": 2.1, "probabilities": {"0": 0.02, "1": 0.08, "2": 0.7, "3": 0.2}}
        else:
            answers[name] = {"type": "noul", "noul": 0.9 if name == "same_grounds" else 0.1}
    return {"id": "stand-in", "model": body["model"], "provider": "stand-in", "answers": answers,
            "usage": {"input_tokens": 100, "output_tokens": 0, "cost": 0.0001}}


@app.get("/health")
def health():
    """Say that the stand-in is up."""
    return {"status": "ok"}
