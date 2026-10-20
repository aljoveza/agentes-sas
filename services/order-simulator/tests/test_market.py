import random
from decimal import Decimal

from order_simulator.market import CUSTOMERS, SCARCE, MarketPlanner, Product

CATALOG = {
    "LAPTOP-PRO": Product("LAPTOP-PRO", "Laptop", Decimal("3000"), 10),
    "MONITOR-27": Product("MONITOR-27", "Monitor", Decimal("450"), 25),
    "DOCK-USB-C": Product("DOCK-USB-C", "Dock", Decimal("180"), 2),
    "KEYBOARD-MX": Product("KEYBOARD-MX", "Keyboard", Decimal("120"), 60),
    "HEADSET-PRO": Product("HEADSET-PRO", "Headset", Decimal("200"), 30),
    "WEBCAM-4K": Product("WEBCAM-4K", "Webcam", Decimal("150"), 20),
}


def planner(seed: int = 7) -> MarketPlanner:
    return MarketPlanner({k: Product(**vars(v)) for k, v in CATALOG.items()}, random.Random(seed))


def test_same_seed_same_orders():
    first = [planner(42).plan(f"O-{i}") for i in range(10)]
    second = [planner(42).plan(f"O-{i}") for i in range(10)]
    assert [(o.kind, o.customer_id, o.lines, o.payment_amount) for o in first] == \
           [(o.kind, o.customer_id, o.lines, o.payment_amount) for o in second]


def test_normal_orders_fit_stock_and_credit():
    p = planner()
    for i in range(30):
        order = p.plan(f"O-{i}", "standard")
        assert all(line["productId"] != SCARCE for line in order.lines)
        assert order.subtotal <= CUSTOMERS[order.customer_id][1]
        assert order.payment_amount == order.subtotal
    assert all(left >= 0 for left in p.stock.values())


def test_discounts_split_by_authority():
    p = planner()
    within = {p.plan(f"A-{i}", "discount_within_authority").requested_discount for i in range(20)}
    ceo = {p.plan(f"B-{i}", "discount_needs_ceo").requested_discount for i in range(20)}
    assert max(within) <= 10 < min(ceo)


def test_failure_situations():
    p = planner()
    shortage = p.plan("S", "shortage")
    assert shortage.lines[0]["quantity"] > CATALOG[shortage.lines[0]["productId"]].available
    under = p.plan("U", "underpayment")
    assert Decimal(0) < under.payment_amount < under.subtotal
    credit = p.plan("C", "over_credit_limit")
    assert credit.customer_id == "CUST-GLOBEX" and credit.subtotal > CUSTOMERS["CUST-GLOBEX"][1]
    assert p.plan("D", "duplicate").repeat == 2


def test_payment_matches_requested_discount():
    order = planner().plan("X", "discount_needs_ceo")
    expected = (order.subtotal * (100 - order.requested_discount) / 100).quantize(Decimal("0.01"))
    assert order.payment_amount == expected
