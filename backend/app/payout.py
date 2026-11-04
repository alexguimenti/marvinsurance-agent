from pydantic import BaseModel


class LineItem(BaseModel):
    description: str
    amount: float
    item_limit: float | None


def calculate_payout(
    items: list[LineItem],
    third_party_paid: float,
    deductible: float,
    benefit_max: float | None,
    approval_limit: float,
) -> dict:
    """Policy wording section 10: eligible amounts, minus third parties, minus deductible, capped."""
    steps: list[dict] = []
    total = 0.0
    for item in items:
        eligible = item.amount if item.item_limit is None else min(item.amount, item.item_limit)
        steps.append({"step": item.description, "amount": round(eligible, 2)})
        total += eligible
    if third_party_paid:
        steps.append({"step": "Less what a carrier or another party has paid", "amount": -round(third_party_paid, 2)})
    if deductible:
        steps.append({"step": "Less the deductible", "amount": -round(deductible, 2)})
    payout = total - third_party_paid - deductible
    if benefit_max is not None and payout > benefit_max:
        steps.append({"step": "Reduced to the benefit maximum", "amount": round(benefit_max - payout, 2)})
        payout = benefit_max
    payout = round(max(payout, 0.0), 2)
    return {"payout": payout, "steps": steps, "approval_limit": approval_limit, "needs_sign_off": payout > approval_limit}
