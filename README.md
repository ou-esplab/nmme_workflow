# NMME Unified Workflow (Monthly)

## Run (using unified runner)
```bash
python3 runners/cli.py --system nmme --config confignmme.yaml --init YYYYMM
```

- Stages: `ingest`, `products`, `pycpt` (default runs all).
- Place your real `confignmme.yaml` here if the placeholder was created.
- Shell entry (optional): `./nmme_pipeline.sh` uses nmme_fcst_utils + locks.

Outputs and logs are written under `logs/YYYYMMDD_HHMMSS/nmme/<init>/`.
