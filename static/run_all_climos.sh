#!/bin/bash
# Run climatology for all models and variables
declare -A MODELS
MODELS[NASA-GEOSS2S]="prec tref sst h500 h200"
MODELS[CanESM5]="prec tref sst h500 h200"
MODELS[GEM5.2-NEMO]="prec tref sst h500 h200"
MODELS[NCEP-CFSv2]="prec tref sst h500 h200"
MODELS[COLA-RSMAS-CCSM4]="prec tref sst h500 h200"
MODELS[COLA-RSMAS-CESM1]="prec tref sst h500 h200"
MODELS[NOAA-SFS]="prec tref sst"

for model in "${!MODELS[@]}"; do
  for var in ${MODELS[$model]}; do
    if [ "$model" = "NOAA-SFS" ]; then
      input_dir="/data/esplab/nmme-backup/${model}/reforecast/${var}"
    else
      input_dir="/data/esplab/nmme-backup/${model}/hindcast/${var}"
    fi
    output_file="/data/esplab/shared/model/initialized/nmme/climatology/monthly/1991-2020/${model}.${var}_sfc.clim.1991-2020.nc"
    echo "Running climatology for $model $var (input: $input_dir, output: $output_file)"
    python make_sfs_climo_from_reforecast.py --model "$model" --local-var "$var" --input-dir "$input_dir" --output-file "$output_file"
  done
done
