"""Premium regression sampler.

Computes a fixed matrix of quotes through calculate_premium() and prints them as
JSON, so figures can be diffed before/after a rate or product change.

    python backend/tests/premium_regression.py > before.json
    # ...make changes...
    python backend/tests/premium_regression.py > after.json
    diff before.json after.json

Rows flagged `enforce_catalog: False` bypass the INSURER_PRODUCTS guard, so they
keep reporting a figure even if the product is withdrawn from that insurer's
catalog — useful for confirming that withdrawing a product leaves its rate
tables intact.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from premium_calc import calculate_premium, available_periods  # noqa: E402

# (company, product, cover, certificate, value, seats, tonnage, pax, sub_type)
SAMPLE = [
    # ── Monarch ──────────────────────────────────────────────────────────────
    ('monarch', 'private', 'third_party_only', 'annual', 0, 5, 0, 0, None),
    ('monarch', 'private', 'comprehensive', 'annual', 1_200_000, 5, 0, 0, None),
    ('monarch', 'private', 'third_party_only', 'inst_2', 0, 5, 0, 0, None),
    ('monarch', 'motorcycle', 'comprehensive', 'annual', 150_000, 2, 0, 0, None),
    ('monarch', 'motorcycle', 'third_party_only', 'annual', 0, 2, 0, 0, None),
    ('monarch', 'motorcycle_psv', 'comprehensive', 'annual', 150_000, 2, 0, 0, None),
    ('monarch', 'commercial_own_goods', 'third_party_only', 'annual', 0, 3, 4, 0, None),
    ('monarch', 'general_cartage', 'comprehensive', 'annual', 2_500_000, 3, 6, 0, None),
    ('monarch', 'institutional', 'third_party_only', 'annual', 0, 14, 0, 0, None),
    ('monarch', 'psv', 'third_party_only', 'annual', 0, 14, 0, 0, None),
    ('monarch', 'tuktuk_commercial', 'third_party_only', 'annual', 0, 2, 0, 0, None),
    ('monarch', 'tuktuk_psv', 'third_party_only', 'annual', 0, 2, 0, 0, None),

    # ── Definite ─────────────────────────────────────────────────────────────
    ('definite', 'private', 'comprehensive', 'annual', 1_200_000, 5, 0, 0, None),
    ('definite', 'private', 'third_party_only', 'annual', 0, 5, 0, 0, None),
    ('definite', 'private', 'comprehensive', 'inst_2', 1_200_000, 5, 0, 0, None),
    ('definite', 'private_fleet', 'comprehensive', 'annual', 1_200_000, 5, 0, 0, None),
    ('definite', 'commercial_hybrid', 'comprehensive', 'annual', 2_500_000, 3, 2, 0, 'own_goods'),
    ('definite', 'commercial_hybrid', 'third_party_only', 'annual', 0, 3, 5, 0, 'general_cartage'),
    ('definite', 'institutional', 'third_party_only', 'annual', 0, 30, 0, 0, None),
    ('definite', 'psv', 'third_party_only', 'inst_2', 0, 14, 0, 0, None),
    ('definite', 'tuktuk_commercial', 'third_party_only', 'annual', 0, 2, 0, 0, None),
    ('definite', 'tuktuk_psv', 'third_party_only', 'annual', 0, 2, 0, 0, None),

    # ── Directline ───────────────────────────────────────────────────────────
    ('directline', 'private', 'comprehensive', 'annual', 1_200_000, 5, 0, 0, None),
    ('directline', 'private', 'third_party_only', 'annual', 0, 5, 0, 0, None),
    ('directline', 'private', 'third_party_only', 'inst_2', 0, 5, 0, 0, None),
    ('directline', 'motorcycle', 'third_party_only', 'annual', 0, 2, 0, 0, None),
    ('directline', 'motorcycle_psv', 'third_party_only', 'annual', 0, 2, 0, 0, None),
    ('directline', 'commercial_own_goods', 'third_party_only', 'annual', 0, 3, 2, 0, None),
    ('directline', 'general_cartage', 'third_party_only', 'annual', 0, 3, 12, 0, None),
    ('directline', 'psv', 'third_party_only', 'annual', 0, 14, 0, 0, None),
]

# Products withdrawn from an insurer's catalog still get priced here, so the
# rate tables can be confirmed untouched after a catalog-only change.
NO_CATALOG_GUARD = {
    ('monarch', 'tuktuk_commercial'),
    ('monarch', 'tuktuk_psv'),
    ('definite', 'tuktuk_commercial'),
    ('definite', 'tuktuk_psv'),
}


def main():
    rows = []
    for company, product, cover, certificate, value, seats, tonnage, pax, sub_type in SAMPLE:
        key = {
            'company': company,
            'product': product,
            'cover': cover,
            'certificate': certificate,
            'value': value,
            'seats': seats,
            'tonnage': tonnage,
            'pax': pax,
            'sub_type': sub_type,
        }
        try:
            result = calculate_premium(
                cover, product, value, certificate,
                seats=seats, company=company, tonnage=tonnage,
                sub_type=sub_type, pax=pax,
                enforce_catalog=((company, product) not in NO_CATALOG_GUARD),
            )
            key['total_payable'] = result['total_payable']
            key['base_premium'] = result['base_premium']
            key['levies_and_taxes'] = result['levies_and_taxes']
        except Exception as exc:  # noqa: BLE001 — the exception IS the observation
            key['error'] = f'{type(exc).__name__}: {exc}'
        rows.append(key)

    # Period options are equally part of the contract; capture them per insurer.
    periods = {}
    for company in ('monarch', 'definite', 'directline'):
        for product in ('private', 'motorcycle', 'commercial_own_goods', 'psv'):
            periods[f'{company}/{product}'] = available_periods(
                company, product, 'third_party_only')

    json.dump({'premiums': rows, 'periods': periods}, sys.stdout, indent=2)
    sys.stdout.write('\n')


if __name__ == '__main__':
    main()
