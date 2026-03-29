# NMME Unified Workflow (Monthly)

## Environment Setup

Preferred (exact, reproducible):
```bash
conda create -n nmme_workflow_env --file environment.from-pycpt-2.8.2.lock.txt
conda activate nmme_workflow_env
```

Alternative (solve from YAML):
```bash
conda env create -f environment.yml -n nmme_workflow_env --solver=libmamba
conda activate nmme_workflow_env
```

Verify CPT dependencies in the active environment:
```bash
which CPT.x
python -c "import cptcore, cptio; print('ok')"
```

## Run (using unified runner)
```bash
python3 runners/cli.py --system nmme --config confignmme.yaml --init YYYYMM
```

- Stages: `ingest`, `products`, `pycpt` (default runs all).
- Place your real `confignmme.yaml` here if the placeholder was created.
- Legacy shell entry is deprecated: `./nmme_pipeline.sh` now forwards to `runners/cli.py`.

## Regression Checklist

Use [docs/REGRESSION_CHECKLIST.md](docs/REGRESSION_CHECKLIST.md) after refactors and dependency changes.

Outputs and logs are written under `logs/YYYYMMDD_HHMMSS/nmme/<init>/`.
