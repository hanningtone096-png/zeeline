"""
Westlake premium calculation module — pure functions, no Flask/DB/session
dependency. Safe to import from app.py and to unit-test in isolation.

    from premium_calc import calculate_premium, UnsupportedInsurerProductError, \
        INSURER_PRODUCTS, insurer_offers, available_periods, allowed_installments

FIX (2026-08-06): Directline's rate-sheet figures are the insurer's published
GROSS/total-payable premium (levies already included) — confirmed against
their sheet. Monarch and Definite are NOT confirmed either way yet (updated
sheets pending), so their branches are untouched and still treated as
pre-levy base figures. Only Directline's comprehensive and third-party-only
branches use _extract_levies() instead of _add_levies(), so the levy/stamp
duty isn't added a second time on top of an already-levied number.
When Monarch/Definite's updated sheets arrive, re-check the same question for
each table before assuming _add_levies() is still correct for them.
"""

from datetime import date

# ─────────────────────────────────────────────────────────────────────────────
# LEVY / STAMP DUTY
# ─────────────────────────────────────────────────────────────────────────────

TOTAL_LEVY_RATE = 0.0045
STAMP_DUTY = 40


def _add_levies(base):
    """base is a PRE-levy net premium. Adds levy + stamp duty on top."""
    levies = base * TOTAL_LEVY_RATE + STAMP_DUTY
    return round(levies), round(base + levies)


def _extract_levies(gross_total):
    """Reverse of _add_levies: gross_total ALREADY includes levy + stamp duty
    (e.g. Directline's published rate-sheet figure). Splits it back into
    (levies, net_base) without adding anything further, so the total payable
    stays exactly what the rate sheet says — no double-counting."""
    net_base = (gross_total - STAMP_DUTY) / (1 + TOTAL_LEVY_RATE)
    levies = gross_total - net_base
    return round(levies), round(net_base)


# ─────────────────────────────────────────────────────────────────────────────
# PERIODS / INSTALLMENTS
# ─────────────────────────────────────────────────────────────────────────────

PERIOD_FACTORS = {
    'annual':  1.00,
    '30_days': 0.125,
    '14_days': 0.075,
    '7_days':  0.050,
}

INSTALLMENT_COUNTS = {
    'inst_2':  2,
    'inst_3':  3,
}

INSTALLMENT_CAPS = {
    'monarch': 2,
    'definite': 2,
    'directline': 3,
}

NO_INSTALLMENTS_PRODUCTS = {'motorcycle', 'motorcycle_psv'}

NO_SHORT_TERM = {
    ('directline', 'private',        'third_party_only'),
    ('directline', 'motorcycle',     'third_party_only'),
    ('directline', 'motorcycle_psv', 'third_party_only'),
}


def _period_base(annual_base, certificate):
    if certificate in INSTALLMENT_COUNTS:
        n = INSTALLMENT_COUNTS[certificate]
        _, annual_total = _add_levies(annual_base)
        installment_total = annual_total / n
        return (installment_total - STAMP_DUTY) / (1 + TOTAL_LEVY_RATE)
    factor = PERIOD_FACTORS.get(certificate, 1.0)
    return annual_base * factor


def allowed_installments(company, product):
    if (product or '').lower() in NO_INSTALLMENTS_PRODUCTS:
        return set()
    cap = INSTALLMENT_CAPS.get((company or '').lower(), 2)
    return {f'inst_{number}' for number in range(2, cap + 1)}


def available_periods(company, product, cover):
    base_periods = ['annual', '30_days', '14_days', '7_days']
    key = ((company or '').lower(), product, cover)
    if key in NO_SHORT_TERM:
        base_periods = ['annual']
    return base_periods + [
        certificate for certificate in ('inst_2', 'inst_3')
        if certificate in allowed_installments(company, product)
    ]


# ─────────────────────────────────────────────────────────────────────────────
# INSURER PRODUCT CAPABILITY MAP
# ─────────────────────────────────────────────────────────────────────────────

INSURER_PRODUCTS = {
    'monarch': {
        'private':              {'label': 'Motor Private',                 'icon': 'fa-car',            'covers': ['comprehensive', 'third_party_only']},
        'commercial_own_goods': {'label': 'Commercial — Own Goods',        'icon': 'fa-truck',          'covers': ['comprehensive', 'third_party_only']},
        'general_cartage':      {'label': 'Commercial — General Cartage', 'icon': 'fa-truck-loading',  'covers': ['comprehensive', 'third_party_only']},
        'institutional':        {'label': 'Institutional',                 'icon': 'fa-school',         'covers': ['comprehensive', 'third_party_only']},
        'agriculture_forestry': {'label': 'Agriculture & Forestry',       'icon': 'fa-tractor',        'covers': ['comprehensive', 'third_party_only']},
        'special_vehicles':     {'label': 'Special Vehicles',              'icon': 'fa-cogs',           'covers': ['comprehensive']},
        'driving_school':       {'label': 'Driving School',                'icon': 'fa-id-card',        'covers': ['comprehensive', 'third_party_only']},
        'asset_finance':        {'label': 'Asset Finance',                 'icon': 'fa-file-invoice',   'covers': ['comprehensive']},
        'psv':                  {'label': 'PSV Chauffeur Driven',          'icon': 'fa-bus',            'covers': ['comprehensive', 'third_party_only']},
        'tour_service':         {'label': 'Tour Service Vehicles',        'icon': 'fa-route',          'covers': ['comprehensive', 'third_party_only']},
        'motorcycle':           {'label': 'Motorcycle — Private',         'icon': 'fa-motorcycle',     'covers': ['comprehensive', 'third_party_only']},
        'motorcycle_psv':       {'label': 'Motorcycle — PSV',              'icon': 'fa-motorcycle',     'covers': ['comprehensive', 'third_party_only']},
        'tuktuk_commercial':    {'label': 'TukTuk — Commercial',          'icon': 'fa-shuttle-van',    'covers': ['comprehensive', 'third_party_only']},
        'tuktuk_psv':           {'label': 'TukTuk — PSV',                  'icon': 'fa-shuttle-van',    'covers': ['comprehensive', 'third_party_only']},
    },
    'directline': {
        'private':              {'label': 'Motor Private',                 'icon': 'fa-car',            'covers': ['comprehensive', 'third_party_only']},
        'commercial_own_goods': {'label': 'Commercial — Own Goods',       'icon': 'fa-truck',          'covers': ['third_party_only']},
        'general_cartage':      {'label': 'Commercial — General Cartage','icon': 'fa-truck-loading',  'covers': ['third_party_only']},
        'institutional':        {'label': 'Institutional',                 'icon': 'fa-school',         'covers': ['third_party_only']},
        'agriculture_forestry': {'label': 'Agriculture & Forestry',       'icon': 'fa-tractor',        'covers': ['third_party_only']},
        'special_vehicles':     {'label': 'Special Vehicles',              'icon': 'fa-cogs',           'covers': ['third_party_only']},
        'motorcycle':           {'label': 'Motorcycle — Private',         'icon': 'fa-motorcycle',     'covers': ['third_party_only']},
        'motorcycle_psv':       {'label': 'Motorcycle — PSV/Boda',        'icon': 'fa-motorcycle',     'covers': ['third_party_only']},
        'psv':                  {'label': 'PSV Matatu/Bus',                'icon': 'fa-bus',            'covers': ['third_party_only']},
    },
    'definite': {
        'private':                {'label': 'Motor Private (Individual)',           'icon': 'fa-car',              'covers': ['comprehensive', 'third_party_only']},
        'private_fleet':          {'label': 'Motor Private (Fleet)',                'icon': 'fa-car-side',         'covers': ['comprehensive', 'third_party_only']},
        'commercial_hybrid':      {'label': 'Commercial (Own Goods/Cartage)',      'icon': 'fa-truck',            'covers': ['comprehensive', 'third_party_only']},
        'tanker':                 {'label': 'Tankers — Flammable Liquids',         'icon': 'fa-gas-pump',         'covers': ['comprehensive']},
        'motor_trade':            {'label': 'Motor Trade — Road Risks',            'icon': 'fa-store',            'covers': ['comprehensive', 'third_party_only']},
        'private_hire_self':      {'label': 'Private Hire — Self Drive',           'icon': 'fa-key',              'covers': ['comprehensive', 'third_party_only']},
        'private_hire_chauffeur': {'label': 'Private Hire — Chauffeur/Taxi',       'icon': 'fa-user-tie',         'covers': ['comprehensive', 'third_party_only']},
        'driving_school_car':     {'label': 'Driving School — Cars',              'icon': 'fa-id-card',          'covers': ['comprehensive', 'third_party_only']},
        'driving_school_heavy':   {'label': 'Driving School — Heavy Vehicles',    'icon': 'fa-truck-monster',    'covers': ['comprehensive', 'third_party_only']},
        'institutional':          {'label': 'Institutional (School/Hotel/Office bus)', 'icon': 'fa-school',      'covers': ['comprehensive', 'third_party_only']},
        'ambulance_fire':         {'label': 'Ambulance / Firefighter',             'icon': 'fa-truck-medical',    'covers': ['comprehensive', 'third_party_only']},
        'agriculture_forestry':   {'label': 'Agricultural & Forestry',            'icon': 'fa-tractor',          'covers': ['comprehensive', 'third_party_only']},
        'special_types':          {'label': 'Special Types (Crane/Forklift/etc.)', 'icon': 'fa-cogs',             'covers': ['comprehensive', 'third_party_only']},
        'motorcycle':             {'label': 'Motorcycle (Non-PSV)',               'icon': 'fa-motorcycle',       'covers': ['comprehensive', 'third_party_only']},
        'motorcycle_psv':         {'label': 'Motorcycle — PSV',                    'icon': 'fa-motorcycle',       'covers': ['comprehensive', 'third_party_only']},
        'electric_motorbike':     {'label': 'Electric Motorbike',                  'icon': 'fa-charging-station', 'covers': ['third_party_only']},
        'tuktuk_commercial':      {'label': 'TukTuk — Commercial',                'icon': 'fa-shuttle-van',      'covers': ['comprehensive', 'third_party_only']},
        'tuktuk_psv':             {'label': 'TukTuk — PSV',                        'icon': 'fa-shuttle-van',      'covers': ['comprehensive', 'third_party_only']},
        'psv':                    {'label': 'PSV Matatu (7–35 pax)',              'icon': 'fa-bus',              'covers': ['comprehensive', 'third_party_only']},
        'psv_bus':                {'label': 'PSV Bus (Above 35 pax)',             'icon': 'fa-bus-alt',          'covers': ['comprehensive', 'third_party_only']},
        'psv_electric_bus':       {'label': 'PSV Electric Bus',                    'icon': 'fa-bus-alt',          'covers': ['comprehensive', 'third_party_only']},
        'asset_only_matatu':      {'label': 'Asset Only — Matatu',                'icon': 'fa-shield-alt',       'covers': ['comprehensive']},
        'asset_only_bus':         {'label': 'Asset Only — Bus',                    'icon': 'fa-shield-alt',       'covers': ['comprehensive']},
        'tour_service':           {'label': 'Tour Service Vehicles',              'icon': 'fa-route',            'covers': ['comprehensive', 'third_party_only']},
    },
}


def insurer_offers(company, product, cover):
    company = (company or '').lower()
    catalog = INSURER_PRODUCTS.get(company)
    if not catalog:
        return False
    entry = catalog.get(product)
    if not entry:
        return False
    return cover in entry['covers']


# ─────────────────────────────────────────────────────────────────────────────
# PSV RATE TABLE (shared by all insurers except Definite's own PSV branch)
# ─────────────────────────────────────────────────────────────────────────────

PSV_RATE_TABLE = {
    7:  (6300,3781,2145,70020,23761,12147,8281),
    8:  (6570,3958,2195,72899,24751,12687,8638),
    9:  (6840,4137,2295,75868,25737,13139,8909),
    10: (7109,4319,2395,78750,26729,13681,9269),
    11: (7215,4310,2495,79817,27101,13815,9416),
    12: (7650,4588,2545,84597,28710,14667,9991),
    13: (7917,4770,2645,87478,29698,15118,10258),
    14: (7920,4840,2745,88352,30007,15311,10382),
    15: (8368,5040,2795,93327,31678,16110,10980),
    16: (8639,5220,2895,96210,32668,16650,11339),
    17: (8910,5398,2995,99180,33660,17187,11700),
    18: (9180,5488,3095,102057,34650,17640,11968),
    19: (9450,5670,3195,104938,35640,18179,12330),
    20: (9721,5851,3245,107910,36627,18629,12690),
    21: (9897,6029,3345,110788,37620,19171,13049),
    22: (10169,6117,3395,113671,38610,19621,13321),
    23: (10439,6300,3495,116637,39600,20160,13677),
    24: (10710,6477,3595,119518,40587,20700,14040),
    25: (10980,6660,3695,122488,41577,21148,14400),
    26: (11136,6699,3895,124324,42196,21487,14526),
    27: (11389,6797,4045,127161,43178,21928,14873),
    28: (11901,7137,4255,132939,45135,22947,15553),
    29: (11970,7215,4395,133821,45345,23122,15661),
    30: (12917,7733,4595,144498,48957,24904,16914),
    31: (13430,8075,4795,150278,50912,25923,17593),
    32: (13855,8328,4945,155547,52698,26861,18189),
    33: (13940,8365,5145,156127,52891,26893,18285),
    34: (14960,9007,5345,167618,56777,28898,19635),
    35: (15468,9263,5495,173398,58733,29919,20230),
    36: (17423,10456,6195,195330,66213,33659,22779),
    37: (17705,10685,6895,199287,67545,34320,23241),
    38: (18749,11248,7595,210972,71472,36298,24597),
    39: (19109,11479,8295,214897,72798,37028,25057),
    40: (19145,11570,8995,216318,73252,37244,25217),
    41: (19235,11515,9095,216254,73214,37246,25215),
    42: (19558,11710,9245,219516,74367,37757,25597),
    43: (19812,11903,9395,222780,75452,38332,25982),
    44: (20133,12094,9545,226110,76542,38910,26366),
    45: (20393,12220,9695,229372,77693,39487,26750),
    46: (20710,12410,9795,232638,78782,40063,27133),
    47: (20968,12606,9945,235902,79870,40572,27519),
    48: (21291,12732,10095,239167,81023,41150,27903),
    49: (21544,12925,10195,242431,82109,41727,28220),
    50: (21867,13118,10345,245756,83199,42302,28606),
    51: (21995,13185,10395,247295,83710,42559,28797),
    52: (22725,14695,10495,256995,90915,50055,30035),
    53: (23455,14995,10545,266495,93995,51145,30685),
    54: (24185,15195,10595,275295,96995,52055,31235),
    55: (24915,15395,10695,284095,99995,52975,31785),
    56: (25645,15695,10745,292795,102995,54055,32435),
    57: (26375,15895,10845,301555,105515,55055,33035),
    58: (27105,16145,10895,310395,106495,55975,33585),
    59: (27835,16395,10995,319075,107395,56475,33885),
    60: (28565,16615,11045,327835,108665,56475,33885),
    61: (29295,16845,11095,336595,109795,57145,34285),
    62: (29995,16995,11145,339995,109995,57225,34335),
    63: (31595,19195,11195,344895,114955,57475,34485),
    64: (31795,19295,11245,346895,115625,57805,34685),
    65: (31925,19445,11345,349895,116625,58305,34985),
    66: (31965,19445,11345,349895,116625,58305,34985),
    67: (32445,19645,11445,353895,117955,58975,35385),
    68: (32705,19745,11545,357895,119295,59645,35785),
    69: (32835,19895,11595,358395,119455,59725,35835),
    70: (33035,19995,11645,359895,119955,59975,35985),
    71: (33235,20095,11745,363895,121295,60645,36385),
    72: (33435,20195,11795,365895,121955,60975,36585),
    73: (33625,20345,11845,367895,122625,61305,36785),
    74: (33825,20445,11895,370395,123455,61725,37035),
    75: (34025,20545,11995,372895,124295,62145,37285),
    76: (34225,20645,12045,374395,124795,62395,37435),
    77: (34425,20745,12095,376895,125625,62805,37685),
    78: (34615,20895,12195,378395,126125,63055,37835),
    79: (34815,20995,12245,379895,126625,63305,37985),
    80: (35015,21095,12295,381395,127125,63555,38135),
    81: (35215,21195,12395,385395,128455,64225,38535),
    82: (35415,21345,12445,386895,128955,64475,38685),
    83: (35605,21455,12495,388395,129455,64725,38835),
    84: (35805,21545,12595,392395,130795,65395,39235),
    85: (36005,21645,12645,393895,131295,65645,39385),
    86: (36205,21795,12695,395395,131795,65895,39535),
    87: (36405,21895,12795,396895,132295,66145,39685),
    88: (36595,21995,12845,400895,133625,66805,40085),
    89: (36795,22095,12895,402395,134125,67055,40235),
    90: (36995,22245,12945,403895,134625,67305,40385),
    91: (37195,22355,13045,405395,135125,67555,40535),
    92: (37395,22445,13095,409395,136455,68225,40935),
    93: (37585,22545,13145,410895,136955,68475,41085),
    94: (37785,22695,13245,412395,137455,68725,41235),
    95: (37985,22795,13295,413895,137955,68975,41385),
    96: (38185,22895,13345,417895,139295,69645,41785),
    97: (38385,22995,13395,419395,139795,69895,41935),
    98: (38575,23095,13495,420895,140295,70145,42085),
    99: (38775,23245,13545,424895,141625,70805,42485),
    100:(38975,23345,13595,426395,142125,71055,42635),
    101:(39175,23495,13695,427895,142625,71305,42785),
    102:(39375,23545,13745,429395,143125,71555,42935),
    103:(39565,23695,13795,433395,144455,72225,43335),
    104:(39765,23795,13895,434895,144955,72475,43485),
    105:(39965,23895,13945,436895,145625,72805,43685),
}
PSV_COL = {
    '30_days': 0, '14_days': 1, '7_days': 2,
    'annual':  3, 'inst_3':  4, 'inst_6': 5, 'inst_9': 6, 'inst_2': 7,
}


def get_psv_premium(seats, certificate):
    seats = int(seats or 0)
    if seats < 7:   seats = 7
    if seats > 105: seats = 105
    if seats not in PSV_RATE_TABLE:
        for cap in sorted(PSV_RATE_TABLE.keys()):
            if cap >= seats:
                seats = cap
                break
    row = PSV_RATE_TABLE[seats]
    col = PSV_COL.get(certificate, PSV_COL['annual'])
    if certificate == 'inst_2':
        base_amt = _period_base(row[PSV_COL['annual']], 'inst_2')
    else:
        base_amt = row[col]

    breakdown = {}
    for cert in ('annual', '30_days', '14_days', '7_days', 'inst_2', 'inst_3'):
        idx = PSV_COL[cert]
        breakdown[cert] = row[idx]
    breakdown['inst_2'] = round(_period_base(row[PSV_COL['annual']], 'inst_2'))

    return round(base_amt), breakdown


# ─────────────────────────────────────────────────────────────────────────────
# DEFINITE ASSURANCE
# ─────────────────────────────────────────────────────────────────────────────

DEFINITE_COMP_RATES = {
    'private': [(500_000, 1_000_000, 0.045), (1_000_001, 2_000_000, 0.035), (2_000_001, None, 0.030)],
    'private_fleet':             0.040,
    'commercial_hybrid':         0.045,
    'tanker':                    0.080,
    'motor_trade':               0.045,
    'private_hire_self':         0.075,
    'private_hire_chauffeur':    0.055,
    'driving_school_car':        0.050,
    'driving_school_heavy':      0.055,
    'institutional':             0.035,
    'ambulance_fire':            0.040,
    'agriculture_forestry':      0.035,
    'special_types':             0.030,
    'motorcycle':                0.030,
    'motorcycle_psv':            0.040,
    'motorcycle_psv_individual': 0.050,
    'tuktuk_commercial':         0.040,
    'tuktuk_psv':                0.040,
    'psv':                       0.040,
    'psv_bus':                   0.045,
    'psv_electric_bus':          0.050,
    'asset_only_matatu':         0.040,
    'asset_only_bus':            0.045,
    'tour_service':              0.045,
}

DEFINITE_COMP_MINIMUMS = {
    'private':                   30_000,
    'private_fleet':             30_000,
    'commercial_hybrid':         35_000,
    'tanker':                   100_000,
    'motor_trade':               35_000,
    'private_hire_self':         45_000,
    'private_hire_chauffeur':    37_500,
    'driving_school_car':        40_000,
    'driving_school_heavy':      40_000,
    'institutional':             35_000,
    'ambulance_fire':            40_000,
    'agriculture_forestry':      20_000,
    'special_types':             40_000,
    'motorcycle':                 5_000,
    'motorcycle_psv':             6_500,
    'motorcycle_psv_individual':  7_500,
    'tuktuk_commercial':         15_000,
    'tuktuk_psv':                21_500,
    'psv':                       30_000,
    'psv_bus':                   30_000,
    'psv_electric_bus':          50_000,
    'asset_only_matatu':         40_000,
    'asset_only_bus':            50_000,
    'tour_service':               40_000,
}

DEFINITE_TP_FLAT = {
    'private':               4_500,
    'private_fleet':          4_500,
    'motor_trade':           12_500,
    'driving_school_car':     7_500,
    'agriculture_forestry':   3_000,
    'special_types':          5_000,
    'motorcycle':             2_000,
    'tuktuk_commercial':      4_000,
    'tuktuk_psv':             4_500,
    'ambulance_fire':         7_500,
    'electric_motorbike':     5_000,
}

DEFINITE_TP_TONNAGE = [
    (0,    3,   5_500),
    (3.1,  8,   7_500),
    (8.1, None, 9_500),
]
DEFINITE_TP_PRIME_MOVER = 15_000

DEFINITE_TP_PAX_SCALE = {
    'private_hire_self':      [(0, 9, 12_500)],
    'private_hire_chauffeur': [(0, 9, 5_500), (10, 17, 8_500), (18, 25, 12_500), (26, None, 15_500)],
    'driving_school_heavy':   [(0, 15, 15_000), (15.1, None, 20_000)],
    'institutional':          [(0, 9, 7_500), (10, 25, 15_000), (26, None, 20_000)],
    'tour_service':           [(0, 9, 7_500), (10, 25, 12_500), (26, None, 15_000)],
}

DEFINITE_MOTORCYCLE_PSV_TP = 3_500


def get_definite_pax_band(scale_key, count):
    count = int(count or 0)
    bands = DEFINITE_TP_PAX_SCALE.get(scale_key, [])
    for lo, hi, amt in bands:
        if hi is None and count >= lo:
            return amt
        if hi is not None and lo <= count <= hi:
            return amt
    return bands[-1][2] if bands else 0


def get_definite_tonnage_tp(tonnage):
    t = float(tonnage or 0)
    for lo, hi, amt in DEFINITE_TP_TONNAGE:
        if hi is None and t >= lo:
            return amt
        if hi is not None and lo <= t <= hi:
            return amt
    return DEFINITE_TP_TONNAGE[-1][2]


def get_definite_comp_rate(product, value, sub_type=None):
    if product == 'motorcycle_psv' and sub_type == 'individual':
        return DEFINITE_COMP_RATES['motorcycle_psv_individual']
    entry = DEFINITE_COMP_RATES.get(product)
    if entry is None:
        return 0.040
    if isinstance(entry, list):
        for lo, hi, rate in entry:
            if hi is None and value >= lo:
                return rate
            if hi is not None and lo <= value <= hi:
                return rate
        return entry[0][2]
    return entry


def get_definite_comp_minimum(product, sub_type=None):
    if product == 'motorcycle_psv' and sub_type == 'individual':
        return DEFINITE_COMP_MINIMUMS['motorcycle_psv_individual']
    return DEFINITE_COMP_MINIMUMS.get(product, 15_000)


def get_definite_tp_commercial(tonnage=0, prime_mover=False):
    if prime_mover:
        return DEFINITE_TP_PRIME_MOVER
    return get_definite_tonnage_tp(tonnage)


# ─────────────────────────────────────────────────────────────────────────────
# MONARCH
# ─────────────────────────────────────────────────────────────────────────────

MONARCH_COMP_TIERS = {
    'private':              [(500_000,1_500_000,0.0400),(1_500_001,2_000_000,0.0375),(2_000_001,2_500_000,0.0350),(2_500_001,None,0.0300)],
    'commercial_own_goods': [(500_000,1_500_000,0.0400),(1_500_001,2_000_000,0.0400),(2_000_001,2_500_000,0.0375),(2_500_001,None,0.0400)],
    'general_cartage':      [(500_000,1_500_000,0.0400),(1_500_001,2_000_000,0.0400),(2_000_001,2_500_000,0.0400),(2_500_001,None,0.0375)],
    'institutional':        [(500_000,1_500_000,0.0400),(1_500_001,2_000_000,0.0375),(2_000_001,2_500_000,0.0350),(2_500_001,None,0.0300)],
    'agriculture_forestry': [(500_000,1_500_000,0.0350),(1_500_001,2_000_000,0.0325),(2_000_001,None,0.0300)],
    'special_vehicles':     [(500_000,1_500_000,0.0400),(1_500_001,2_000_000,0.0375),(2_000_001,2_500_000,0.0350),(2_500_001,None,0.0300)],
    'driving_school':       [(500_000,1_500_000,0.0400),(1_500_001,2_000_000,0.0375),(2_000_001,2_500_000,0.0350),(2_500_001,None,0.0300)],
    'asset_finance':        [(500_000,1_500_000,0.0400),(1_500_001,2_000_000,0.0375),(2_000_001,2_500_000,0.0350),(2_500_001,None,0.0300)],
    'psv':                  [(500_000,None,0.0550)],
    'tour_service':         [(500_000,None,0.0400)],
    'motorcycle':           [(80_000, None,0.0300)],
    'motorcycle_psv':       [(80_000, None,0.0400)],
    'tuktuk_commercial':    [(200_000,None,0.0400)],
    'tuktuk_psv':           [(200_000,None,0.0500)],
    'commercial_vehicle':   [(500_000,1_500_000,0.0400),(1_500_001,2_000_000,0.0400),(2_000_001,2_500_000,0.0375),(2_500_001,None,0.0400)],
}

MONARCH_MINIMUMS = {
    'private':              27_500,
    'commercial_own_goods': 30_000,
    'general_cartage':      30_000,
    'institutional':        30_000,
    'agriculture_forestry': 30_000,
    'special_vehicles':     30_000,
    'driving_school':       30_000,
    'asset_finance':        40_000,
    'psv':                  35_000,
    'tour_service':         35_000,
    'motorcycle':            5_000,
    'motorcycle_psv':        6_000,
    'tuktuk_commercial':    10_000,
    'tuktuk_psv':           10_000,
    'commercial_vehicle':   30_000,
}

MONARCH_TONNAGE_TP = [
    (0,   3,    4_500),
    (3.1, 8,    5_500),
    (8.1, 12,   6_500),
    (12.1,15,   7_500),
    (15.1,20,  10_000),
    (20.1,None,15_000),
]

MONARCH_TP_FLAT = {
    'psv':               5_500,
    'tour_service':      5_500,
    'motorcycle_psv':    3_000,
    'motorcycle':        2_000,
    'tuktuk_commercial': 3_000,
    'tuktuk_psv':        3_000,
    'private':           3_245,
    'institutional':     5_000,
    'driving_school':    5_000,
    'agriculture_forestry': 3_000,
    'special_vehicles':  7_500,
    'commercial_vehicle':None,
    'commercial_own_goods':None,
    'general_cartage':   None,
    'asset_finance':     None,
}


def get_monarch_comp_rate(product, value):
    tiers = MONARCH_COMP_TIERS.get(product, MONARCH_COMP_TIERS['private'])
    for lo, hi, rate in tiers:
        if hi is None and value >= lo:   return rate
        if hi is not None and lo <= value <= hi: return rate
    return tiers[0][2]


def get_monarch_tonnage_tp(tonnage):
    t = float(tonnage or 0)
    for lo, hi, amt in MONARCH_TONNAGE_TP:
        if hi is None and t >= lo:             return amt
        if hi is not None and lo <= t <= hi:   return amt
    return 15_000


def get_monarch_tp_flat(product, seats=0, sub_type=None):
    seats = int(seats or 0)
    if product in ('institutional', 'driving_school'):
        return 5_000 if seats <= 14 else 7_500
    if product == 'agriculture_forestry':
        return 3_000 if sub_type == 'tractor' else 7_500
    return MONARCH_TP_FLAT.get(product)


# ─────────────────────────────────────────────────────────────────────────────
# DIRECTLINE
# NOTE: these figures are confirmed GROSS (levies already included) — see
# module docstring. calculate_premium() below uses _extract_levies() instead
# of _add_levies() for this insurer only.
# ─────────────────────────────────────────────────────────────────────────────

DIRECTLINE_COMP_TIERS = {
    'private': [(0, 1_500_000, 0.0400), (1_500_001, 3_000_000, 0.0375),
                (3_000_001, 5_000_000, 0.0350), (5_000_001, None, 0.0300)],
}

DIRECTLINE_COMP_FLAT = {
    'commercial_own_goods': 0.04,
    'general_cartage':      0.04,
    'tanker':               0.09,
    'institutional':        0.035,
    'psv_yellow_taxi':      0.055,
    'tuktuk_psv':           0.04,
    'psv_chauffeur_app':    0.055,
    'chauffeur_van_tour':   0.055,
    'psv':                  0.04,
    'psv_bus':              0.045,
    'asset_only_matatu':    0.04,
    'asset_only_bus':       0.045,
    'agriculture_forestry': 0.03,
    'driving_school':       0.05,
}
DIRECTLINE_COMP_FLAT_MINIMUMS = {
    'commercial_own_goods': 40_000,
    'general_cartage':      45_000,
    'tanker':               100_000,
    'institutional':        35_000,
    'psv_yellow_taxi':      40_000,
    'tuktuk_psv':           20_000,
    'psv_chauffeur_app':    40_000,
    'chauffeur_van_tour':   37_500,
    'psv':                  30_000,
    'psv_bus':              30_000,
    'asset_only_matatu':    40_000,
    'asset_only_bus':       50_000,
    'agriculture_forestry': 25_000,
    'driving_school':       40_000,
}

DIRECTLINE_MINIMUMS = {
    'private':              35_000,
    'commercial_own_goods': 40_000,
    'general_cartage':      45_000,
    'institutional':        40_000,
    'agriculture_forestry': 40_000,
    'special_vehicles':     40_000,
    'commercial_vehicle':   40_000,
}

DIRECTLINE_TP_FLAT = {
    'private':         3_171,
    'motorcycle':      3_194,
    'motorcycle_psv':  3_651,
}

DIRECTLINE_TONNAGE_TP = [
    (0,    10,   3_890),
    (10.1, 15,  15_100),
    (15.1, 20,  20_100),
    (20.1, None,25_200),
]


def get_directline_comp_rate(product, value):
    if product in DIRECTLINE_COMP_TIERS:
        tiers = DIRECTLINE_COMP_TIERS[product]
        for lo, hi, rate in tiers:
            if hi is None and value >= lo:           return rate
            if hi is not None and lo <= value <= hi: return rate
        return tiers[0][2]
    return DIRECTLINE_COMP_FLAT.get(product, DIRECTLINE_COMP_TIERS['private'][0][2])


def get_directline_tonnage_tp(tonnage):
    t = float(tonnage or 0)
    for lo, hi, amt in DIRECTLINE_TONNAGE_TP:
        if hi is None and t >= lo:           return amt
        if hi is not None and lo <= t <= hi: return amt
    return 25_200


# ─────────────────────────────────────────────────────────────────────────────
# GENERIC FALLBACK
# ─────────────────────────────────────────────────────────────────────────────

GENERIC_RATES = {
    'comprehensive': {
        'private':            0.0400,
        'commercial_vehicle': 0.0450,
        'motorcycle':         0.0400,
        'psv':                0.0495,
        'tuktuk_commercial':  0.0400,
        'tuktuk_psv':         0.0500,
    },
    'third_party_only': {
        'private':            7_500,
        'commercial_vehicle': 12_000,
        'psv':                15_000,
        'motorcycle':         1_500,
        'tuktuk_commercial':  3_000,
        'tuktuk_psv':         3_000,
    },
    'third_party_fire_theft': {
        'private':            0.0150,
        'commercial_vehicle': 0.0200,
        'motorcycle':         0.0150,
    },
}

GENERIC_MINIMUMS = {
    'private':            15_000,
    'commercial_vehicle': 20_000,
    'motorcycle':          3_000,
    'psv':                30_000,
    'tuktuk_commercial':  10_000,
    'tuktuk_psv':         10_000,
}


def _build_breakdown(rate_fn, certificate, company='', product='', cover=''):
    annual_base = rate_fn(1.0)
    allowed = set(available_periods(company, product, cover))
    breakdown = {}
    is_directline = (company or '').lower() == 'directline'
    for period in ('annual', '30_days', '14_days', '7_days', 'inst_2', 'inst_3'):
        if period not in allowed:
            continue
        base = _period_base(annual_base, period)
        if is_directline:
            _, total = _extract_levies(base)
            total = base  # gross figure IS the period total — nothing added
        else:
            _, total = _add_levies(base)
        breakdown[period] = total
    return breakdown


def _flat_quote(annual_amount, certificate, minimum_floor=500):
    annual_base = max(annual_amount, minimum_floor)
    base = _period_base(annual_base, certificate)
    def rate_fn(f, amt=annual_base):
        return amt
    return base, rate_fn


MONARCH_TP_INSTALLMENT_OVERRIDE = {
    ('private', 'inst_2'): 1712,
}


class UnsupportedInsurerProductError(Exception):
    def __init__(self, company, product, cover):
        self.company, self.product, self.cover = company, product, cover
        super().__init__(
            f"{company or 'this insurer'} does not offer {cover.replace('_',' ')} "
            f"cover for {product.replace('_',' ')}."
        )


def apply_installment_override(company, product, certificate, levies, total, breakdown):
    if company != 'monarch':
        return levies, total, breakdown
    override = MONARCH_TP_INSTALLMENT_OVERRIDE.get((product, certificate))
    if override is None:
        return levies, total, breakdown
    net_base = round((override - STAMP_DUTY) / (1 + TOTAL_LEVY_RATE))
    override_levies = override - net_base
    if certificate in breakdown:
        breakdown[certificate] = override
    return override_levies, override, breakdown


def calculate_premium(cover, product, value, certificate, seats=0, company='',
                       tonnage=0, sub_type=None, pax=0, enforce_catalog=True):
    company  = (company or '').lower()
    seats    = int(seats or 0)
    value    = float(value or 0)
    tonnage  = float(tonnage or 0)
    pax      = int(pax or 0)

    if enforce_catalog and company in INSURER_PRODUCTS:
        if not insurer_offers(company, product, cover):
            raise UnsupportedInsurerProductError(company, product, cover)

    if product == 'psv' and cover == 'third_party_only' and company != 'definite':
        if seats < 7:
            seats = 7
        base_amt, raw_breakdown = get_psv_premium(seats, certificate)
        levies, total = _add_levies(base_amt)
        breakdown = {}
        for period, raw_base in raw_breakdown.items():
            if period not in available_periods(company, product, cover):
                continue
            _, period_total = _add_levies(raw_base)
            breakdown[period] = period_total
        return {
            'base_premium':     round(base_amt),
            'levies_and_taxes': levies,
            'total_payable':    total,
            'period_breakdown': breakdown,
            'psv_table':        True,
            'seats_used':       seats,
        }

    if company == 'definite':
        minimum = get_definite_comp_minimum(product, sub_type)

        if cover == 'comprehensive':
            rate = get_definite_comp_rate(product, value, sub_type)
            annual_base = max(value * rate, minimum)
            base = _period_base(annual_base, certificate)
            def rate_fn(f, amt=annual_base):
                return amt
            levies, total = _add_levies(base)
            breakdown = _build_breakdown(rate_fn, certificate, company, product, cover)
            return {
                'base_premium':     round(base),
                'levies_and_taxes': levies,
                'total_payable':    total,
                'period_breakdown': breakdown,
                'psv_table':        False,
                'insurer':          'Definite Assurance',
                'rate_applied':     f"{rate*100:.1f}%",
            }

        elif cover == 'third_party_only':
            if product in ('commercial_hybrid',):
                flat = get_definite_tp_commercial(tonnage, prime_mover=(sub_type == 'prime_mover'))
            elif product in DEFINITE_TP_PAX_SCALE:
                flat = get_definite_pax_band(product, pax or seats)
            elif product == 'motorcycle_psv':
                flat = DEFINITE_MOTORCYCLE_PSV_TP
            elif product == 'psv':
                if seats < 7:
                    seats = 7
                base_amt, raw_breakdown = get_psv_premium(seats, certificate)
                levies, total = _add_levies(base_amt)
                breakdown = {}
                for period, raw_base in raw_breakdown.items():
                    if period not in available_periods(company, product, cover):
                        continue
                    _, period_total = _add_levies(raw_base)
                    breakdown[period] = period_total
                return {
                    'base_premium': round(base_amt), 'levies_and_taxes': levies,
                    'total_payable': total, 'period_breakdown': breakdown,
                    'psv_table': True, 'seats_used': seats, 'insurer': 'Definite Assurance',
                }
            elif product == 'psv_bus':
                flat = 30_000
            else:
                flat = DEFINITE_TP_FLAT.get(product)
                if flat is None:
                    flat = GENERIC_RATES['third_party_only'].get(product, 7_500)
            base, rate_fn = _flat_quote(flat, certificate)
            levies, total = _add_levies(base)
            breakdown = _build_breakdown(rate_fn, certificate, company, product, cover)
            return {
                'base_premium': round(base), 'levies_and_taxes': levies,
                'total_payable': total, 'period_breakdown': breakdown,
                'psv_table': False, 'insurer': 'Definite Assurance',
            }

    if company == 'monarch':
        mprod   = product if product in MONARCH_COMP_TIERS else 'private'
        minimum = MONARCH_MINIMUMS.get(mprod, 27_500)

        if cover == 'comprehensive':
            rate = get_monarch_comp_rate(mprod, value)
            annual_base = max(value * rate, minimum)
            base = _period_base(annual_base, certificate)
            def rate_fn(f, amt=annual_base):
                return amt
            levies, total = _add_levies(base)
            breakdown = _build_breakdown(rate_fn, certificate, company, product, cover)
            return {
                'base_premium': round(base), 'levies_and_taxes': levies,
                'total_payable': total, 'period_breakdown': breakdown,
                'psv_table': False, 'insurer': 'Monarch',
                'rate_applied': f"{rate*100:.2f}%",
            }

        elif cover == 'third_party_only':
            if mprod in ('commercial_vehicle', 'commercial_own_goods', 'general_cartage'):
                flat = get_monarch_tonnage_tp(tonnage)
            else:
                flat = get_monarch_tp_flat(mprod, seats)
                if flat is None:
                    flat = GENERIC_RATES['third_party_only'].get(product, 7_500)
            base, rate_fn = _flat_quote(flat, certificate)
            levies, total = _add_levies(base)
            breakdown = _build_breakdown(rate_fn, certificate, company, product, cover)
            levies, total, breakdown = apply_installment_override(
                company, mprod, certificate, levies, total, breakdown
            )
            return {
                'base_premium': round(base), 'levies_and_taxes': levies,
                'total_payable': total, 'period_breakdown': breakdown,
                'psv_table': False, 'insurer': 'Monarch',
            }

    if company == 'directline':
        dprod   = product
        minimum = DIRECTLINE_MINIMUMS.get(dprod, 35_000)

        if cover == 'comprehensive' and dprod in DIRECTLINE_COMP_TIERS:
            rate = get_directline_comp_rate(dprod, value)
            annual_base = max(value * rate, minimum)
            base = _period_base(annual_base, certificate)   # base is GROSS (levies included)
            def rate_fn(f, amt=annual_base):
                return amt
            levies, computed_base = _extract_levies(base)
            total = base  # don't add anything on top — it's already the gross total
            breakdown = _build_breakdown(rate_fn, certificate, company, product, cover)
            return {
                'base_premium':     computed_base,
                'levies_and_taxes': levies,
                'total_payable':    total,
                'period_breakdown': breakdown,
                'psv_table':        False,
                'insurer':          'Directline',
                'rate_applied':     f"{rate*100:.2f}%",
            }

        elif cover == 'third_party_only':
            if dprod in ('commercial_vehicle', 'commercial_own_goods', 'general_cartage',
                         'institutional', 'agriculture_forestry', 'special_vehicles'):
                flat = get_directline_tonnage_tp(tonnage)
            else:
                flat = DIRECTLINE_TP_FLAT.get(dprod)
                if flat is None:
                    flat = GENERIC_RATES['third_party_only'].get(product, 7_500)
            base, rate_fn = _flat_quote(flat, certificate)   # base is GROSS (levies included)
            levies, computed_base = _extract_levies(base)
            total = base
            breakdown = _build_breakdown(rate_fn, certificate, company, product, cover)
            return {
                'base_premium':     computed_base,
                'levies_and_taxes': levies,
                'total_payable':    total,
                'period_breakdown': breakdown,
                'psv_table':        False,
                'insurer':          'Directline',
            }

    rate_table = GENERIC_RATES.get(cover, GENERIC_RATES['third_party_only'])
    rate       = rate_table.get(product, rate_table.get('private', 7500))
    minimum    = GENERIC_MINIMUMS.get(product, 5000)

    if isinstance(rate, float):
        annual_base = max(value * rate, minimum)
        base = _period_base(annual_base, certificate)
        def rate_fn(f, amt=annual_base):
            return amt
    else:
        base, rate_fn = _flat_quote(rate, certificate)

    levies, total = _add_levies(base)
    breakdown = _build_breakdown(rate_fn, certificate, company, product, cover)
    return {
        'base_premium':     round(base),
        'levies_and_taxes': levies,
        'total_payable':    total,
        'period_breakdown': breakdown,
        'psv_table':        False,
    }
