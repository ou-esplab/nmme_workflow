#!/usr/bin/env python3

import argparse
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from uuid import uuid4

import xarray as xr

from utils.nmme_normalize import normalize_forecast_dataset


HEIGHT_ALIASES = {"gz", "hgt", "zg"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize NMME forecast file variable names and height levels."
    )
    parser.add_argument(
        "--root",
        default="/data/esplab/nmme-backup",
        help="Forecast archive root used when scanning or writing canonical files.",
    )
    parser.add_argument(
        "--file",
        help="Single forecast file to normalize.",
    )
    parser.add_argument(
        "--model",
        help="Model name for --file mode. Parsed from the file name when omitted.",
    )
    parser.add_argument(
        "--requested-var",
        help="Canonical workflow variable for --file mode, such as h200 or h500.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Apply changes. Without this flag the script only reports planned actions.",
    )
    parser.add_argument(
        "--delete-legacy",
        action="store_true",
        help="Delete legacy source files after canonical outputs are written.",
    )
    parser.add_argument(
        "--models",
        nargs="*",
        help="Optional model filter when scanning the archive.",
    )
    return parser.parse_args()


def parse_forecast_filename(path: Path) -> Tuple[str, str, str, str]:
    stem_parts = path.stem.split("_")
    if len(stem_parts) < 4:
        raise ValueError(f"Unexpected forecast filename: {path.name}")

    src_var = stem_parts[0]
    year = stem_parts[-2]
    month = stem_parts[-1]
    model = "_".join(stem_parts[1:-2])
    return src_var, model, year, month


def canonical_targets_for_file(
    src_var: str,
    ds: xr.Dataset,
    requested_var: Optional[str],
) -> Sequence[str]:
    if requested_var:
        return [requested_var]

    if src_var == "gz":
        return ["h200"]

    if src_var in {"h200", "h500"}:
        return [src_var]

    if src_var in {"hgt", "zg"}:
        if "P" not in ds:
            raise ValueError("Height file is missing a pressure coordinate")

        p_values = {int(value) for value in ds["P"].values.reshape(-1).tolist()}
        targets = []
        if 500 in p_values:
            targets.append("h500")
        if 200 in p_values:
            targets.append("h200")
        if not targets:
            raise ValueError(
                f"Unsupported pressure levels for {src_var}: {sorted(p_values)}"
            )
        return targets

    return [src_var]


def build_output_path(
    root: Path,
    model: str,
    requested_var: str,
    year: str,
    month: str,
) -> Path:
    return (
        root
        / model
        / "forecast"
        / requested_var
        / f"{requested_var}_{model}_{year}_{month}.nc"
    )


def sanitize_for_write(ds: xr.Dataset) -> xr.Dataset:
    ds = ds.copy()
    for name in ds.variables:
        attrs = ds[name].attrs
        if "_FillValue" in attrs and "missing_value" in attrs:
            attrs.pop("missing_value", None)
    return ds



def write_dataset(ds: xr.Dataset, target_path: Path) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_name(
        f".{target_path.name}.{uuid4().hex}.tmp"
    )
    ds = sanitize_for_write(ds)
    ds.load()
    ds.to_netcdf(tmp_path)
    tmp_path.replace(target_path)


def normalize_file(
    path: Path,
    root: Path,
    model: Optional[str] = None,
    requested_var: Optional[str] = None,
    write: bool = False,
    delete_legacy: bool = False,
) -> List[str]:
    src_var, parsed_model, year, month = parse_forecast_filename(path)
    model = model or parsed_model

    messages = []
    outputs: List[Path] = []

    try:
        with xr.open_dataset(path, decode_times=False) as ds:
            targets = canonical_targets_for_file(src_var, ds, requested_var)

            for target_var in targets:
                normalized = normalize_forecast_dataset(ds, model, target_var)
                if normalized is None:
                    raise ValueError(
                        f"Could not normalize {path.name} for target variable {target_var}"
                    )

                target_path = build_output_path(root, model, target_var, year, month)
                action = "rewrite" if target_path == path else "write"
                messages.append(f"{action}: {path} -> {target_path}")
                outputs.append(target_path)

                if write:
                    write_dataset(normalized, target_path)
    except FileNotFoundError:
        return []

    if write and delete_legacy and path not in outputs and path.exists():
        path.unlink()
        messages.append(f"delete: {path}")

    return messages


def iter_scan_files(root: Path, model_filters: Optional[Iterable[str]]) -> Iterable[Path]:
    allowed_models = set(model_filters or [])
    for model_dir in sorted(root.iterdir()):
        if not model_dir.is_dir():
            continue
        if allowed_models and model_dir.name not in allowed_models:
            continue
        forecast_dir = model_dir / "forecast"
        if not forecast_dir.is_dir():
            continue
        for var_dir in sorted(forecast_dir.iterdir()):
            if not var_dir.is_dir():
                continue
            if var_dir.name not in HEIGHT_ALIASES:
                continue
            for path in sorted(var_dir.glob("*.nc")):
                yield path


def main() -> int:
    args = parse_args()
    root = Path(args.root)

    messages: List[str] = []
    files: Sequence[Path]
    if args.file:
        files = [Path(args.file)]
    else:
        files = list(iter_scan_files(root, args.models))

    for path in files:
        if not path.exists():
            continue
        try:
            messages.extend(
                normalize_file(
                    path=path,
                    root=root,
                    model=args.model,
                    requested_var=args.requested_var,
                    write=args.write,
                    delete_legacy=args.delete_legacy,
                )
            )
        except Exception as exc:
            print(f"ERROR: {path}: {exc}")
            return 1

    if not messages:
        print("No matching forecast files found.")
        return 0

    summary: Dict[str, int] = {"write": 0, "rewrite": 0, "delete": 0}
    for message in messages:
        print(message)
        verb = message.split(":", 1)[0]
        if verb in summary:
            summary[verb] += 1

    print(
        "SUMMARY: "
        f"write={summary['write']} "
        f"rewrite={summary['rewrite']} "
        f"delete={summary['delete']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())