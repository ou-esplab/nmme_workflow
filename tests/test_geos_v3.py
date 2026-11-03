import unittest

import numpy as np
import xarray as xr

from utils.geos_v3 import (
    extract_field,
    full_length_members,
    parse_member_names,
    parse_rt_listing,
    regrid_native_to_1deg,
    s_month_of_init,
    rt_month_dir,
    s_value,
    to_nmme_dataset,
)
from utils.nmme_io import decode_S_cftime


def _listing(entries):
    """Fake nginx listing: entries of (init, ens, maxlead) for token 'precip'."""
    rows = []
    for init, ens, maxlead in entries:
        for lead in range(1, maxlead + 1):
            name = f"{init}.precip.ens{ens}.lead{lead}.nc4"
            rows.append(f'<a href="{name}">{name}</a>')
        # a different variable in the same folder must be ignored
        rows.append(f'<a href="{init}.at.ens{ens}.lead1.nc4">x</a>')
    return "\n".join(rows)


class GeosV3ListingTests(unittest.TestCase):
    def test_rt_month_dir(self):
        self.assertEqual(rt_month_dir("202506"), "jun2025")
        self.assertEqual(rt_month_dir("202612"), "dec2026")

    def test_full_length_members_picks_only_9_lead_runs(self):
        # Mirrors the jun2025 pattern: mostly 2-lead members, a few 9-lead ones.
        html = _listing([
            ("20250501", 1, 2), ("20250506", 2, 9), ("20250511", 4, 9),
            ("20250511", 1, 2), ("20250531", 13, 9), ("20250531", 5, 9),
        ])
        members = parse_rt_listing(html, "precip")
        self.assertEqual(len(members), 6)
        self.assertEqual(
            full_length_members(members),
            [("20250506", 2), ("20250511", 4), ("20250531", 5), ("20250531", 13)],
        )
        self.assertEqual(members[("20250506", 2)][9], "20250506.precip.ens2.lead9.nc4")


class GeosV3RegridTests(unittest.TestCase):
    def test_native_subsample_handles_lon_seam_noise(self):
        lat = np.arange(-90, 90.25, 0.5)
        lon = np.arange(-180, 180, 0.5)
        lon[360] = -1.3e-12  # the float noise at lon~0 seen in GMAO files
        vals = np.broadcast_to(np.mod(np.arange(-180, 180, 0.5), 360.0), (lat.size, lon.size))
        ds = xr.Dataset({"T2M": (("lat", "lon"), vals)}, coords={"lat": lat, "lon": lon})
        out = regrid_native_to_1deg(ds)
        self.assertEqual(out.sizes, {"lat": 181, "lon": 360})
        np.testing.assert_allclose(out["T2M"].isel(lat=0).values, np.arange(360))

    def test_non_native_grid_raises(self):
        lat = np.arange(-89.5, 90, 1.0)  # a 1-deg cell-centred grid is not what GMAO posts
        lon = np.arange(0, 360, 1.0)
        ds = xr.Dataset({"T2M": (("lat", "lon"), np.zeros((lat.size, lon.size)))},
                        coords={"lat": lat, "lon": lon})
        with self.assertRaises(RuntimeError):
            regrid_native_to_1deg(ds)


class GeosV3FieldTests(unittest.TestCase):
    def _height(self, lev):
        return xr.Dataset(
            {"H": (("time", "lev", "lat", "lon"), np.ones((1, 1, 2, 2)))},
            coords={"lev": [lev]},
        )

    def test_height_level_guard(self):
        self.assertEqual(extract_field(self._height(500.0), "h500").dims, ("time", "lat", "lon"))
        self.assertEqual(extract_field(self._height(200.0), "h200").dims, ("time", "lat", "lon"))
        with self.assertRaises(ValueError):
            extract_field(self._height(200.0), "h500")  # GMAO's old mislabeled NRT "z500"
        with self.assertRaises(ValueError):
            extract_field(self._height(150.0), "h200")  # GMAO's old mislabeled hindcast "h200"

    def test_prec_units(self):
        ds = xr.Dataset({"PRECTOTCORR": (("lat", "lon"), np.full((2, 2), 1.0 / 86400.0))})
        np.testing.assert_allclose(extract_field(ds, "prec").values, 1.0, rtol=1e-6)


class GeosV3OutputTests(unittest.TestCase):
    def test_s_encoding_matches_iridl_and_decodes(self):
        self.assertEqual(s_value("202609"), 800.0)  # same S as IRIDL's Sep 2026 files
        ds = to_nmme_dataset(np.zeros((9, 10, 181, 360), np.float32), "prec", "202506")
        self.assertEqual(ds["prec"].dims, ("S", "L", "M", "Y", "X"))
        np.testing.assert_allclose(ds["L"].values, np.arange(9) + 0.5)
        s = decode_S_cftime(ds)["S"].values[0]
        self.assertEqual((s.year, s.month, s.day), (2025, 6, 1))


class GeosV3HindcastTarTests(unittest.TestCase):
    def test_parse_member_names_uses_basename_and_token(self):
        names = ["./19911202.sst.ens2.lead9.nc4", "19911202.sst.ens2.lead1.nc4",
                 "19911202.at.ens2.lead1.nc4", "readme.txt"]
        got = parse_member_names(names, "sst")
        self.assertEqual(list(got), [("19911202", 2)])
        self.assertEqual(sorted(got[("19911202", 2)]), [1, 9])
        self.assertEqual(full_length_members(got), [("19911202", 2)])

    def test_s_month_is_month_after_init(self):
        self.assertEqual(s_month_of_init("19911227"), "199201")
        self.assertEqual(s_month_of_init("19900702"), "199008")


class GeosV3ProcessTarTests(unittest.TestCase):
    def test_native_tar_to_member_file(self):
        import sys, tarfile, tempfile
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "static"))
        from download_geos_v3_nmme_hindcast import process_tar

        lat = np.arange(-90, 90.25, 0.5)
        lon = np.arange(-180, 180, 0.5)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src = tmp / "src"
            src.mkdir()
            names = []
            # 10 full-length members (2 inits x 5 ens) plus one short 2-lead member
            members = [("19911202", e, 9) for e in range(1, 6)] + [("19911227", e, 9) for e in range(1, 6)]
            members.append(("19911202", 9, 2))
            for init, ens, nl in members:
                for lead in range(1, nl + 1):
                    name = f"{init}.sst.ens{ens}.lead{lead}.nc4"
                    vals = np.full((1, lat.size, lon.size), 280.0 + ens + lead)
                    xr.Dataset({"TS": (("time", "lat", "lon"), vals)},
                               coords={"time": [0], "lat": lat, "lon": lon}).to_netcdf(src / name)
                    names.append(name)
            tar_path = tmp / "t.tar"
            with tarfile.open(tar_path, "w") as tf:
                for n in names:
                    tf.add(src / n, arcname=n)
            root = tmp / "root"
            ok, err = process_tar(tar_path, "sst", "sst", 1, range(1991, 2021), root, overwrite=False)
            self.assertEqual((ok, err), (1, 0))
            out = xr.open_dataset(root / "NASA-GEOSS2S3" / "hindcast" / "sst" / "sst_NASA-GEOSS2S3_1992_01.nc",
                                  decode_times=False)
            self.assertEqual(out["sst"].dims, ("S", "L", "M", "Y", "X"))
            self.assertEqual(out["sst"].shape, (1, 9, 10, 181, 360))
            self.assertAlmostEqual(float(out["sst"].isel(S=0, L=0, M=0, Y=5, X=5)), 282.0)  # ens1, lead1


if __name__ == "__main__":
    unittest.main()
