"""
dmvic_mapping.py — Westlake product -> DMVIC certificate-type mapping.

DMVIC exposes FOUR separate issuance endpoints; Westlake only needs THREE
of them, confirmed with the business (2026-07-21):

  Type A  /api/v7/IntermediaryIntegration/IssuanceTypeACertificate   — PSV
  Type B  /api/v7/IntermediaryIntegration/IssuanceTypeBCertificate   — Commercial
  Type C  /api/v7/IntermediaryIntegration/IssuanceTypeCCertificate   — Private / general

Type D (standalone Motorcycle, non-PSV) is NOT wired up — no Westlake
product issued through the app needs it under this confirmed scope.

CONFIRMED with the business:
  - (2026-07-11) Any commercial product -> Type B. Any PSV product -> Type A.
  - (2026-07-21) The ONLY products actually issued through the app are:
      * PSV: tuktuk_psv and motorcycle_psv ONLY (not bus/matatu/asset-only —
        DMVIC support separately confirmed 2026-07-11 that intermediaries
        cannot issue Bus/Matatu via this API at all regardless of mapping)
      * Motor Commercial (Type B)
      * Motor Private (Type C)
    Bus/matatu/asset-only PSV entries have been removed from the mapping
    below since they're out of scope and were never issuable anyway.

STILL UNCONFIRMED — do not treat these as final:
  - Yearofregistration is MANDATORY on all three endpoints now in use.
    Westlake currently only captures year_of_manufacture, and
    issue_dmvic_certificate() in app.py sends that as a stand-in. For
    Type A this matches DMVIC's documented fallback behavior; for Type B
    and Type C, DMVIC's spec does NOT document a fallback — CONFIRM WITH
    DMVIC that year_of_manufacture is an acceptable substitute, or capture
    a real registration year, before relying on this in production.
  - Tuktuk products (tuktuk_commercial, tuktuk_psv) are NOT mentioned
    anywhere in DMVIC's endpoint specs (no vehicle type, no cert type
    covers a three-wheeler explicitly). Following the "commercial -> B,
    PSV -> A" rule as a best guess, but this is unconfirmed by DMVIC and
    should be verified before going live for these products.
  - Type A's own documentation has an internal inconsistency: the
    Typeofcover parameter table lists 1/2/3, but every example payload
    across all endpoint docs uses 100/200/300. This file assumes
    100/200/300 is correct everywhere (matching the examples). Worth a
    one-line confirmation email to DMVIC regardless.
  - commercial_hybrid is deliberately absent from PRODUCT_TO_VEHICLE_TYPE_B:
    it needs to be resolved to own_goods vs general_cartage by sub_type/
    tonnage at issuance time, same pattern used in calculate_premium()'s
    get_definite_tp_commercial(). Not yet wired into issue_dmvic_certificate().

PRODUCT_TO_CERT_TYPE and PRODUCT_TO_VEHICLE_TYPE_B are imported by app.py.
"""

# ─────────────────────────────────────────────────────────────────────────
# Bucket used by app.py's issue_dmvic_certificate() to decide routing:
#   'psv'        -> Type A (tuktuk_psv, motorcycle_psv only)
#   'commercial' -> Type B
#   'general'    -> Type C
#   None / not present in this dict -> unsupported, no DMVIC endpoint
# ─────────────────────────────────────────────────────────────────────────
PRODUCT_TO_CERT_TYPE = {
    # ── PSV -> Type A (only these two are actually issued) ─────────────
    'tuktuk_psv':          'psv',   # UNCONFIRMED — tuktuks not in DMVIC spec, see notes above
    'motorcycle_psv':      'psv',

    # ── Commercial -> Type B ───────────────────────────────────────────
    'commercial_own_goods': 'commercial',
    'general_cartage':       'commercial',
    'commercial_hybrid':     'commercial',   # Definite's combined product; split by sub_type at issuance time
    'institutional':         'commercial',
    'special_vehicles':      'commercial',
    'special_types':          'commercial',
    'tanker':                 'commercial',
    'motor_trade':            'commercial',
    'tuktuk_commercial':      'commercial',  # UNCONFIRMED — tuktuks not in DMVIC spec, see notes above

    # ── Private / general -> Type C ────────────────────────────────────
    'private':                 'general',
    'private_fleet':           'general',
    'agriculture_forestry':    'general',
    'driving_school':          'general',
    'driving_school_car':      'general',
    'driving_school_heavy':    'general',
    'private_hire_self':       'general',
    'private_hire_chauffeur':  'general',
    'ambulance_fire':          'general',
    'tour_service':            'general',
    'asset_finance':           'general',
    'electric_motorbike':      'general',   # NOTE: named "motorbike" but has no PSV markers in
                                             # INSURER_PRODUCTS — treated as general pending confirmation.

    # 'motorcycle' (standalone, non-PSV) deliberately absent: falls through
    # to 'unsupported' in app.py since Type D isn't wired up. Add it here
    # (bucketed 'general' or a new 'motorcycle' bucket + Type D support)
    # if/when a non-PSV motorcycle product needs issuing.
}

# ─────────────────────────────────────────────────────────────────────────
# Type A — TypeOfCertificate sub-codes (PSV)
# ─────────────────────────────────────────────────────────────────────────
CERT_TYPE_A = {
    'psv_unmarked': 1,
    'type_a_taxi':    8,
}

# Which CERT_TYPE_A key each PSV product maps to. Both confirmed products
# use psv_unmarked — neither is a taxi. UNCONFIRMED — see notes above.
PRODUCT_TO_CERT_TYPE_A_SUBCODE = {
    'tuktuk_psv':          'psv_unmarked',   # best guess only — UNCONFIRMED
    'motorcycle_psv':      'psv_unmarked',   # best guess only — UNCONFIRMED
}

# ─────────────────────────────────────────────────────────────────────────
# Type B — VehicleType codes (Commercial)
# Confirmed against DMVIC's Intermediary Issuance API doc v1.8.2, sec 4.12.2.
# ─────────────────────────────────────────────────────────────────────────
VEHICLE_TYPE_B = {
    'own_goods':        1,
    'general_cartage':   2,
    'institutional':      3,
    'special_vehicles':   4,
    'tankers':             5,
    'motor_trade':          6,
}

PRODUCT_TO_VEHICLE_TYPE_B = {
    'commercial_own_goods': 'own_goods',
    'general_cartage':       'general_cartage',
    'institutional':          'institutional',
    'special_vehicles':       'special_vehicles',
    'special_types':           'special_vehicles',
    'tanker':                   'tankers',
    'motor_trade':                'motor_trade',
    'tuktuk_commercial':          'own_goods',   # best guess only — UNCONFIRMED
    # 'commercial_hybrid' deliberately absent: resolve at issuance time from
    # sub_type/tonnage (own_goods vs general_cartage), same pattern used in
    # calculate_premium()'s get_definite_tp_commercial().
}

# ─────────────────────────────────────────────────────────────────────────
# Typeofcover — consistent across Type A/B/C per the example payloads
# (see module docstring re: Type A table's 1/2/3 inconsistency).
# ─────────────────────────────────────────────────────────────────────────
COVER_TYPE = {
    'comprehensive':          100,
    'third_party_only':        200,
    'third_party_fire_theft':  300,
}