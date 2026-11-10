"""What the agents are told. The backend shows this on the instructions page, so it stays free of agent machinery."""
from app.documents import documents_for
from app.reference import Reference


def claim_prompt(ref: Reference, instructions: str) -> str:
    """What the claim agent is told: its version's instructions, then the sections it can read."""
    return f"{instructions}\n\n{section_index(ref)}"


def section_index(ref: Reference) -> str:
    """The numbered sections of both documents, so the agent can go straight to the one it needs."""
    docs = documents_for(ref)
    lines = ["## Sections you can read with read_section"]
    for document, label in (("policy_wording", "Policy wording"), ("claims_manual", "Claims manual")):
        lines.append(f"{label}: " + "; ".join(f"{s.number}. {s.title}" for s in docs.sections(document)))
    return "\n".join(lines)
