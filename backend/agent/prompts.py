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


def send_back_message(note: str, handler_name: str) -> str:
    """What the claim agent is told when a senior handler sends its decision back. It is part of the agent's code, not its
    instructions, so a new version of the instructions cannot drop how to treat the note."""
    return (f"{handler_name}, a senior claims handler, sent your decision back for review with this note: \"{note}\". "
            "Treat it as a senior handler's review of the decision: check what it asks against the policy wording and the claims "
            "manual like everything else. If the wording does not support what the note asks, refer the claim to a handler and "
            "give the reason. Record a new decision.")
