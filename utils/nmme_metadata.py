from typing import Dict, Any, List, Tuple

def init_models() -> Tuple[List[Dict[str, Any]], Dict[str, str], List[str], Dict[str, str]]:
    models = [
        {"model":"NASA-GEOSS2S", "varnames":["prec","tref","sst","h500","h200"], "levstrs":["sfc","2m","sfc","500","200"]},
        {"model":"CanESM5",      "varnames":["prec","tref","sst","h500","h200"], "levstrs":["sfc","2m","sfc","500","200"]},
        {"model":"GEM5.2-NEMO",  "varnames":["prec","tref","sst","h500","h200"], "levstrs":["sfc","2m","sfc","500","200"]},
        {"model":"NCEP-CFSv2",   "varnames":["prec","tref","sst","h500","h200"], "levstrs":["sfc","2m","sfc","500","200"]},
        {"model":"COLA-RSMAS-CCSM4","varnames":["prec","tref","sst","h500","h200"],"levstrs":["sfc","2m","sfc","500","200"]},
        {"model":"COLA-RSMAS-CESM1","varnames":["prec","tref","sst","h500","h200"],"levstrs":["sfc","2m","sfc","500","200"]},
        {"model":"NOAA-SFS", "varnames":["prec","tref","sst"], "levstrs":["sfc","2m","sfc"]},
    ]
    vnames = {v: v for v in ["prec","tref","sst","h500","h200"]}
    levs   = ["sfc","2m","sfc","500","200"]
    units  = {"prec":"mm/day","tref":"K","sst":"K","h500":"m","h200":"m"}
    return models, vnames, levs, units
