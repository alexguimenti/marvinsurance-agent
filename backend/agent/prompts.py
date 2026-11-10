"""What the agents are told. The backend shows this on the instructions page, so it stays free of agent machinery."""

LABELS = {"policy_wording": "Policy wording", "claims_manual": "Claims manual"}


def claim_prompt(instructions: str, index: str) -> str:
    """What the claim agent is told: its version's instructions, then the sections it can read."""
    return f"{instructions}\n\n{index}"


def section_index(sections: dict[str, list[dict]]) -> str:
    """The numbered sections of both documents, so the agent can go straight to the one it needs. `sections` is each
    document's list of `{"section", "title"}`, as the app gives it."""
    lines = ["## Sections you can read with read_section"]
    for document, label in LABELS.items():
        lines.append(f"{label}: " + "; ".join(f"{s['section']}. {s['title']}" for s in sections[document]))
    return "\n".join(lines)
