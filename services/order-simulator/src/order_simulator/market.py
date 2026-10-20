"""A random but replayable market: a stream of varied orders, so the demo doesn't look scripted.

Each run draws a seed (printed); `--seed` replays the exact same orders. The planner is pure (catalog
snapshot + RNG in, orders out) and keeps its own running stock so a run never accidentally sells
what an earlier order in the same run already took.
"""

import random
from dataclasses import dataclass, field
from decimal import Decimal

CENTS = Decimal("0.01")

# What the storefront knows about its customers (mirrors the seed data).
CUSTOMERS = {
    "CUST-ACME": ("Acme Retail", Decimal("50000")),
    "CUST-UMBRELLA": ("Umbrella Labs", Decimal("30000")),
    "CUST-STARK": ("Stark Industries", Decimal("100000")),
    "CUST-HOOLI": ("Hooli", Decimal("8000")),
    "CUST-GLOBEX": ("Globex Startup", Decimal("5000")),
}
PRODUCT_IDS = ["LAPTOP-PRO", "MONITOR-27", "DOCK-USB-C", "KEYBOARD-MX", "HEADSET-PRO", "WEBCAM-4K"]
SCARCE = "DOCK-USB-C"  # kept for shortages (and scenario 03)

# Situation -> weight. Discounts above 10% need the CEO; the CEO approves only if margin stays positive.
KINDS = {
    "standard": 45,
    "discount_within_authority": 15,
    "discount_needs_ceo": 15,
    "shortage": 8,
    "underpayment": 7,
    "over_credit_limit": 5,
    "duplicate": 5,
}


@dataclass
class Product:
    product_id: str
    name: str
    price: Decimal
    available: int


@dataclass
class PlannedOrder:
    kind: str
    order_id: str
    customer_id: str
    lines: list[dict]
    subtotal: Decimal
    requested_discount: Decimal = Decimal(0)
    payment_amount: Decimal = Decimal(0)
    repeat: int = 1
    notes: list[str] = field(default_factory=list)

    def arguments(self) -> dict:
        return {
            "order_id": self.order_id,
            "customer_id": self.customer_id,
            "lines": self.lines,
            "payment_reference": f"PAY-{self.order_id}",
            "payment_amount": str(self.payment_amount),
            "requested_discount_percent": str(self.requested_discount),
        }

    def describe(self, catalog: dict[str, Product]) -> str:
        items = ", ".join(f"{l['quantity']}× {catalog[l['productId']].name}" for l in self.lines)
        customer = CUSTOMERS.get(self.customer_id, (self.customer_id,))[0]
        ask = f" · asks {self.requested_discount}% off" if self.requested_discount else ""
        return f"{customer} · {items} · {self.subtotal}{ask} · pays {self.payment_amount}"


def _paid(subtotal: Decimal, discount: Decimal) -> Decimal:
    return (subtotal * (100 - discount) / 100).quantize(CENTS)


class MarketPlanner:
    def __init__(self, catalog: dict[str, Product], rng: random.Random) -> None:
        self.catalog = catalog
        self.rng = rng
        self.stock = {pid: p.available for pid, p in catalog.items()}

    def plan(self, order_id: str, kind: str | None = None) -> PlannedOrder:
        kind = kind or self.rng.choices(list(KINDS), weights=list(KINDS.values()))[0]
        order = getattr(self, f"_{kind}")(order_id)
        return order if order else self._standard(order_id)

    def _subtotal(self, lines: list[dict]) -> Decimal:
        return sum((self.catalog[l["productId"]].price * l["quantity"] for l in lines), Decimal(0))

    def _take(self, lines: list[dict]) -> None:
        for line in lines:
            self.stock[line["productId"]] -= line["quantity"]

    def _basket(self, max_products: int = 2) -> list[dict]:
        candidates = [pid for pid, left in self.stock.items() if left > 0 and pid != SCARCE]
        if not candidates:
            return []
        chosen = self.rng.sample(candidates, k=min(len(candidates), self.rng.randint(1, max_products)))
        return [{"productId": pid, "quantity": self.rng.randint(1, min(4, self.stock[pid]))} for pid in chosen]

    def _customer_for(self, subtotal: Decimal) -> str:
        able = [cid for cid, (_, limit) in CUSTOMERS.items() if subtotal <= limit and cid != "CUST-GLOBEX"]
        return self.rng.choice(able or ["CUST-STARK"])

    def _priced(self, kind: str, order_id: str, discount: Decimal = Decimal(0)) -> PlannedOrder | None:
        lines = self._basket()
        if not lines:
            return None
        self._take(lines)
        subtotal = self._subtotal(lines)
        return PlannedOrder(kind, order_id, self._customer_for(subtotal), lines, subtotal, discount,
                            _paid(subtotal, discount))

    def _standard(self, order_id: str) -> PlannedOrder:
        order = self._priced("standard", order_id)
        if order is None:
            raise RuntimeError("Out of stock for every product: reseed the catalog (company-mcp-seed).")
        return order

    def _discount_within_authority(self, order_id: str) -> PlannedOrder | None:
        return self._priced("discount_within_authority", order_id, Decimal(self.rng.choice([5, 8, 10])))

    def _discount_needs_ceo(self, order_id: str) -> PlannedOrder | None:
        return self._priced("discount_needs_ceo", order_id, Decimal(self.rng.choice([15, 20, 25, 30])))

    def _underpayment(self, order_id: str) -> PlannedOrder | None:
        order = self._priced("underpayment", order_id)
        if order:
            short = min(order.payment_amount - CENTS, Decimal(self.rng.choice([10, 50, 100])))
            order.payment_amount -= short
            order.notes.append(f"underpays by {short}")
        return order

    def _duplicate(self, order_id: str) -> PlannedOrder | None:
        order = self._priced("duplicate", order_id)
        if order:
            order.repeat = 2
            order.notes.append("submitted twice")
        return order

    def _shortage(self, order_id: str) -> PlannedOrder:
        # Ask for more than is left of the scarcest product; nothing is taken from stock.
        pid = min((p for p in self.stock if self.stock[p] >= 0), key=lambda p: (p != SCARCE, self.stock[p]))
        lines = [{"productId": pid, "quantity": max(1, self.stock[pid]) + self.rng.randint(1, 3)}]
        subtotal = self._subtotal(lines)
        return PlannedOrder("shortage", order_id, self._customer_for(subtotal), lines, subtotal,
                            payment_amount=subtotal, notes=[f"only {self.stock[pid]} in stock"])

    def _over_credit_limit(self, order_id: str) -> PlannedOrder | None:
        limit = CUSTOMERS["CUST-GLOBEX"][1]
        for pid in sorted(self.stock, key=lambda p: -self.catalog[p].price):
            price, left = self.catalog[pid].price, self.stock[pid]
            quantity = int(limit // price) + 1
            if pid != SCARCE and quantity <= left:
                lines = [{"productId": pid, "quantity": quantity}]
                subtotal = self._subtotal(lines)
                return PlannedOrder("over_credit_limit", order_id, "CUST-GLOBEX", lines, subtotal,
                                    payment_amount=subtotal, notes=[f"credit limit {limit}"])
        return None
