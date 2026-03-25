from __future__ import annotations
from datetime import datetime
import numpy as np

_MONTH_MAP = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4,
    "May": 5, "Jun": 6, "Jul": 7, "Aug": 8,
    "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

def yyyymm_parts(yyyymm: str) -> tuple[str, str]:
    if len(yyyymm) != 6 or not yyyymm.isdigit():
        raise ValueError(f"Expected YYYYMM, got: {yyyymm}")
    return yyyymm[:4], yyyymm[4:]

def select_lead(fdate, season: str, L_coord) -> float:
    if isinstance(fdate, str):
        init_month = datetime.fromisoformat(fdate).month
    elif isinstance(fdate, datetime):
        init_month = fdate.month
    else:
        raise TypeError("fdate must be str or datetime")

    try:
        start_m, end_m = season.split("-")
        start_m = _MONTH_MAP[start_m]
        end_m = _MONTH_MAP[end_m]
    except Exception:
        raise ValueError("season must be like 'Feb-Apr'")

    if end_m >= start_m:
        season_months = range(start_m, end_m + 1)
    else:
        season_months = list(range(start_m, 13)) + list(range(1, end_m + 1))

    leads = []
    for m in season_months:
        lead = m - init_month
        if lead <= 0:
            lead += 12
        leads.append(lead)

    center = np.median(leads)
    L_coord = np.asarray(L_coord)
    if center in L_coord:
        return float(center)

    return float(L_coord[np.argmin(np.abs(L_coord - center))])
``
