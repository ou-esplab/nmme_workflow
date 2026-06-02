# NMME Unified Workflow (Monthly)

## Environment Setup

Preferred (exact, reproducible):
```bash
conda create -n nmme_workflow_env --file environment.from-pycpt-2.8.2.yml
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

- Default run executes: `ingest`, `preprocess`, `products`, `pycpt`.
- `arraylake` is opt-in and must be requested explicitly with `--stages arraylake`.
- `publish` is optional and must be requested explicitly with `--stages ... publish`.
- Place your real `confignmme.yaml` here if the placeholder was created.

## Regression Checklist

Use [docs/REGRESSION_CHECKLIST.md](docs/REGRESSION_CHECKLIST.md) after refactors and dependency changes.

Outputs and logs are written under `logs/YYYYMMDD_HHMMSS/nmme/<init>/`.
