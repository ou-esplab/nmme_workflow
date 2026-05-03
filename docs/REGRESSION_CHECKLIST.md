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

## 2. Runner Validation: Preprocess

```bash
python runners/cli.py --system nmme --config confignmme.yaml --init 202601 --stages preprocess
```

Expected:
- command exits 0
- no preprocess invariant/normalization traceback

## 3. Runner Dry-Run: PyCPT

```bash
python runners/cli.py --system nmme --config confignmme.yaml --init 202601 --stages pycpt --pycpt-dry-run
```

Expected:
- command exits 0
- log path created under `logs/.../nmme/202601/`

## 4. Runner Dry-Run: Products

```bash
python runners/cli.py --system nmme --config confignmme.yaml --init 202601 --stages products --products-direct --products-dry-run
```

Expected:
- command exits 0
- no plotting import errors

## 5. Full Runner Workflow

```bash
python runners/cli.py --system nmme --config confignmme.yaml --init 202601
```

Expected:
- runner runs default stages: `ingest`, `preprocess`, `products`, `pycpt`
- no uncaught runner exception

## 6. Publish Stage Dry-Run (Optional)

```bash
python runners/cli.py --system nmme --config confignmme.yaml --init 202601 --stages publish --publish-dry-run
```

Expected:
- command exits 0
- publish log generated without SSH/SCP side effects

## 7. Log Review

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
