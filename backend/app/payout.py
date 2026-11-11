from pydantic import BaseModel, Field, field_validator


class LineItem(BaseModel):
    description: str
    amount: float
    # A limit that does not apply is 0, not null: the Gateway cannot read "a number or null" and refuses every value
    item_limit: float = Field(description="The per-item limit that applies to this item, or 0 when none applies")

    @field_validator("item_limit")
    @classmethod
    def not_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("a limit is 0 (none) or more")
        return value


def calculate_payout(
    items: list[LineItem],
    third_party_paid: float,
    deductible: float,
    benefit_max: float,
    approval_limit: float,
) -> dict:
    """Policy wording section 10: eligible amounts, minus third parties, minus deductible, capped. A per-item limit or
    a benefit maximum of 0 means there is none."""
    steps: list[dict] = []
    total = 0.0
    for item in items:
        eligible = min(item.amount, item.item_limit) if item.item_limit else item.amount
        steps.append({"step": item.description, "amount": round(eligible, 2)})
        total += eligible
    if third_party_paid:
        steps.append({"step": "Less what a carrier or another party has paid", "amount": -round(third_party_paid, 2)})
    if deductible:
        steps.append({"step": "Less the deductible", "amount": -round(deductible, 2)})
    payout = total - third_party_paid - deductible
    if benefit_max and payout > benefit_max:
        steps.append({"step": "Reduced to the benefit maximum", "amount": round(benefit_max - payout, 2)})
        payout = benefit_max
    payout = round(max(payout, 0.0), 2)
    return {"payout": payout, "steps": steps, "approval_limit": approval_limit, "needs_sign_off": payout > approval_limit}
