#!/usr/bin/env python3

import argparse
from pathlib import Path
from bs4 import BeautifulSoup


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="Forecast date YYYYMM")
    parser.add_argument("--input", default=None, help="Input HTML path")
    parser.add_argument("--output", default=None, help="Output HTML path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    fcstdate = args.date

    if len(fcstdate) != 6 or not fcstdate.isdigit():
        raise ValueError("--date must be YYYYMM")

    input_path = Path(args.input) if args.input else Path(f"forecasts.{fcstdate}.html")
    output_path = Path(args.output) if args.output else Path(f"output.{fcstdate}.html")

    with input_path.open("r", encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "html.parser")

    options = soup.find_all("option")
    if not options:
        raise RuntimeError("No <option> elements found in input HTML")

    existing = {opt.get_text(strip=True) for opt in options}
    if fcstdate not in existing:
        anchor = None
        for opt in options:
            if opt.get_text(strip=True).lower() == "latest":
                anchor = opt
                break
        if anchor is None:
            anchor = options[0]

        new_option = soup.new_tag("option")
        new_option.string = fcstdate
        anchor.insert_after(new_option)

    with output_path.open("w", encoding="utf-8") as f:
        print("Writing new forecasts.html file to", output_path)
        f.write(str(soup.prettify()))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
