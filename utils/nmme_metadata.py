from typing import Dict, Any, List, Tuple

def init_models() -> Tuple[List[Dict[str, Any]], Dict[str, str], List[str], Dict[str, str]]:
    models = [
        {"model":"NASA-GEOSS2S", "varnames":["prec","olr","tref","sst","h500","h200"], "levstrs":["sfc","toa","2m","sfc","500","200"]},
        {"model":"CanESM5",      "varnames":["prec","olr","tref","sst","h500","h200"], "levstrs":["sfc","toa","2m","sfc","500","200"]},
        {"model":"GFDL-SPEAR",   "varnames":["prec","olr","tref","sst","h500","h200"], "levstrs":["sfc","toa","2m","sfc","500","200"]},
        {"model":"GEM5.2-NEMO",  "varnames":["prec","olr","tref","sst","h500","h200"], "levstrs":["sfc","toa","2m","sfc","500","200"]},
        {"model":"NCEP-CFSv2",   "varnames":["prec","olr","tref","sst","h500","h200"], "levstrs":["sfc","toa","2m","sfc","500","200"]},
        {"model":"COLA-RSMAS-CCSM4","varnames":["prec","olr","tref","sst","h500","h200"],"levstrs":["sfc","toa","2m","sfc","500","200"]},
        {"model":"COLA-RSMAS-CESM1","varnames":["prec","olr","tref","sst","h500","h200"],"levstrs":["sfc","toa","2m","sfc","500","200"]},
    ]
    vnames = {v: v for v in ["prec","olr","tref","sst","h500","h200"]}
    levs   = ["sfc","toa","2m","sfc","500","200"]
    units  = {"prec":"mm/day","olr":"W/m^2","tref":"K","sst":"K","h500":"m","h200":"m"}
    return models, vnames, levs, units
