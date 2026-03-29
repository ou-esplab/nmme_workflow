# NMME Workflow Regression Checklist

Use this checklist after refactors, dependency updates, or runner changes.

## 1. Environment

```bash
source ~/miniconda3/etc/profile.d/conda.sh
conda activate nmme_workflow_env
which CPT.x
python -c "import cptcore, cptio; print('ok')"
```

Expected:
- `CPT.x` exists in PATH.
- Python import prints `ok`.

## 2. Runner Dry-Run: PyCPT

```bash
python runners/cli.py --system nmme --config confignmme.yaml --init 202601 --stages pycpt --pycpt-dry-run
```

Expected:
- command exits 0
- log path created under `logs/.../nmme/202601/`

## 3. Runner Dry-Run: Products

```bash
python runners/cli.py --system nmme --config confignmme.yaml --init 202601 --stages products --products-direct --products-dry-run
```

Expected:
- command exits 0
- no plotting import errors

## 4. Full Runner Workflow

```bash
python runners/cli.py --system nmme --config confignmme.yaml --init 202601
```

Expected:
- runner prints all three stages: `ingest`, `products`, `pycpt`
- no uncaught runner exception

## 5. Log Review

Check latest stage logs:

- `logs/<stamp>/nmme/<init>/01_ingest.log`
- `logs/<stamp>/nmme/<init>/02_products.log`
- `logs/<stamp>/nmme/<init>/03_pycpt.log`

Acceptable warnings:
- model-variable missing file/var messages for unavailable sources

Blocking errors:
- traceback / RuntimeError / command failed exit codes
