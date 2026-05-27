#!/bin/bash
# Run climatology for all models and variables

declare -A MODELS
MODELS[NASA-GEOSS2S]="prec tref sst h500 h200"
MODELS[CanESM5]="prec tref sst h500 h200"
MODELS[GEM5.2-NEMO]="prec tref sst h500 h200"
MODELS[NCEP-CFSv2]="prec tref sst h500 h200"
MODELS[NCAR-CESM1]="prec tref sst h500 h200"
MODELS[COLA-RSMAS-CCSM4]="prec tref sst h500 h200"
MODELS[COLA-RSMAS-CESM1]="prec tref sst h200"   # no h500 data; hindcast ends 2010
MODELS[NOAA-SFS]="prec tref sst"

# Level suffix per variable — used to build the standard output filename.
declare -A LEVSTR
LEVSTR[prec]="sfc"
LEVSTR[tref]="2m"
LEVSTR[sst]="sfc"
LEVSTR[h200]="200"
LEVSTR[h500]="500"

CLIMO_OUT="/data/esplab/shared/model/initialized/nmme/climatology/monthly/1991-2020"

for model in "${!MODELS[@]}"; do
  for var in ${MODELS[$model]}; do
    if [ "$model" = "NOAA-SFS" ]; then
      input_dir="/data/esplab/nmme-backup/${model}/reforecast/${var}"
    else
      input_dir="/data/esplab/nmme-backup/${model}/hindcast/${var}"
    fi

    lev="${LEVSTR[$var]}"
    output_file="${CLIMO_OUT}/${model}.${var}_${lev}.clim.1991-2020.nc"

    # COLA-RSMAS-CESM1 hindcast only covers through 2010; cap end year accordingly.
    extra_args=""
    if [ "$model" = "COLA-RSMAS-CESM1" ]; then
      extra_args="--end-year 2010"
    fi

    echo "Running climatology for $model $var (input: $input_dir, output: $output_file)"
    python make_sfs_climo_from_reforecast.py \
      --model "$model" \
      --local-var "$var" \
      --input-dir "$input_dir" \
      --output-file "$output_file" \
      $extra_args
  done
done
