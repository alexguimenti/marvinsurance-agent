"""Claims manual section 14: "Every decision record holds the decision, the payout, the calculation, the section relied on,
a reason in plain language, who or what made the decision and when. For a payout above $1,000 it also holds who signed off
and when. A decision with no reason on file is treated as not yet made." What falls short is refused, with what is missing."""
from typing import Any

from sqlalchemy import Engine, select

from app.db import claims, payout_calculations

PREFIX = "Claims manual section 14: "


class Section14Refused(ValueError):
    """A decision record that falls short of section 14. The message names the section and says what is missing."""


def request_problem(body: Any) -> str | None:
    """What is wrong with the request itself, whatever the claim: a reason, a section or a maker that is blank, an
    approval that pays nothing, and a decline or a referral that pays something."""
    if not body.rationale.strip():
        return f"{PREFIX}the reason is blank."
    if not any(c.strip() for c in body.citations):
        return f"{PREFIX}the section relied on is blank."
    if not body.decided_by.strip():
        return f"{PREFIX}who made the decision is blank."
    if body.decision == "approve" and not body.payout > 0:
        return f"{PREFIX}an approval pays more than zero."
    if body.decision != "approve" and body.payout != 0:
        return f"{PREFIX}a decline or a referral pays nothing."
    return None


def calculation_on_file(engine: Engine, claim_id: str, payout: float) -> list[dict] | None:
    """The lines of the calculation on file for this attempt, if there is one and it comes to this payout. Raises
    Section14Refused saying why not."""
    with engine.connect() as conn:
        attempts = conn.execute(select(claims.c.attempts).where(claims.c.claim_id == claim_id)).scalar_one()
        row = conn.execute(select(payout_calculations).where(payout_calculations.c.claim_id == claim_id)).mappings().first()
    if row is None or row["attempt"] != attempts + 1:  # claims.attempts counts the failed ones
        raise Section14Refused(f"{PREFIX}an approval needs a payout calculation on file for this attempt. Call calculate_payout first.")
    if round(row["payout"], 2) != round(payout, 2):
        raise Section14Refused(f"{PREFIX}the payout ${payout:,.2f} does not match the calculation on file (${row['payout']:,.2f}).")
    import json

    return json.loads(row["steps"])
