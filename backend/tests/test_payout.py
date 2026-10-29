from app.payout import LineItem, calculate_payout


def test_payout_per_item_limit_carrier_and_deductible():
    items = [LineItem(description=f"item {i}", amount=a, item_limit=300.0)
             for i, a in enumerate([280, 300, 160, 110, 60, 120])]
    result = calculate_payout(items, third_party_paid=400.0, deductible=50.0)
    assert result["payout"] == 580.0
    assert len(result["steps"]) == 8
    assert result["steps"][-1]["amount"] == -50.0


def test_payout_set_counts_as_one_item():
    items = [
        LineItem(description="Suitcase", amount=260, item_limit=300),
        LineItem(description="Tuxedo", amount=450, item_limit=300),
        LineItem(description="Dress shoes", amount=230, item_limit=300),
        LineItem(description="Camera with its lens", amount=700, item_limit=300),
        LineItem(description="Cufflinks", amount=120, item_limit=300),
        *[LineItem(description=f"Shirt {i}", amount=85, item_limit=300) for i in range(4)],
    ]
    result = calculate_payout(items, third_party_paid=350.0, deductible=50.0)
    assert result["payout"] == 1150.0
    camera = next(s for s in result["steps"] if "Camera" in s["step"])
    assert camera["amount"] == 300.0


def test_payout_medical_deductible_only():
    result = calculate_payout([LineItem(description="Hospital bill", amount=3800)], deductible=50.0)
    assert result["payout"] == 3750.0


def test_payout_cap_and_floor():
    capped = calculate_payout([LineItem(description="x", amount=2000)], benefit_max=1500.0)
    assert capped["payout"] == 1500.0
    assert "maximum" in capped["steps"][-1]["step"].lower()
    floored = calculate_payout([LineItem(description="x", amount=100)], deductible=150.0)
    assert floored["payout"] == 0.0
