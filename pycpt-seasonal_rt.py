#!/usr/bin/env python3
"""
pycpt-seasonal_rt.py (local-only, explicit-lead CPT)

Driver that:
  • Parses YAML + CLI
  • Resolves region + models
  • Loads local predictand, hindcasts, forecasts
  • Selects ONE lead following PyCPT logic
  • Stacks ensemble members into CPT feature axis
  • Prepares predictand as seasonal-mean anomalies
  • Converts to CPTv10 naming
  • Runs CPT-Core CCA directly
"""

from __future__ import annotations

from pathlib import Path
import argparse
import datetime as dt

import nmme_pycpt_utils as U
from cptcore.functional import cca


def main() -> int:
    # ---------------- CLI ----------------
    ap = argparse.ArgumentParser(description="Run CPT seasonal CCA using local NMME data")
    ap.add_argument("config", type=Path, help="Path to confignmme.yaml")
    ap.add_argument("fcstdate", help="Forecast initialization date YYYY-MM-DD or YYYYMM")
    ap.add_argument("--only", required=True, help="Region name (exact match)")
    args = ap.parse_args()

    # Normalize forecast date
    if len(args.fcstdate) == 6:
        fdate = dt.datetime.strptime(args.fcstdate, "%Y%m")
    else:
        fdate = dt.datetime.fromisoformat(args.fcstdate)

    # --------------- Config --------------
    cfg = U.load_config(args.config)
    regions = cfg["pycpt_regions"]
    models_global = cfg["models"]
    data_cfg = cfg["data"]
    local_cfg = data_cfg["local"]

    if not local_cfg.get("enabled", False):
        raise RuntimeError("Local data is disabled in YAML (data.local.enabled=false).")

    # Resolve region + models
    region = U.get_region(regions, args.only)
    models_used = U.resolve_models(models_global, region)

    print(f"[INFO] Region: {region['name']}")
    print(f"[INFO] Models: {', '.join(models_used)}")

    # --------- Local paths ---------
    root = Path(local_cfg["root"])
    pred_dir = local_cfg["predictand"]["dir"]
    pred_var = local_cfg["predictand"]["var"]
    model_var = local_cfg["model_vars"]["precipitation"]
    model_base_map = local_cfg.get("model_base", {})

    patterns = local_cfg.get(
        "path_patterns",
        {
            "hindcast": "{root}/{model}/hindcast/{var}/{var}_{model}_????_??.nc",
            "forecast": "{root}/{model}/forecast/{var}/{var}_{model}_{yyyy}_{mm}.nc",
        },
    )

    # --------- Load predictand (daily/monthly) ---------
    Y_raw = U.load_predictand_local(root, pred_dir, pred_var)

    # --------- Load hindcasts & forecasts ---------
    hindcasts = []
    forecasts = []

    for m in models_used:
        base = model_base_map.get(m, m)
        hc, fc = U.load_model_local_with_patterns(
            root=root,
            model_base=base,
            init_yyyymm=fdate.strftime("%Y%m"),
            var=model_var,
            patterns=patterns,
        )
        hindcasts.append(hc)
        forecasts.append(fc)

    # --------- Single-model workflow (CPT-style) ---------
    model_hcst = hindcasts[0]

    # --------- Select lead (PyCPT-consistent logic) ---------
    selected_L = U.select_lead(
        fdate=fdate,
        season=region["season"],
        L_coord=model_hcst["L"].values,
    )
    print(f"[INFO] Selected lead L = {selected_L} months")

    # Slice to one lead
    X_L = model_hcst.sel(L=selected_L)

    # --------- Prepare predictand (seasonal anomalies) ---------
    hindcast_years = X_L["S"].dt.year.values

    Y = U.prepare_predictand_for_cpt(
        Y=Y_raw,
        season=region["season"],
        hindcast_years=hindcast_years,
    )

    # --------- Stack ensemble members as CPT features ---------
    X = (
        X_L
        .stack(C=("M",))
        .transpose("S", "C", "Y", "X")
    )

    # --------- Final analysis-space checks ---------
    print("\n=== FINAL ANALYSIS INPUT CHECK ===")
    print("X (analysis) dims:", X.dims)
    print("Y (analysis) dims:", Y.dims)
    print("================================\n")

    assert X.dims == ("S", "C", "Y", "X")
    assert Y.dims == ("S", "Y", "X")

    # --------- Convert to CPTv10 naming (utility boundary) ---------
    X_v10, Y_v10 = U.to_cptv10(X=X, Y=Y)

    print("\n=== CPTv10 INPUT CHECK ===")
    print("X (CPTv10) dims:", X_v10.dims)
    print("Y (CPTv10) dims:", Y_v10.dims)
    print("=========================\n")

    assert X_v10.dims == ("T", "C", "row", "col")
    assert Y_v10.dims == ("T", "row", "col")

    # --------- Run CPT-Core CCA ---------
    cca_h, cca_rtf, cca_s, cca_px, cca_py = cca.canonical_correlation_analysis(
        X_v10,
        Y_v10,
    )

    print("[INFO] CPT-Core CCA complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
