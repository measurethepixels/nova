"""Settings-free catalog identity mappings shared by pure name logic.

Keep this module free of application configuration and database imports so
callers such as folio canonicalization remain usable in headless tooling and CI.
"""

from __future__ import annotations

# Messier → NGC/IC canonical mapping (bidirectional)
_MESSIER_NGC: dict[str, str] = {
    "M 1": "NGC 1952", "M 2": "NGC 7089", "M 3": "NGC 5272", "M 4": "NGC 6121",
    "M 5": "NGC 5904", "M 6": "NGC 6405", "M 7": "NGC 6475", "M 8": "NGC 6523",
    "M 9": "NGC 6333", "M 10": "NGC 6254", "M 11": "NGC 6705", "M 12": "NGC 6218",
    "M 13": "NGC 6205", "M 14": "NGC 6402", "M 15": "NGC 7078", "M 16": "NGC 6611",
    "M 17": "NGC 6618", "M 18": "NGC 6613", "M 19": "NGC 6273", "M 20": "NGC 6514",
    "M 21": "NGC 6531", "M 22": "NGC 6656", "M 23": "NGC 6494", "M 24": "NGC 6603",
    "M 25": "IC 4725", "M 26": "NGC 6694", "M 27": "NGC 6853", "M 28": "NGC 6626",
    "M 29": "NGC 6913", "M 30": "NGC 7099", "M 31": "NGC 224", "M 32": "NGC 221",
    "M 33": "NGC 598", "M 34": "NGC 1039", "M 35": "NGC 2168", "M 36": "NGC 1960",
    "M 37": "NGC 2099", "M 38": "NGC 1912", "M 39": "NGC 7092", "M 41": "NGC 2287",
    "M 42": "NGC 1976", "M 43": "NGC 1982", "M 44": "NGC 2632", "M 45": "Mel 22",
    "M 46": "NGC 2437", "M 47": "NGC 2422", "M 48": "NGC 2548", "M 49": "NGC 4472",
    "M 50": "NGC 2323", "M 51": "NGC 5194", "M 52": "NGC 7654", "M 53": "NGC 5024",
    "M 54": "NGC 6715", "M 55": "NGC 6809", "M 56": "NGC 6779", "M 57": "NGC 6720",
    "M 58": "NGC 4579", "M 59": "NGC 4621", "M 60": "NGC 4649", "M 61": "NGC 4303",
    "M 62": "NGC 6266", "M 63": "NGC 5055", "M 64": "NGC 4826", "M 65": "NGC 3623",
    "M 66": "NGC 3627", "M 67": "NGC 2682", "M 68": "NGC 4590", "M 69": "NGC 6637",
    "M 70": "NGC 6681", "M 71": "NGC 6838", "M 72": "NGC 6981", "M 74": "NGC 628",
    "M 75": "NGC 6864", "M 76": "NGC 650", "M 77": "NGC 1068", "M 78": "NGC 2068",
    "M 79": "NGC 1904", "M 80": "NGC 6093", "M 81": "NGC 3031", "M 82": "NGC 3034",
    "M 83": "NGC 5236", "M 84": "NGC 4374", "M 85": "NGC 4382", "M 86": "NGC 4406",
    "M 87": "NGC 4486", "M 88": "NGC 4501", "M 89": "NGC 4552", "M 90": "NGC 4569",
    "M 91": "NGC 4548", "M 92": "NGC 6341", "M 93": "NGC 2447", "M 94": "NGC 4736",
    "M 95": "NGC 3351", "M 96": "NGC 3368", "M 97": "NGC 3587", "M 98": "NGC 4192",
    "M 99": "NGC 4254", "M 100": "NGC 4321", "M 101": "NGC 5457", "M 102": "NGC 5866",
    "M 103": "NGC 581", "M 104": "NGC 4594", "M 105": "NGC 3379", "M 106": "NGC 4258",
    "M 107": "NGC 6171", "M 108": "NGC 3556", "M 109": "NGC 3992", "M 110": "NGC 205",
}

# Caldwell → NGC/IC mapping (from caldwell_associations.py)
_CALDWELL_NGC: dict[str, str] = {
    "C 1": "NGC 188", "C 2": "NGC 40", "C 3": "NGC 4236", "C 4": "NGC 7023",
    "C 5": "IC 342", "C 6": "NGC 6543", "C 7": "NGC 2403", "C 8": "NGC 559",
    "C 9": "Sh 2-155", "C 10": "NGC 663", "C 11": "NGC 7635", "C 12": "NGC 6946",
    "C 13": "NGC 457", "C 14": "NGC 869", "C 15": "NGC 6826", "C 16": "NGC 7243",
    "C 17": "NGC 147", "C 18": "NGC 185", "C 19": "IC 5146", "C 20": "NGC 7000",
    "C 21": "NGC 4449", "C 22": "NGC 7662", "C 23": "NGC 891", "C 24": "NGC 1275",
    "C 25": "NGC 2419", "C 26": "NGC 4244", "C 27": "NGC 6888", "C 28": "NGC 752",
    "C 29": "NGC 5005", "C 30": "NGC 7331", "C 31": "IC 405", "C 32": "NGC 4631",
    "C 33": "NGC 6992", "C 34": "NGC 6960", "C 35": "NGC 4889", "C 36": "NGC 4559",
    "C 37": "NGC 6885", "C 38": "NGC 4565", "C 39": "NGC 2392", "C 40": "NGC 3626",
    "C 41": "NGC 7006", "C 42": "NGC 7009", "C 43": "NGC 7814", "C 44": "NGC 7479",
    "C 45": "NGC 5248", "C 46": "NGC 2261", "C 47": "NGC 6934", "C 48": "NGC 2775",
    "C 49": "NGC 2237", "C 50": "NGC 2244", "C 51": "NGC 5195", "C 52": "NGC 4697",
    "C 53": "NGC 3115", "C 54": "NGC 2506", "C 56": "NGC 246", "C 57": "NGC 6822",
    "C 58": "NGC 2360", "C 59": "NGC 3242", "C 60": "NGC 4039", "C 61": "NGC 4038",
    "C 62": "NGC 247", "C 63": "NGC 7293", "C 64": "NGC 2362", "C 65": "NGC 253",
    "C 66": "NGC 5694", "C 67": "NGC 1097", "C 68": "NGC 6729", "C 69": "NGC 6302",
    "C 70": "NGC 300", "C 71": "NGC 2477", "C 72": "NGC 55", "C 73": "NGC 1851",
    "C 74": "NGC 3132", "C 75": "NGC 6124", "C 76": "NGC 6231", "C 77": "NGC 5128",
    "C 78": "NGC 6541", "C 79": "NGC 3201", "C 80": "NGC 5139", "C 81": "NGC 6352",
    "C 82": "NGC 6193", "C 83": "NGC 4945", "C 84": "NGC 5286", "C 85": "IC 2391",
    "C 86": "NGC 6397", "C 88": "NGC 5823", "C 89": "NGC 6067", "C 90": "NGC 2867",
    "C 91": "NGC 3532", "C 92": "NGC 3372", "C 93": "NGC 6752", "C 94": "NGC 4755",
    "C 95": "NGC 6025", "C 96": "NGC 2516", "C 97": "NGC 3766", "C 98": "NGC 4609",
    "C 100": "NGC 3699", "C 101": "NGC 6744", "C 102": "NGC 3504", "C 103": "NGC 2070",
    "C 104": "NGC 104", "C 105": "NGC 4833", "C 107": "NGC 6101", "C 108": "NGC 4372",
    "C 109": "NGC 3195",
}
