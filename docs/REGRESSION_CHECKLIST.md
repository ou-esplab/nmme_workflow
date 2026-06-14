# NMME Workflow Regression Checklist

Use this checklist after refactors, dependency updates, or runner changes.

## 0. Static Files Validation (Prerequisites)

Before running any stages, verify static files are available:

```bash
python scripts/check_static_files.py --config confignmme.yaml \
	--climatology-root /data/esplab/shared/model/initialized/nmme/climatology/monthly/1991-2020 \
	--tercile-root tercile_thresholds --verbose
```

Expected:
- All climatology files show "✓ VALID"
- All tercile files show "✓ VALID"
- Exit code: 0

If any files are invalid:
```bash
# Generate static files
./scripts/run_nmme_workflow.sh --stages climatology terciles
# Then re-run check above
```

---

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

## 2. Runner Validation: Ingest

```bash
python runners/cli.py --system nmme --config confignmme.yaml --init 202601 --stages ingest
```

Expected:
- command exits 0
- ingest log is created under `logs/.../nmme/202601/01_ingest.log`
- no ingest traceback

## 3. Runner Validation: Preprocess

```bash
python runners/cli.py --system nmme --config confignmme.yaml --init 202601 --stages preprocess
```

Expected:
- command exits 0
- no preprocess invariant/normalization traceback

## 4. Runner Dry-Run: PyCPT

```bash
python runners/cli.py --system nmme --config confignmme.yaml --init 202601 --stages pycpt --pycpt-dry-run
```

Expected:
- command exits 0
- log path created under `logs/.../nmme/202601/`

## 5. Runner Dry-Run: Products

```bash
python runners/cli.py --system nmme --config confignmme.yaml --init 202601 --stages products --products-direct --products-dry-run
```

Expected:
- command exits 0
- no plotting import errors

## 6. Full Runner Workflow

```bash
python runners/cli.py --system nmme --config confignmme.yaml --init 202601
```

Expected:
- runner runs default stages: `ingest`, `preprocess`, `products`, `pycpt`
- no uncaught runner exception

## 7. Runner Dry-Run: PyCPT Maps

```bash
python runners/cli.py --system nmme --config confignmme.yaml --init 202601 --stages pycpt_maps --pycpt-dry-run
```

Expected:
- command exits 0
- inputs built and logged; no map files written

## 8. Skill Static Stage (One-Time)

To validate the skill computation scripts parse without error:

```bash
python static/skill/compute_rpss.py --help
python static/skill/compute_acc.py --help
```

Expected: help text printed, no import errors.

To run the full skill computation (long-running; requires large memory machine such as esplab-0-2):

```bash
# Use a screen session on esplab-0-2
bash static/skill/run_skill.sh
```

Expected:
- RPSS and ACC NetCDF files created under `/data/esplab/shared/model/initialized/nmme/skill/1991-2020/`
- One file per model per variable per metric: `{model}.{var}.rpss.1991-2020.nc`, `{model}.{var}.acc.1991-2020.nc`
- Plots written to `publish/skill/plots/`

## 9. Arraylake Dry-Run (Optional)

```bash
python runners/cli.py --system nmme --config confignmme.yaml --init 202601 --stages arraylake --arraylake-dry-run
```

Expected:
- command exits 0
- Arraylake log is created under `logs/.../nmme/202601/`
- no Arraylake write/commit side effects

## 10. Publish Stage Dry-Run (Optional)

```bash
python runners/cli.py --system nmme --config confignmme.yaml --init 202601 --stages publish --publish-dry-run
```

Expected:
- command exits 0
- publish log generated without SSH/SCP side effects

## 11. Log Review

Check latest stage logs:

- `logs/<stamp>/nmme/<init>/01_ingest.log`
- `logs/<stamp>/nmme/<init>/02_preprocess.log`
- `logs/<stamp>/nmme/<init>/03products1.log`
- `logs/<stamp>/nmme/<init>/03products2.log`
- `logs/<stamp>/nmme/<init>/04_pycpt.log`

Acceptable warnings:
- model-variable missing file/var messages for unavailable sources

Blocking errors:
- traceback / RuntimeError / command failed exit codes
