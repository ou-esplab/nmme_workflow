#!/usr/bin/env python3
"""CLI tool for checking NMME static file status."""

import argparse
import sys
from pathlib import Path
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.static_file_validation import report_validation_status, print_report

def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Check and report status of NMME static files (climatology, terciles)"
    )
    parser.add_argument(
        "--config", type=str, default="confignmme.yaml",
        help="Path to YAML configuration file (default: confignmme.yaml)"
    )
    parser.add_argument(
        "--climatology-root", type=str, default=None,
        help="Root directory containing climatology files"
    )
    parser.add_argument(
        "--tercile-root", type=str, default="tercile_thresholds",
        help="Root directory containing tercile threshold files (default: tercile_thresholds)"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print detailed file-by-file validation status"
    )
    parser.add_argument(
        "--quiet", "-q", action="store_true",
        help="Only print summary"
    )
    return parser.parse_args()

def load_config(config_path: str) -> dict:
    """Load YAML configuration file."""
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with config_path.open("r") as f:
        cfg = yaml.safe_load(f)
    return cfg or {}

def main():
    """Main entry point."""
    args = parse_args()
    
    try:
        config = load_config(args.config)
        if not config:
            print("Error: Configuration file is empty", file=sys.stderr)
            return 1
        
        models = config.get("models", [])
        if not models:
            print("Error: No models defined in configuration", file=sys.stderr)
            return 1
        
        variables = ["prec", "tref", "sst"]
        seasons = {
            "MAM": (0, 3),
            "AMJ": (1, 4),
            "MJJ": (2, 5),
            "JJA": (3, 6),
            "ASO": (7, 10),
            "NDJ": (8, 11),
        }
        
        check_climatology = args.climatology_root is not None
        check_terciles = Path(args.tercile_root).exists() if args.tercile_root else False
        
        if not check_climatology and not check_terciles:
            print(
                "Warning: Neither --climatology-root provided nor tercile-root exists",
                file=sys.stderr
            )
            return 1
        
        report = report_validation_status(
            models=models,
            variables=variables,
            seasons=seasons if check_terciles else None,
            clim_root=Path(args.climatology_root) if check_climatology else None,
            tercile_root=Path(args.tercile_root) if check_terciles else None,
            verbose=args.verbose,
        )
        
        if not args.quiet:
            print_report(report, verbose=args.verbose)
        else:
            print(report.get("summary", "No validation performed"))
        
        all_valid = True
        if "climatology_valid" in report:
            valid, total = report["climatology_valid"]
            if valid != total:
                all_valid = False
        
        if "tercile_valid" in report:
            valid, total = report["tercile_valid"]
            if valid != total:
                all_valid = False
        
        return 0 if all_valid else 1
        
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except yaml.YAMLError as e:
        print(f"Error parsing YAML config: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
