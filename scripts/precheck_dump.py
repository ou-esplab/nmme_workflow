#!/usr/bin/env python3
from pathlib import Path
import sys
sys.path.insert(0,'.')
print('START SCRIPT')
print('IMPORTS OK')
from utils.config import load_config
from utils.nmme_metadata import init_models
from utils.nmme_anomalies import model_anomalies_for_month
from utils.nmme_io import open_local_forecast
print('MODULES IMPORTED')
cfg = load_config('confignmme.yaml')
print('CONFIG LOADED')
fcst='202604'
pre_root = Path(cfg['data']['local']['preprocess_root'])/fcst/'preprocess'
print('pre_root',pre_root)
models,_,_,_ = init_models()
print('MODELS INITIALIZED')
for m in models:
    model = m['model']
    for var in m['varnames']:
        print('---')
        print('model',model,'var',var)
        path = pre_root / model / 'forecast' / var
        files = list(path.glob('*.nc')) if path.exists() else []
        print('files found:', len(files), 'example:', files[0] if files else None)
        try:
            ds_var = model_anomalies_for_month(pre_root, Path(cfg['data']['local'].get('climatology','/data/esplab/shared/model/initialized/nmme/climatology/monthly/1991-2020/')), model, var, m['levstrs'][m['varnames'].index(var)], fcst)
            print('result:', 'OK' if ds_var is not None else 'None')
            if ds_var is not None:
                print('ds_var dims:', ds_var.dims, 'vars:', list(ds_var.data_vars))
        except Exception as e:
            import traceback
            traceback.print_exc()
            print('EXCEPTION for', model, var, e)
print('END SCRIPT')
