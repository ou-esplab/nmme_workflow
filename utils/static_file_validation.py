#!/usr/bin/env python3
"""Static file validation utilities for NMME workflow."""

from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
import xarray as xr

def validate_climatology_file(fpath: Path, var: str, verbose: bool = False) -> Tuple[bool, str]:
    """Validate a single climatology file."""
    if not fpath.exists():
        return False, f"File does not exist"
    try:
        ds = xr.open_dataset(fpath)
        if var not in ds.data_vars:
            ds.close()
            return False, f"Variable '{var}' not found"
        data_array = ds[var]
        dims = set(data_array.dims)
        lat_ok = "latitude" in dims or "lat" in dims
        lon_ok = "longitude" in dims or "lon" in dims
        if not lat_ok or not lon_ok or "lead" not in dims:
            ds.close()
            return False, f"Missing required dimensions"
        if data_array.isnull().all():
            ds.close()
            return False, f"Variable is entirely NaN"
        ds.close()
        msg = f"Valid climatology"
        return True, msg
    except Exception as e:
        return False, f"Error: {str(e)[:60]}"

def validate_tercile_file(fpath: Path, verbose: bool = False) -> Tuple[bool, str]:
    """Validate a single tercile threshold file."""
    if not fpath.exists():
        return False, f"File does not exist"
    try:
        ds = xr.open_dataset(fpath)
        if "t33" not in ds.data_vars or "t66" not in ds.data_vars:
            ds.close()
            return False, f"Missing tercile variables"
        if ds["t33"].isnull().all() or ds["t66"].isnull().all():
            ds.close()
            return False, f"All NaN"
        ds.close()
        return True, f"Valid tercile file"
    except Exception as e:
        return False, f"Error: {str(e)[:60]}"

def check_all_climatology_files(models: List[str], variables: List[str], clim_root: Path, verbose: bool = False) -> Dict[str, Dict[str, Tuple[bool, str]]]:
    """Check all climatology files."""
    results = {}
    for model in models:
        results[model] = {}
        for var in variables:
            if var == "prec":
                lev = "sfc"
            elif var == "tref":
                lev = "2m"
            elif var == "sst":
                lev = "sfc"
            else:
                lev = None
            if lev:
                fpath = clim_root / f"{model}.{var}_{lev}.clim.1991-2020.nc"
            else:
                fpath = clim_root / f"{model}.{var}.clim.1991-2020.nc"
            is_valid, msg = validate_climatology_file(fpath, var, verbose=verbose)
            results[model][var] = (is_valid, msg)
    return results

def check_all_tercile_files(models: List[str], variables: List[str], seasons: Dict[str, Tuple[int, int]], tercile_root: Path, verbose: bool = False) -> Dict[str, Dict[str, Dict[str, Tuple[bool, str]]]]:
    """Check all tercile files."""
    results = {}
    for model in models:
        results[model] = {}
        for var in variables:
            results[model][var] = {}
            for season in seasons.keys():
                fname = f"{model}.{var}.{season}.terciles.1991-2020.nc"
                fpath = tercile_root / fname
                is_valid, msg = validate_tercile_file(fpath, verbose=verbose)
                results[model][var][season] = (is_valid, msg)
    return results

def report_validation_status(models: List[str], variables: List[str], seasons: Optional[Dict[str, Tuple[int, int]]] = None, clim_root: Optional[Path] = None, tercile_root: Optional[Path] = None, verbose: bool = False) -> Dict[str, Any]:
    """Generate comprehensive validation report."""
    report = {}
    summary_lines = []
    
    if clim_root:
        clim_root = Path(clim_root)
        clim_status = check_all_climatology_files(models, variables, clim_root, verbose=verbose)
        report["climatology_status"] = clim_status
        total_clim = sum(1 for m in clim_status.values() for _ in m.values())
        valid_clim = sum(1 for m in clim_status.values() for is_valid, _ in m.values() if is_valid)
        report["climatology_valid"] = (valid_clim, total_clim)
        summary_lines.append(f"Climatology: {valid_clim}/{total_clim}")
    
    if tercile_root and seasons:
        tercile_root = Path(tercile_root)
        tercile_status = check_all_tercile_files(models, variables, seasons, tercile_root, verbose=verbose)
        report["tercile_status"] = tercile_status
        total_tercile = sum(1 for m in tercile_status.values() for v in m.values() for _ in v.values())
        valid_tercile = sum(1 for m in tercile_status.values() for v in m.values() for is_valid, _ in v.values() if is_valid)
        report["tercile_valid"] = (valid_tercile, total_tercile)
        summary_lines.append(f"Terciles: {valid_tercile}/{total_tercile}")
    
    report["summary"] = " | ".join(summary_lines)
    return report

def print_report(report: Dict[str, Any], verbose: bool = False) -> None:
    """Pretty-print validation report."""
    print("\n" + "=" * 70)
    print("STATIC FILE VALIDATION REPORT")
    print("=" * 70)
    
    if "climatology_valid" in report:
        valid, total = report["climatology_valid"]
        status = "✓ VALID" if valid == total else "✗ INVALID"
        print(f"\nCLIMATOLOGY FILES: {status} ({valid}/{total})")
        if verbose and "climatology_status" in report:
            for model, var_status in report["climatology_status"].items():
                for var, (is_valid, msg) in var_status.items():
                    symbol = "✓" if is_valid else "✗"
                    print(f"  [{symbol}] {model:20s} {var:8s}: {msg}")
    
    if "tercile_valid" in report:
        valid, total = report["tercile_valid"]
        status = "✓ VALID" if valid == total else "✗ INVALID"
        print(f"\nTERCILE FILES: {status} ({valid}/{total})")
        if verbose and "tercile_status" in report:
            for model, var_status in report["tercile_status"].items():
                for var, season_status in var_status.items():
                    for season, (is_valid, msg) in season_status.items():
                        symbol = "✓" if is_valid else "✗"
                        print(f"  [{symbol}] {model:20s} {var:8s} {season:6s}: {msg}")
    
    print(f"\nSUMMARY: {report.get('summary', 'N/A')}")
    print("=" * 70 + "\n")
