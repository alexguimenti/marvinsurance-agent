from app.payout import LineItem, calculate_payout

LIMIT = 1000.0


def item(description: str, amount: float, item_limit: float | None = None) -> LineItem:
    return LineItem(description=description, amount=amount, item_limit=item_limit)


def test_payout_per_item_limit_carrier_and_deductible():
    items = [item(f"item {i}", a, 300.0) for i, a in enumerate([280, 300, 160, 110, 60, 120])]
    result = calculate_payout(items, 400.0, 50.0, None, LIMIT)
    assert result["payout"] == 580.0
    assert len(result["steps"]) == 8
    assert result["steps"][-1]["amount"] == -50.0


def test_payout_set_counts_as_one_item():
    items = [
        item("Suitcase", 260, 300),
        item("Tuxedo", 450, 300),
        item("Dress shoes", 230, 300),
        item("Camera with its lens", 700, 300),
        item("Cufflinks", 120, 300),
        *[item(f"Shirt {i}", 85, 300) for i in range(4)],
    ]
    result = calculate_payout(items, 350.0, 50.0, None, LIMIT)
    assert result["payout"] == 1150.0
    camera = next(s for s in result["steps"] if "Camera" in s["step"])
    assert camera["amount"] == 300.0


def test_payout_medical_deductible_only():
    result = calculate_payout([item("Hospital bill", 3800)], 0.0, 50.0, None, LIMIT)
    assert result["payout"] == 3750.0


def test_payout_cap_and_floor():
    capped = calculate_payout([item("x", 2000)], 0.0, 0.0, 1500.0, LIMIT)
    assert capped["payout"] == 1500.0
    assert "maximum" in capped["steps"][-1]["step"].lower()
    floored = calculate_payout([item("x", 100)], 0.0, 150.0, None, LIMIT)
    assert floored["payout"] == 0.0


def test_payout_says_whether_it_needs_a_sign_off():
    over = calculate_payout([item("a", 1500)], 0, 0, None, approval_limit=1000)
    assert over["payout"] == 1500 and over["needs_sign_off"] is True and over["approval_limit"] == 1000


def test_payout_at_the_limit_needs_no_sign_off():
    assert calculate_payout([item("a", 1000)], 0, 0, None, 1000)["needs_sign_off"] is False
