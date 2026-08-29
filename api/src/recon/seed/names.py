"""Deterministic name pools for the property-management dataset.

Combinatorial rather than a long literal list, so the pools are large enough
that Tier 1's "unique counterparty" rule is genuinely tested and the duplicate
amount cases are genuinely ambiguous.
"""

from __future__ import annotations

SURNAMES = [
    "Morrison",
    "Okafor",
    "Chaudhry",
    "Delgado",
    "Whitfield",
    "Nakamura",
    "Brennan",
    "Aleman",
    "Kowalski",
    "Fitzgerald",
    "Osei",
    "Vasquez",
    "Lindqvist",
    "Haddad",
    "Petrov",
    "Ibarra",
    "Sandoval",
    "Mbeki",
    "Rourke",
    "Castellanos",
    "Novak",
    "Ferreira",
    "Ahmadi",
    "Bergstrom",
    "Duval",
    "Ngata",
    "Salinas",
    "Thornton",
    "Rasmussen",
    "Oyelaran",
    "Marchetti",
    "Kaplan",
    "Devereux",
    "Sokolova",
    "Achterberg",
    "Bianchi",
    "Cardoso",
    "Eriksen",
    "Fontaine",
    "Gutierrez",
    "Halvorsen",
    "Iqbal",
    "Jankowski",
    "Kirchner",
    "Laurent",
    "Mensah",
    "Nordstrom",
    "Ovalle",
    "Pankhurst",
    "Quintero",
    "Rosales",
    "Stavros",
    "Tremblay",
    "Ustinov",
    "Villanueva",
    "Wojcik",
    "Yamashita",
    "Zeleny",
]

INITIALS = list("ABCDEHJKLMNPRSTVW")

BUILDINGS = [
    ("Willow Court", "WLC"),
    ("Cedar Ridge", "CDR"),
    ("Harbor Point", "HBP"),
    ("Ash Grove", "ASG"),
    ("Kestrel House", "KSH"),
    ("Lantern Yard", "LTY"),
    ("Marlowe Place", "MRP"),
    ("Northgate Mews", "NGM"),
]

UNIT_LETTERS = list("ABCD")

SUPPLIERS = [
    "Northside Plumbing",
    "Cascade Electrical",
    "Vertex Elevator Service",
    "Brightline Cleaning",
    "Ironwood Landscaping",
    "Sentinel Fire Protection",
    "Copperfield HVAC",
    "Redstone Roofing",
    "Halcyon Pest Control",
    "Bluepoint Waste Services",
    "Grantham Glazing",
    "Meridian Security Systems",
    "Ashford Painting",
    "Lockridge Locksmiths",
    "Thames Valley Insurance",
    "Pinnacle Property Insurance",
    "Metro Water Utility",
    "Granite Power Co",
    "Foundry Gas Supply",
    "Clearview Window Cleaning",
    "Saltmarsh Groundworks",
    "Kingsway Surveyors",
    "Draycott Legal",
    "Ellerslie Accountancy",
]

# The four corporate tenants behind the feedback-loop case. Their payments
# arrive via a processor, so the bank narrative names the processor and not
# them -- unreachable by Tiers 0-1 until a human resolves one and the pair is
# written back to retrieval. See NOTES.md 0.4e.
PROCESSOR_TENANTS = [
    ("Cedarbrook Holdings LLC", "774120"),
    ("Alderman Retail Group", "774355"),
    ("Quayside Ventures Ltd", "774891"),
    ("Ravensworth Partners", "775003"),
]

PROCESSOR_NAME = "PAYCLEAR SETTLEMENT"


def tenant_pool(count: int) -> list[str]:
    """Stable, ordered pool of `count` distinct tenant names."""
    out: list[str] = []
    for i in range(count):
        initial = INITIALS[i % len(INITIALS)]
        surname = SURNAMES[(i // len(INITIALS)) % len(SURNAMES)]
        suffix = i // (len(INITIALS) * len(SURNAMES))
        name = f"{initial} {surname.upper()}"
        out.append(f"{name} {suffix + 1}" if suffix else name)
    return out


def unit_pool(count: int) -> list[tuple[str, str]]:
    """Stable pool of (display unit, building code) pairs."""
    out: list[tuple[str, str]] = []
    i = 0
    while len(out) < count:
        building, code = BUILDINGS[i % len(BUILDINGS)]
        floor = (i // len(BUILDINGS)) % 12 + 1
        letter = UNIT_LETTERS[(i // (len(BUILDINGS) * 12)) % len(UNIT_LETTERS)]
        out.append((f"Unit {floor}{letter} {building}", code))
        i += 1
    return out
