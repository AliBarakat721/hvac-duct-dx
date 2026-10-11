"""Al-Andalosia catalog tables from diffuser.pdf.
Circular performance is on printed p.6, 4SCD on p.12, and 3SCD on p.15.
Square diffusers: band 0=<=20, 1=<=30, 2=<=40, 3=>40 NC (catalog colour code, 8 dB room attenuation).
The printed 18x18 square AK differs from the stored metadata value; AK is not
used in diffuser calculations, which use geometric face area."""
VEL8=[200, 300, 400, 500, 600, 700, 800, 900]
PT8=[0.019, 0.039, 0.067, 0.1, 0.14, 0.19, 0.24, 0.3]
VC=[300, 400, 500, 600, 700, 800, 1000, 1200, 1400, 1600]
SQUARE={6: {'area': 0.25,
     'band3': [0, 0, 0, 1, 1, 2, 2, 3],
     'band4': [0, 0, 0, 1, 1, 2, 2, 3],
     'cfm': [50, 75, 100, 125, 150, 175, 200, 225],
     'th4': [(2, 3), (3, 5), (4, 7), (5, 9), (6, 11), (7, 13), (8, 15), (9, 17)],
     'tha': [(2, 3), (3, 5), (4, 7), (4, 8), (5, 10), (6, 11), (7, 13), (8, 15)],
     'thb': [(2, 3), (2, 4), (3, 5), (3, 6), (4, 8), (5, 9), (5, 10), (6, 11)]},
 9: {'area': 0.5625,
     'band3': [0, 0, 1, 1, 2, 2, 3, 3],
     'band4': [0, 0, 1, 1, 2, 2, 3, 3],
     'cfm': [112, 169, 225, 281, 338, 394, 450, 506],
     'th4': [(2, 3), (3, 5), (4, 8), (5, 10), (6, 12), (7, 14), (9, 17), (9, 18)],
     'tha': [(2, 4), (3, 6), (4, 8), (5, 10), (6, 11), (7, 13), (8, 15), (9, 17)],
     'thb': [(2, 3), (3, 5), (4, 7), (4, 8), (5, 10), (6, 12), (7, 13), (8, 15)]},
 12: {'area': 1.0,
      'band3': [0, 0, 1, 2, 2, 3, 3, 3],
      'band4': [0, 0, 1, 2, 2, 3, 3, 3],
      'cfm': [200, 300, 400, 500, 600, 700, 800, 900],
      'th4': [(2, 4), (4, 7), (5, 9), (6, 11), (7, 14), (8, 16), (10, 19), (11, 21)],
      'tha': [(3, 5), (4, 7), (5, 9), (6, 12), (7, 14), (8, 16), (9, 18), (10, 20)],
      'thb': [(2, 4), (3, 6), (4, 8), (5, 10), (6, 12), (7, 14), (8, 16), (9, 18)]},
 15: {'area': 1.5625,
      'band3': [0, 0, 1, 2, 2, 3, 3, 3],
      'band4': [0, 0, 1, 2, 2, 3, 3, 3],
      'cfm': [312, 469, 625, 781, 938, 1094, 1250, 1406],
      'th4': [(3, 5), (4, 8), (5, 10), (7, 13), (8, 16), (10, 19), (11, 21), (12, 24)],
      'tha': [(3, 6), (4, 8), (6, 11), (7, 13), (8, 16), (10, 19), (11, 21), (13, 25)],
      'thb': [(3, 5), (4, 7), (5, 9), (6, 11), (7, 14), (8, 16), (10, 19), (11, 21)]},
 18: {'area': 2.25,
      'AK': 1.13,
      'band3': [0, 1, 1, 2, 2, 3, 3, 3],
      'band4': [0, 1, 1, 2, 2, 3, 3, 3],
      'cfm': [450, 675, 900, 1125, 1350, 1575, 1800, 2025],
      'th4': [(3, 5), (4, 8), (5, 10), (7, 13), (9, 17), (10, 20), (12, 24), (13, 26)],
      'tha': [(4, 7), (5, 9), (7, 13), (8, 16), (10, 19), (11, 22), (13, 25), (14, 28)],
      'thb': [(3, 5), (4, 8), (5, 10), (6, 12), (8, 15), (9, 17), (10, 20), (11, 22)]},
 21: {'area': 3.0625,
      'band3': [1, 1, 2, 2, 3, 3, 3, 3],
      'band4': [1, 1, 2, 2, 3, 3, 3, 3],
      'cfm': [612, 919, 1225, 1531, 1838, 2144, 2450, 2756],
      'th4': [(3, 5), (4, 8), (6, 11), (7, 13), (9, 18), (11, 21), (13, 25), (14, 28)],
      'tha': [(4, 7), (5, 10), (7, 13), (9, 17), (10, 20), (12, 23), (13, 26), (15, 29)],
      'thb': [(3, 6), (4, 8), (6, 11), (7, 14), (8, 16), (10, 19), (11, 22), (13, 25)]},
 24: {'area': 4.0,
      'band3': [1, 1, 2, 2, 3, 3, 3, 3],
      'band4': [1, 1, 2, 2, 3, 3, 3, 3],
      'cfm': [800, 1200, 1600, 2000, 2400, 2800, 3200, 3600],
      'th4': [(3, 6), (5, 9), (6, 12), (7, 14), (10, 19), (11, 22), (13, 26), (15, 30)],
      'tha': [(4, 8), (6, 11), (8, 15), (10, 19), (11, 22), (13, 26), (15, 30), (17, 33)],
      'thb': [(3, 6), (5, 9), (6, 12), (8, 15), (9, 18), (11, 21), (12, 24), (14, 27)]}}
CCD={6: {'area': 0.196,
     'cfm': [60.0, 80.0, 100.0, 120.0, 135.0, 155.0, 195.0, 235.0, 275.0, 315.0],
     'nc': [19.0, 19.0, 19.0, 19.0, 19.0, 25.0, 30.0, 35.0, 39.0, 45.0],
     'pt': [0.025, 0.041, 0.061, 0.082, 0.106, 0.132, 0.201, 0.282, 0.38, 0.48],
     'th': [(3, 5), (3, 6), (3, 6), (4, 6), (4, 6), (4, 6), (5, 7), (5, 7), (6, 8), (7, 10)]},
 8: {'area': 0.349,
     'cfm': [105.0, 140.0, 175.0, 210.0, 245.0, 280.0, 350.0, 420.0, 490.0, 560.0],
     'nc': [19.0, 19.0, 19.0, 20.0, 25.0, 29.0, 33.0, 38.0, 42.0, 46.0],
     'pt': [0.021, 0.032, 0.052, 0.071, 0.1, 0.122, 0.183, 0.271, 0.37, 0.48],
     'th': [(4, 6), (4, 6), (5, 7), (6, 8), (6, 8), (6, 8), (7, 10), (7, 11), (8, 13), (9, 14)]},
 10: {'area': 0.545,
      'cfm': [165.0, 220.0, 275.0, 325.0, 380.0, 435.0, 545.0, 655.0, 765.0, 870.0],
      'nc': [19.0, 19.0, 19.0, 21.0, 26.0, 30.0, 34.0, 39.0, 43.0, 47.0],
      'pt': [0.04, 0.061, 0.092, 0.122, 0.181, 0.242, 0.361, 0.5, 0.71, 0.95],
      'th': [(4, 6), (6, 9), (7, 10), (7, 10), (7, 11), (8, 13), (9, 14), (10, 16), (12, 18), (15, 22)]},
 12: {'area': 0.785,
      'cfm': [225.0, 315.0, 395.0, 470.0, 550.0, 630.0, 785.0, 940.0, 1100.0, 1255.0],
      'nc': [19.0, 19.0, 19.0, 23.0, 28.0, 32.0, 36.0, 41.0, 45.0, 48.0],
      'pt': [0.026, 0.041, 0.072, 0.101, 0.152, 0.201, 0.313, 0.49, 0.67, 0.87],
      'th': [(6, 8), (7, 10), (7, 11), (7, 11), (8, 13), (9, 14), (10, 16), (14, 21), (15, 23), (19, 27)]},
 14: {'area': 1.07,
      'cfm': [320.0, 430.0, 535.0, 640.0, 750.0, 855.0, 1070.0, 1285.0, 1500.0, 1710.0],
      'nc': [19.0, 19.0, 19.0, 24.0, 29.0, 33.0, 37.0, 42.0, 46.0, 49.0],
      'pt': [0.034, 0.051, 0.08, 0.112, 0.156, 0.201, 0.313, 0.46, 0.6, 0.8],
      'th': [(6, 8), (8, 13), (9, 15), (10, 16), (11, 17), (12, 18), (15, 23), (19, 26), (19, 27), (21, 31)]},
 16: {'area': 1.4,
      'cfm': [420.0, 560.0, 700.0, 840.0, 980.0, 1120.0, 1400.0, 1680.0, 1960.0, 2240.0],
      'nc': [19.0, 19.0, 20.0, 25.0, 30.0, 34.0, 38.0, 43.0, 47.0, 50.0],
      'pt': [0.043, 0.075, 0.106, 0.14, 0.202, 0.262, 0.413, 0.593, 0.8, 0.98],
      'th': [(7, 11),
             (9, 14),
             (9, 15),
             (11, 17),
             (12, 18),
             (15, 23),
             (16, 25),
             (19, 27),
             (21, 31),
             (25, 36)]},
 18: {'area': 1.77,
      'cfm': [530.0, 710.0, 885.0, 1060.0, 1240.0, 1415.0, 1770.0, 2125.0, 2480.0, 2830.0],
      'nc': [19.0, 19.0, 21.0, 26.0, 31.0, 35.0, 39.0, 44.0, 48.0, 51.0],
      'pt': [0.033, 0.052, 0.082, 0.113, 0.166, 0.22, 0.35, 0.53, 0.71, 0.95],
      'th': [(8, 13),
             (9, 14),
             (10, 16),
             (12, 18),
             (14, 22),
             (17, 24),
             (19, 27),
             (21, 31),
             (22, 33),
             (26, 38)]},
 20: {'area': 2.18,
      'cfm': [660.0, 850.0, 1100.0, 1300.0, 1530.0, 1750.0, 2150.0, 2650.0, 3050.0, 3500.0],
      'nc': [19.0, 19.0, 21.0, 27.0, 32.0, 36.0, 40.0, 45.0, 49.0, 52.0],
      'pt': [0.04, 0.065, 0.092, 0.131, 0.182, 0.251, 0.45, 0.64, 0.82, 0.93],
      'th': [(8, 14),
             (11, 16),
             (14, 20),
             (17, 21),
             (19, 25),
             (22, 28),
             (29, 34),
             (34, 40),
             (41, 47),
             (43, 50)]},
 24: {'area': 3.14,
      'cfm': [940.0, 1250.0, 1570.0, 1850.0, 2200.0, 2500.0, 3130.0, 3850.0, 4320.0, 4950.0],
      'nc': [19.0, 19.0, 22.0, 28.0, 33.0, 37.0, 41.0, 46.0, 50.0, 53.0],
      'pt': [0.045, 0.07, 0.105, 0.135, 0.195, 0.265, 0.46, 0.69, 0.79, 0.96],
      'th': [(10, 15),
             (14, 20),
             (17, 23),
             (20, 26),
             (24, 30),
             (27, 34),
             (35, 41),
             (43, 50),
             (46, 52),
             (51, 58)]}}

# Return round ceiling diffuser catalogue (p.7). The "<20" catalogue entries
# are represented as 19 for numeric limit checks and formatted as "<20".
RETURN_VELOCITY_FPM = [300, 400, 500, 600, 700, 800, 1000, 1200, 1400, 1600]
RETURN_CCD = {
    6: {
        "area": 0.196,
        "nc": [19, 19, 19, 19, 21, 26, 32, 37, 41, 46],
        "pt": [.029, .048, .076, .108, .146, .18, .294, .421, .569, .749],
    },
    8: {
        "area": 0.349,
        "nc": [19, 19, 19, 21, 26, 30, 34, 39, 43, 47],
        "pt": [.020, .034, .053, .077, .100, .123, .207, .293, .394, .519],
    },
    10: {
        "area": 0.545,
        "nc": [19, 19, 19, 22, 27, 31, 36, 41, 45, 48],
        "pt": [.019, .033, .050, .071, .096, .120, .196, .281, .379, .477],
    },
    12: {
        "area": 0.785,
        "nc": [19, 19, 19, 23, 29, 33, 37, 42, 46, 49],
        "pt": [.018, .029, .045, .064, .086, .112, .176, .249, .339, .443],
    },
    14: {
        "area": 1.07,
        "nc": [19, 19, 19, 25, 30, 34, 38, 43, 47, 50],
        "pt": [.017, .028, .042, .061, .083, .108, .170, .242, .328, .430],
    },
    16: {
        "area": 1.40,
        "nc": [19, 19, 21, 26, 31, 35, 39, 44, 48, 51],
        "pt": [.016, .025, .041, .059, .079, .103, .162, .231, .314, .411],
    },
    18: {
        "area": 1.77,
        "nc": [19, 19, 22, 27, 32, 36, 40, 45, 49, 52],
        "pt": [.015, .025, .040, .057, .077, .099, .156, .224, .303, .397],
    },
    20: {
        "area": 2.18,
        "nc": [19, 19, 21, 28, 33, 37, 41, 46, 50, 53],
        "pt": [.014, .024, .036, .052, .071, .091, .143, .205, .277, .363],
    },
    24: {
        "area": 3.14,
        "nc": [19, 19, 23, 29, 34, 38, 42, 47, 51, 55],
        "pt": [.013, .022, .031, .046, .064, .082, .130, .185, .246, .326],
    },
}

VCD_STANDARD_L_MM = tuple(range(200, 1001, 100))
VCD_DRIVE_ARM_X_MM = {
    200: 52,
    300: 252,
    400: 252,
    500: 252,
    600: 252,
    700: 252,
    800: 452,
    900: 452,
    1000: 452,
}
VCD_BLADE_PITCH_MM = 100
VCD_CASE_DEPTH_MM = 120
VCD_DP_COEFFICIENT = 3.1e-8
VCD_CLOSED_LEAKAGE_CFM_PER_FT2 = {
    0.5: 13,
    1.0: 21,
    2.0: 30,
    4.0: 43,
}

RVCD_FRAME_ALLOWANCE_IN = (
    (9.875, None),
    (23.625, 2.0),
    (35.4375, 2.875),
    (float("inf"), 4.0),
)
RVCD_DP_COEFFICIENT = 0.215
RVCD_REFERENCE_VELOCITY_FPM = 4005.0
RVCD_LEAKAGE_CFM_PER_PERIMETER_IN_AT_1_INWG = 0.08

ACCESS_DOORS = {
    (10, 6): (60, 100),
    (10, 8): (80, 140),
    (12, 6): (70, 120),
    (12, 8): (100, 200),
    (12, 12): (185, 295),
    (18, 6): (105, 210),
    (18, 12): (110, 215),
    (18, 18): (250, 500),
    (24, 12): (370, 665),
    (24, 18): (340, 565),
    (24, 24): (445, 1065),
    (30, 12): (470, 845),
    (30, 18): (280, 700),
    (30, 24): (580, 1160),
    (36, 18): (855, 1370),
    (36, 24): (915, 1830),
    (36, 30): (1440, 2305),
    (48, 24): (1530, 2755),
    (48, 30): (1540, 3080),
    (48, 36): (1850, 3240),
}
ACCESS_DOOR_BLADE_ANGLE_DEG = 45
ACCESS_DOOR_BLADE_PITCH_MM = 19
ACCESS_DOOR_FILTER_DEPTH_IN = 1
ACCESS_DOOR_CATALOG_NOTES = {
    (30, 18): (
        "Printed range 280–700 CFM overlaps inconsistently with 30×12 "
        "(470–845 CFM); confirm selection with manufacturer."),
    (48, 24): (
        "Horizontal size is 48 inches; the catalogue's 36-inch label is a typo."),
}
ACCESS_DOOR_CATALOG_WARNINGS = (
        ("The 30×18 CFM range (280–700) overlaps inconsistently with 30×12 "
         "(470–845); confirm catalogue selection with the manufacturer."),
        ("The 48×24 horizontal size is 48 inches; the catalogue's 36-inch label "
         "is a typo."),
)


# ---------------------------------------------------------------------------
# Diffuser selection rules (sources: SMACNA Duct Design ch.3, ASHRAE HVAC
# Applications 2015 ch.57 / ch.48, Al-Andalosia catalogue).  Every rule has a
# status: supported | engineer_decision | unresolved | pending_verification |
# not_implemented | removed.  Read through the rules_* helpers in
# diffuser_calculator.  NC: set DIFFUSER_RULES["R-NC"]["policy"] to
# "ashrae_ch48" to use the Ch.48 goal + 5 limits.
# ---------------------------------------------------------------------------
DIFFUSER_RULES = {'R-TL-BAND': {'status': 'supported',
               'source': 'ASHRAE HVAC Applications 2015 Ch.57 Table 4 (circular ceiling diffuser, '
                         'T0.25/L vs room load)',
               'terminal_velocity_note': 'T0.25 m/s ~ 50 fpm, matches catalog T50 column',
               'table': [{'load_w_m2': 250,
                          'lo': 0.7,
                          'hi': 1.3,
                          'adpi_greater_than': 70,
                          'max_adpi': 76},
                         {'load_w_m2': 190,
                          'lo': 0.7,
                          'hi': 1.2,
                          'adpi_greater_than': 80,
                          'max_adpi': 83},
                         {'load_w_m2': 125,
                          'lo': 0.5,
                          'hi': 1.5,
                          'adpi_greater_than': 80,
                          'max_adpi': 88},
                         {'load_w_m2': 60,
                          'lo': 0.7,
                          'hi': 1.3,
                          'adpi_greater_than': 90,
                          'max_adpi': 93}],
               'fallback': {'lo': 0.7, 'hi': 1.2},
               'fallback_note': 'No sensible load given or outside 60-250 W/m2: conservative '
                                '0.7-1.2 screen (not an ADPI calculation)',
               'square_kinds_note': 'Table 4 has no square 4-way/3-way row; circular row used as '
                                    'proxy',
               'source_pages': ['ASHRAE Applications 2015 printed p.57.6 Table 4'],
               'adpi_note': "At 250 W/m2 the table's max ADPI is 76, i.e. the ADPI>=80 target "
                            '(p.57.5) is not reachable; band only guarantees ADPI>70. Column '
                            'headings of Table 4 are misaligned in the text - verify visually.',
               'verified': 'Read directly from ashrae_ch57.txt (printed p.57.6 Table 4); columns: '
                           'room load, T0.25/L for max ADPI, max ADPI, ADPI greater than, T0.25/L '
                           'range',
               'target_t_over_l': 0.8,
               'square_diffuser_decision': {'status': 'engineer_decision',
                                            'issue': 'Table 4 has no square 4-way/3-way row. Code '
                                                     'uses the circular-ceiling row as proxy.',
                                            'alternative_row': {'name': 'Perforated, louvered '
                                                                        'ceiling diffusers',
                                                                'load_w_m2': '35 to 160',
                                                                'target_t_over_l': 2.0,
                                                                'max_adpi': 96,
                                                                'ranges': [{'adpi_greater_than': 90,
                                                                            'lo': 1.4,
                                                                            'hi': 2.7},
                                                                           {'adpi_greater_than': 80,
                                                                            'lo': 1.0,
                                                                            'hi': 3.4}],
                                                                'L_definition': 'Distance to wall '
                                                                                'or midplane '
                                                                                'between outlets'},
                                            'enabled': False}},
 'R-CHAR-L': {'status': 'supported',
              'source': 'SMACNA Duct Design Table 3-1 / ASHRAE Applications Ch.57 Table 3',
              'wall_distance': 'distance to nearest wall',
              'mixed_jets': '0.5 x distance to neighbouring outlet + drop to occupied zone',
              'include_drop': True,
              'occupied_zone_top_ft': 6.0,
              'occupied_zone_top_status': 'supported (SMACNA Duct Design p.3.9: drop must not '
                                          'exceed distance to the 6 ft level; found by two '
                                          'independent extractions)',
              'source_pages': ['SMACNA Duct Design printed p.3.3 Table 3-1',
                               'ASHRAE Applications 2015 printed p.57.6 Table 3',
                               'SMACNA printed p.3.9']},
 'R-MIN-THROW': {'status': 'removed',
                 'enabled': False,
                 'reason': 'T50 >= max(W,L)/2 is not supported by SMACNA/ASHRAE docs '
                           '(DIFFUSER_SELECTION_RULES.md sec. G); the per-outlet T/L check '
                           'replaces it'},
 'R-NC': {'status': 'engineer_decision',
          'source': 'ASHRAE Applications Ch.48 Table 1 gives ROOM goals; catalog diffuser NC is '
                    'not interchangeable',
          'default_limit': 35,
          'application': {'residential': {'goal': 30,
                                          'limit': 30,
                                          'ch48_row': 'Living areas',
                                          'catalog_table2_range': None,
                                          'limit_ch48': 34},
                          'hospital': {'goal': 30,
                                       'limit': 35,
                                       'ch48_row': 'Patient rooms',
                                       'catalog_table2_range': 'Private rooms 25-35; Wards 30-40',
                                       'limit_ch48': 34},
                          'office_priv': {'goal': 30,
                                          'limit': 35,
                                          'ch48_row': 'Executive and private offices',
                                          'catalog_table2_range': None,
                                          'limit_ch48': 34},
                          'conference': {'goal': 30,
                                         'limit': 30,
                                         'ch48_row': 'Conference rooms',
                                         'catalog_table2_range': 'Conference Room 25-35',
                                         'limit_ch48': 34},
                          'office_open': {'goal': 40,
                                          'limit': 40,
                                          'ch48_row': 'Open-plan offices',
                                          'catalog_table2_range': None,
                                          'limit_ch48': 44},
                          'classroom': {'goal': 30,
                                        'limit': 35,
                                        'ch48_row': 'Classrooms',
                                        'catalog_table2_range': 'Libraries, school and classrooms '
                                                                '30-40',
                                        'limit_ch48': 34},
                          'retail': {'goal': None,
                                     'limit': 45,
                                     'note': 'Not in Ch.48 Table 1; 45 is inside catalog Table 2 '
                                             'range 35-45 (main floor) - supported by catalog only',
                                     'ch48_row': None,
                                     'catalog_table2_range': 'Stores: dept stores main '
                                                             'floor/supermarkets 35-45; '
                                                             'clothing/upper floors 40-50',
                                     'limit_ch48': 45},
                          'industrial': {'goal': None,
                                         'limit': 55,
                                         'ch48_row': None,
                                         'catalog_table2_range': 'Assembly lines, light machinery '
                                                                 '55-75',
                                         'limit_ch48': 55},
                          'server_room': {'goal': None,
                                          'limit': 55,
                                          'ch48_row': None,
                                          'catalog_table2_range': None,
                                          'limit_ch48': 55}},
          'catalog_conflict': 'Catalog Table 2 (p.4) ranges differ from code limits: Conference '
                              '25-35 (code 30), classrooms 30-40 (code 35), wards 30-40 (code 35), '
                              'private rooms 25-35. Engineer must pick the policy (catalog upper '
                              'bound vs ASHRAE Ch.48 goal).',
          'policy': 'current',
          'policy_options': {'current': 'limit values below (legacy, unchanged)',
                             'ashrae_ch48': 'Ch.48 Table 1 goal + 5, exclusive (limit_ch48)'},
          'source_verified': 'ASHRAE Applications 2015 Ch.48 Table 1 (printed p.48.3) read '
                             'directly; text: background level should be less than NC/RC 35 for a '
                             'goal of 30'},
 'R-NECK-VEL': {'status': 'engineer_decision', 'source': None, 'preferred_fpm': [400, 800]},
 'R-DP': {'status': 'engineer_decision',
          'source': 'Carrier p.71: rated dP may exclude collar/approach',
          'max_inwg': 0.2},
 'R-AREA': {'status': 'engineer_decision',
            'source': None,
            'max_ft2_per_diffuser': 400,
            'min_ft2_per_diffuser': 60},
 'R-TABLE4': {'status': 'supported',
              'source': 'Al-Andalosia diffuser.pdf Table 4 p.5',
              'note': 'kept in diffuser_calculator.CEILING_TABLE',
              'verified_against': 'catalog p.5 table matches CEILING_TABLE exactly (extraction 2)'},
 'R-TERMINAL-VELOCITY': {'status': 'unresolved',
                         'source': 'Al-Andalosia catalog p.3 (definitions) vs p.10 (performance '
                                   'notes)',
                         'conflict': 'p.3 text associates min/max throw with 200/100 fpm (residual '
                                     '75/35); p.10 says 100 fpm (min throw) and 50 fpm (max '
                                     'throw). Code assumes (T100, T50). SMACNA p.3.3 uses 50 fpm '
                                     'for ceiling diffusers. Check catalog p.3 and p.10 on the '
                                     'PDF.',
                         'code_assumption': 'th tuple = (T100, T50)',
                         'verified': "Both statements read verbatim: p.3 'minimum throw terminal "
                                     "velocity 200 (residual 75 FPM)', 'maximum throw ... 100 FPM "
                                     "(residual 35)'; p.10 'maximum terminal velocity of 50 fpm "
                                     "and minimum terminal velocity of 100 fpm'. Performance "
                                     'tables carry no velocity annotation.',
                         'decision': 'Keep (T100,T50) as assumption (p.10 + SMACNA 50 fpm '
                                     'convention) until supplier confirms'},
 'R-NC-SPACING': {'status': 'pending_verification',
                  'enforced': False,
                  'source': 'ASHRAE Applications 2015 printed p.57.5',
                  'rule': 'Catalog NC applies to identical outlets >= 3 m apart; closer outlets '
                          'may add up to 3 dB',
                  'min_spacing_m': 3.0,
                  'add_db': 3.0},
 'R-JET-COLLISION': {'status': 'not_implemented',
                     'source': 'ASHRAE Applications p.57.5',
                     'rule': 'Jets must not collide and project air down above 0.25 m/s in the '
                             'occupied zone'},
 'R-HEATING-OVERHEAD': {'status': 'not_implemented',
                        'source': 'ASHRAE Applications p.57.5',
                        'rule': 'Overhead heating: delta-T below 8.5 K and jet velocity >= 0.75 '
                                'm/s at 1.5 m above floor; T0.25/L method is for cooling only'},
 'R-VAV': {'status': 'not_implemented',
           'source': 'ASHRAE Applications p.57.5; SMACNA ch.3',
           'rule': 'Evaluate at minimum occupied and maximum design flow'},
 'R-THROW-OPPOSITE-WALLS': {'status': 'supported',
                            'source': 'SMACNA printed p.3.9',
                            'rule': 'Outlets on opposite walls: throw = 1/2 wall-to-wall distance '
                                    '(not a ceiling-grid rule)'},
 'R-PROCEDURE': {'status': 'supported',
                 'source': 'ASHRAE Applications 2015 printed p.57.6 (T0.25/L Selection Method)',
                 'steps': ['1 air flow from load/room size (VAV: min and max)',
                           '2 tentative diffuser type and location',
                           '3 characteristic length L (Table 3)',
                           '4 recommended T0.25/L from Table 4',
                           '5 required throw T0.25 = ratio x L',
                           '6 pick size from catalog',
                           '7 verify noise and static pressure loss'],
                 'note': 'Method is a general tool, not meant for every outlet on a project'},
 'R-NC-BASIS': {'status': 'unresolved',
                'source': 'catalog p.3 vs p.10',
                'issue': 'p.3: NC based on 10 dB room attenuation, 10 ft from face (-3 dB at 20 '
                         'ft, -7 dB at 30 ft). p.10: tables are sound-power NC; subtract room '
                         'attenuation to get room NC. catalog_data.py docstring says 8 dB for '
                         'square diffusers.',
                'code': 'Table NC is compared directly with the room NC limit (p.3 reading, '
                        'conservative vs p.10)'}}
