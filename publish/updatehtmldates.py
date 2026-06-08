#!/usr/bin/env python3

import argparse
from pathlib import Path
from bs4 import BeautifulSoup


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="Forecast date YYYYMM to ensure is present")
    parser.add_argument("--dates", default=None,
                        help="Whitespace-separated YYYYMM list — replaces all date options with this set")
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

    select_ids = ["fcstselectdate", "srselectdate"]

    for select_id in select_ids:
        select = soup.find("select", {"id": select_id})
        if not select:
            continue

        if args.dates is not None:
            # Replace date options with exactly the given list (sorted newest-first),
            # always keeping "Latest" as the first option.
            date_list = sorted(
                {d for d in args.dates.split() if len(d) == 6 and d.isdigit()} | {fcstdate},
                reverse=True,
            )
            for opt in select.find_all("option"):
                opt.decompose()
            latest_opt = soup.new_tag("option")
            latest_opt.string = "Latest"
            select.append(latest_opt)
            for d in date_list:
                opt = soup.new_tag("option")
                opt.string = d
                select.append(opt)
        else:
            # Legacy behaviour: just insert fcstdate if missing
            options = select.find_all("option")
            existing = {opt.get_text(strip=True) for opt in options}
            if fcstdate not in existing:
                anchor = next(
                    (o for o in options if o.get_text(strip=True).lower() == "latest"),
                    options[0] if options else None,
                )
                new_option = soup.new_tag("option")
                new_option.string = fcstdate
                if anchor:
                    anchor.insert_after(new_option)
                else:
                    select.append(new_option)

    if not soup.find("select", {"id": "fcstselectdate"}):
        raise RuntimeError("No <select id='fcstselectdate'> found in input HTML")

    with output_path.open("w", encoding="utf-8") as f:
        print("Writing new forecasts.html file to", output_path)
        f.write(str(soup.prettify()))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
