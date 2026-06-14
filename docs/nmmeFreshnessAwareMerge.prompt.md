## Plan: Freshness-Aware NMME Directory Merge

Create a third staging directory and perform a two-source, newest-wins union merge so neither source is treated as globally authoritative. This fits your constraint that nmme-backup is often newer, while initialized/nmme is newer for some files. Keep both sources untouched, generate collision/audit reports, and only promote after validation.

**Steps**
1. Pre-merge inventory and freeze window: capture recursive file lists, sizes, mtimes, and checksums for both source trees; pause upstream writers during merge window to prevent moving-target races.
2. Create rollback assets before merge: create timestamped manifests for both sources and a full destination staging snapshot plan (or CoW snapshot if filesystem supports it) so rollback is instant.
3. Build canonical path mapping: map model-centric source structure from /data/esplab/nmme-backup (model/forecast|hindcast|reforecast/variable/files) to category-centric target layout used by /data/esplab/shared/model/initialized/nmme (forecast/monthly|seasonal..., hindcast/monthly..., skill, terciles, climatology).
4. Initialize third directory: create /data/esplab/shared/model/initialized/nmme_merged_YYYYMMDD as empty target; copy metadata policy (ownership/group/perms) from initialized/nmme.
5. First merge pass (initialized -> merged): sync all files into merged without deletion; preserve timestamps/permissions; log all transfers and skipped files.
6. Second merge pass (backup -> merged, newest-wins): sync with update semantics so backup files replace only older files in merged; never delete. This captures newer files from backup while preserving newer files already copied from initialized.
7. Conflict audit pass: produce reports for same-path files with equal mtimes but different size/checksum, and filename-pattern mismatches (e.g., YYYY_MM vs YYYYMM, model aliases such as GEM5.2-NEMO vs GEM5-NEMO). Route these to a manual review list.
8. Scope completion pass: explicitly verify inclusion of forecast, hindcast/reforecast, skill, terciles, and climatology content; confirm model coverage aligns with current initialized products and legacy variants.
9. Validate merged tree: run count/size/hash deltas per major subtree; sample-open representative NetCDF files from each category/model/variable; ensure no truncated files and expected monthly coverage.
10. Promotion plan with rollback: atomically switch a symlink or directory pointer from initialized/nmme to nmme_merged_YYYYMMDD only after validation; retain prior initialized directory untouched as immediate rollback target.
11. Post-promotion monitor: run a short validation job that reads key downstream products (forecast monthly data/anomaly products, hindcast monthly full sets, tercile/skill outputs), then archive logs and manifests.

**Before to After Mapping Table**
- Destination root for all merged content: /data/esplab/shared/model/initialized/nmme_merged_YYYYMMDD
- Rule: initialize destination by copying full initialized tree first, then overlay backup with newest-wins update behavior.

1. Existing initialized content
- Source: /data/esplab/shared/model/initialized/nmme/*
- Destination: /data/esplab/shared/model/initialized/nmme_merged_YYYYMMDD/*
- Notes: This preserves current product-centric structure as the base layout.

2. Forecast monthly model inputs from backup
- Source pattern: /data/esplab/nmme-backup/<MODEL>/forecast/<VAR>/<var>_<MODEL>_<YYYY>_<MM>.nc
- Destination pattern: /data/esplab/shared/model/initialized/nmme_merged_YYYYMMDD/forecast/monthly/<YYYYMM>/preprocess/<MODEL>/forecast/<VAR>/<var>_<MODEL>_<YYYY>_<MM>.nc
- Notes: files are grouped by cycle month in destination; for collisions, newer mtime wins.

3. Hindcast and reforecast model inputs from backup
- Source pattern A: /data/esplab/nmme-backup/<MODEL>/hindcast/<VAR>/<var>_<MODEL>_<YYYY>_<MM>.nc
- Source pattern B: /data/esplab/nmme-backup/<MODEL>/reforecast/<VAR>/<var>_<MODEL>_<YYYY>_<MM>.nc
- Destination pattern: /data/esplab/shared/model/initialized/nmme_merged_YYYYMMDD/hindcast/monthly/<VAR>/monthly/full/<MODEL_ALIAS>/<var>_<MODEL_ALIAS>_<YYYYMM>.nc
- Notes: apply model alias mapping and filename normalization where destination uses YYYYMM instead of YYYY_MM.

4. Skill products
- Source preferred base: /data/esplab/shared/model/initialized/nmme/skill/1991-2020/*
- Optional backup overlay: /data/esplab/nmme-backup/derived/skill/1991-2020/* (if present)
- Destination: /data/esplab/shared/model/initialized/nmme_merged_YYYYMMDD/skill/1991-2020/*
- Notes: preserve plots subfolder and apply newest-wins for same relative file paths.

5. Terciles products
- Source preferred base: /data/esplab/shared/model/initialized/nmme/terciles/1991-2020/*
- Optional backup overlay: /data/esplab/nmme-backup/derived/terciles/1991-2020/* (if present)
- Destination: /data/esplab/shared/model/initialized/nmme_merged_YYYYMMDD/terciles/1991-2020/*
- Notes: if only initialized has these products, they remain unchanged in destination.

6. Climatology products
- Source preferred base: /data/esplab/shared/model/initialized/nmme/climatology/monthly/1991-2020/*
- Optional backup overlay: /data/esplab/nmme-backup/derived/climatology/monthly/1991-2020/* (if present)
- Destination: /data/esplab/shared/model/initialized/nmme_merged_YYYYMMDD/climatology/monthly/1991-2020/*
- Notes: same-path collisions resolved by newest mtime.

7. Forecast aggregate outputs
- Source base: /data/esplab/shared/model/initialized/nmme/forecast/monthly/<YYYYMM>/data/* and /images/*
- Destination: /data/esplab/shared/model/initialized/nmme_merged_YYYYMMDD/forecast/monthly/<YYYYMM>/data/* and /images/*
- Notes: these are kept from initialized as authoritative generated outputs unless backup contains explicitly newer same-path files.

8. Model alias and variable normalization table used during mapping
- GEM5.2-NEMO -> GEM5-NEMO and GEM-NEMO (resolve by existing destination path and file naming convention)
- NOAA-SFS (backup reforecast) -> NOAA-SFS destination model folder
- CanESM5 remains CanESM5
- NCEP-CFSv2 remains NCEP-CFSv2
- Variable aliases where needed: prec_sfc -> prec, sst_sfc -> sst, tref_2m -> tref

9. Manual review queue
- Any same-path file with equal mtime but different checksum.
- Any model path that maps to more than one alias target.
- Any backup file that does not fit forecast, hindcast, reforecast, skill, terciles, or climatology mappings.

**Command Runbook (Exact Commands)**

Run from any shell with permissions on both source trees.

```bash
#!/usr/bin/env bash
set -euo pipefail

# --- Paths ---
SRC_INIT="/data/esplab/shared/model/initialized/nmme"
SRC_BAK="/data/esplab/nmme-backup"
TS="$(date +%Y%m%d_%H%M%S)"
MERGED="/data/esplab/shared/model/initialized/nmme_merged_${TS}"
LOGROOT="/data/esplab/shared/model/initialized/nmme_merge_logs_${TS}"
PREV_LINK="/data/esplab/shared/model/initialized/nmme_previous"
ACTIVE_LINK="/data/esplab/shared/model/initialized/nmme_active"

mkdir -p "$MERGED" "$LOGROOT"

echo "[1/10] Pre-merge inventories"
find "$SRC_INIT" -type f -printf "%P|%s|%T@\n" | sort > "$LOGROOT/init_files_mtime_size.txt"
find "$SRC_BAK" -type f -printf "%P|%s|%T@\n" | sort > "$LOGROOT/backup_files_mtime_size.txt"
du -sh "$SRC_INIT" "$SRC_BAK" > "$LOGROOT/source_sizes.txt"

echo "[2/10] Baseline copy DRY RUN (initialized -> merged)"
rsync -aHAXvn --info=stats2,progress2 \
	"$SRC_INIT/" "$MERGED/" | tee "$LOGROOT/rsync_init_to_merged.dryrun.log"

echo "[3/10] Baseline copy EXECUTE (initialized -> merged)"
rsync -aHAXv --info=stats2,progress2 \
	"$SRC_INIT/" "$MERGED/" | tee "$LOGROOT/rsync_init_to_merged.run.log"

echo "[4/10] Overlay DRY RUN (backup -> merged, newer-only)"
rsync -aHAXvun --info=stats2,progress2 \
	"$SRC_BAK/" "$MERGED/" | tee "$LOGROOT/rsync_backup_overlay.dryrun.log"

echo "[5/10] Overlay EXECUTE (backup -> merged, newer-only)"
rsync -aHAXvu --info=stats2,progress2 \
	"$SRC_BAK/" "$MERGED/" | tee "$LOGROOT/rsync_backup_overlay.run.log"

echo "[6/10] Post-merge inventories"
find "$MERGED" -type f -printf "%P|%s|%T@\n" | sort > "$LOGROOT/merged_files_mtime_size.txt"
du -sh "$MERGED" > "$LOGROOT/merged_size.txt"

echo "[7/10] Collision candidates by relative path"
cut -d'|' -f1 "$LOGROOT/init_files_mtime_size.txt" | sort > "$LOGROOT/init_relpaths.txt"
cut -d'|' -f1 "$LOGROOT/backup_files_mtime_size.txt" | sort > "$LOGROOT/backup_relpaths.txt"
comm -12 "$LOGROOT/init_relpaths.txt" "$LOGROOT/backup_relpaths.txt" > "$LOGROOT/common_relpaths.txt"

echo "[8/10] Equal-mtime-different-size report"
awk -F'|' 'NR==FNR{a[$1]=$2"|"$3;next} ($1 in a){print $1"|"a[$1]"|"$2"|"$3}' \
	"$LOGROOT/init_files_mtime_size.txt" "$LOGROOT/backup_files_mtime_size.txt" \
	> "$LOGROOT/common_path_compare.txt"
awk -F'|' '($3==$5 && $2!=$4){print}' "$LOGROOT/common_path_compare.txt" \
	> "$LOGROOT/equal_mtime_diff_size.txt"

echo "[9/10] Optional checksum audit for common paths (can be slow)"
: > "$LOGROOT/equal_mtime_diff_checksum.txt"
while IFS= read -r rel; do
	f1="$SRC_INIT/$rel"
	f2="$SRC_BAK/$rel"
	[[ -f "$f1" && -f "$f2" ]] || continue
	h1="$(sha256sum "$f1" | awk '{print $1}')"
	h2="$(sha256sum "$f2" | awk '{print $1}')"
	if [[ "$h1" != "$h2" ]]; then
		printf "%s|%s|%s\n" "$rel" "$h1" "$h2" >> "$LOGROOT/equal_mtime_diff_checksum.txt"
	fi
done < "$LOGROOT/common_relpaths.txt"

echo "[10/10] Promotion and rollback commands"
cat > "$LOGROOT/promotion_rollback_commands.txt" <<EOF
# Promotion option A: update active symlink atomically
ln -sfn "$MERGED" "$ACTIVE_LINK"

# Promotion option B: if you replace nmme directly, keep pointer to previous
ln -sfn "$SRC_INIT" "$PREV_LINK"

# Rollback using active link
ln -sfn "$SRC_INIT" "$ACTIVE_LINK"
EOF

echo "Done. Logs: $LOGROOT"
echo "Merged tree: $MERGED"
```

Notes for command behavior:
- `rsync -u` enforces newer-only updates for same-path files.
- No `--delete` is used, so no source-absent files are removed from merged.
- The runbook merges full trees first; use the mapping table to manually inspect any category/model alias edge cases before promotion.

**Relevant files**
- /data/esplab/nmme-backup — Source A, often newer for many files.
- /data/esplab/shared/model/initialized/nmme — Source B, newer for some files and current operational layout.
- /data/esplab/shared/model/initialized/nmme_merged_YYYYMMDD — New staging/merge output.
- /data/esplab/shared/model/initialized/nmme/forecast/monthly/202605/preprocess — Reference for model-preprocess structure.
- /data/esplab/shared/model/initialized/nmme/hindcast/monthly — Reference for monthly full hindcast organization.
- /data/esplab/shared/model/initialized/nmme/skill/1991-2020 — Product area that must be retained in full-scope merge.
- /data/esplab/shared/model/initialized/nmme/terciles/1991-2020 — Product area that must be retained in full-scope merge.

**Verification**
1. Generate per-tree inventories (path, size, mtime, checksum) before and after merge; confirm merged contains union of both sources.
2. Run dry-run syncs first and review logs for unexpected large overwrite counts.
3. Validate newest-wins behavior on sampled collision files where backup is newer and where initialized is newer.
4. Confirm no source deletions and no destination deletions occurred.
5. Run representative downstream read tests for forecast, hindcast, skill, and tercile files.
6. Verify rollback by test-switching pointer back to pre-merge initialized path.

**Decisions**
- Merge strategy: create third merged directory (no in-place merge).
- Overwrite policy: safest default retained (no blind overwrite); overwrite only when source is strictly newer during freshness-aware pass.
- Scope: include all categories (forecast, hindcast/reforecast, skill, terciles, climatology).
- Included: data union, freshness resolution by mtime, conflict reporting, promotion/rollback process.
- Excluded: reprocessing/regeneration of derived products unless validation detects corruption or structural incompatibility.

**Further Considerations**
1. Timestamp tie-breaker rule recommendation: if same path has identical mtime but different checksums, quarantine both copies into a review folder and do not auto-resolve.
2. Model alias normalization recommendation: maintain an explicit alias table for known naming variants before promotion.
3. If filesystem supports snapshots (ZFS/Btrfs/LVM), prefer snapshot rollback over duplicate copy backups for speed and space efficiency.
