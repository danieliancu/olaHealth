"""Model prices in USD per 1M tokens, used to count AI spend against the monthly budget."""

from __future__ import annotations

from decimal import Decimal

PRICES: dict[str, tuple[Decimal, Decimal]] = {
    # model: (input, output)
    "claude-haiku-5-5": (Decimal("0.10"), Decimal("0.50")),
    # Approximate OpenAI price, to be verified against the current OpenAI price list.
    "gpt-5-nano": (Decimal("0.05"), Decimal("0.40")),
}
# Unknown models are priced conservatively so the budget errs on the safe side.
DEFAULT_PRICE = (Decimal("1.00"), Decimal("5.00"))
MILLION = Decimal(1_000_000)
SIX_PLACES = Decimal("0.000001")


def price_for(model: str) -> tuple[Decimal, Decimal]:
    if model in PRICES:
        return PRICES[model]
    # Dated snapshots such as "gpt-5-nano-2025-08-07" share the base model's price.
    for name, price in PRICES.items():
        if model.startswith(name + "-"):
            return price
    return DEFAULT_PRICE


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    price_in, price_out = price_for(model)
    cost = (price_in * input_tokens + price_out * output_tokens) / MILLION
    return cost.quantize(SIX_PLACES)
