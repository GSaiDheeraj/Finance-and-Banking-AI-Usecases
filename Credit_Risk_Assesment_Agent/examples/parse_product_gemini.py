"""Gemini product-extraction example (prompt | GeminiChat | JsonOutputParser).

Mirrors the reference snippet: extracts name/price/features from a prose
description into guaranteed JSON. Run with GEMINI_API_KEY set:

    set GEMINI_API_KEY=...        (Windows)
    export GEMINI_API_KEY=...     (bash)
    python examples/parse_product_gemini.py
"""

import json
import sys

sys.path.insert(0, ".")

from credit_risk.config import get_product_llm
from credit_risk.llm_json import build_product_chain

DESCRIPTION = """The Kees Van Der Westen Speedster is a high-end, single-group espresso
machine known for its precision, performance, and industrial design. Handcrafted in
the Netherlands, it features dual boilers for brewing and steaming, PID temperature
control for consistency, and a unique pre-infusion system to enhance flavor
extraction. Designed for enthusiasts and professionals, it offers customizable
aesthetics, exceptional thermal stability, and intuitive operation via a lever
system. The pricing is approximately $14,499 depending on the retailer and
customization options."""


def parse_product(description: str) -> dict:
    """Extract product details from prose via the Gemini JSON chain."""
    if not description or not description.strip():
        raise ValueError("description must be a non-empty string.")
    chain = build_product_chain(get_product_llm(temperature=0.7))
    result = chain.invoke({"input": description})
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    parse_product(DESCRIPTION)
