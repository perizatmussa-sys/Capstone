
from pathlib import Path
from collections import Counter
import re

import numpy as np
import rasterio
from pyproj import Transformer
from PIL import Image
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill


# ==========================================
# 1. SETTINGS
# ==========================================

PROJECT = Path(r"D:\Karlag_SI_ML")

DATABASE = PROJECT / "karlag_database.xlsx"

DATASET = PROJECT / "dataset" / "sites_S60"

TIF_DIR = DATASET / "geotiff"
PNG_DIR = DATASET / "previews"

REPORT = (
    DATASET / "metadata"
    / "S60_Validation_Report.xlsx"
)

EXPECTED_SITES = 60
EXPECTED_PIXELS = 1000
EXPECTED_RESOLUTION = 10.0
EXPECTED_SIZE_METERS = 10000.0

# Crops were aligned to the source pixel grid.
# Allow a small difference from the exact site center.
MAX_CENTER_ERROR_METERS = 15.0

# Quality warnings, not automatic failures.
BLACK_WARNING_PERCENT = 30.0
INVALID_WARNING_PERCENT = 0.1


# ==========================================
# 2. READ SITE DATABASE
# ==========================================

def load_sites():

    wb = load_workbook(
        DATABASE,
        read_only=True,
        data_only=True
    )

    try:
        ws = wb["sites"]
        rows = ws.iter_rows(values_only=True)

        headers = [
            str(value).strip()
            if value is not None else ""
            for value in next(rows)
        ]

        sites = {}

        for values in rows:

            row = dict(zip(headers, values))

            site_id = str(
                row.get("site_id") or ""
            ).strip().upper()

            if not site_id:
                continue

            if site_id in sites:
                raise ValueError(
                    f"Duplicate site ID: {site_id}"
                )

            sites[site_id] = {
                "name": str(row.get("name") or ""),
                "lon": float(row["lon"]),
                "lat": float(row["lat"])
            }

        return sites

    finally:
        wb.close()


# ==========================================
# 3. FIND IMAGE FILES
# ==========================================

def find_images(folder, extension):

    result = {}

    for path in folder.glob(
        f"*.{extension}"
    ):

        match = re.match(
            r"^(S\d{3})_",
            path.name,
            re.IGNORECASE
        )

        if not match:
            continue

        site_id = match.group(1).upper()

        result.setdefault(
            site_id, []
        ).append(path)

    return result


# ==========================================
# 4. VALIDATE ONE SITE
# ==========================================

def validate_site(
    site_id,
    site,
    tif_files,
    png_files
):

    errors = []
    warnings = []

    record = {
        "site_id": site_id,
        "site_name": site["name"],
        "tif_file": "",
        "png_file": "",
        "width": None,
        "height": None,
        "resolution_m": None,
        "crs": "",
        "center_error_m": None,
        "valid_percent": None,
        "black_percent": None,
        "png_matches_tif": "",
        "status": "",
        "errors": "",
        "warnings": ""
    }

    if len(tif_files) != 1:
        errors.append(
            f"Expected 1 GeoTIFF, found {len(tif_files)}"
        )

    if len(png_files) != 1:
        errors.append(
            f"Expected 1 PNG, found {len(png_files)}"
        )

    if errors:
        record["status"] = "FAIL"
        record["errors"] = "; ".join(errors)
        return record

    tif_path = tif_files[0]
    png_path = png_files[0]

    record["tif_file"] = tif_path.name
    record["png_file"] = png_path.name

    if tif_path.stem != png_path.stem:
        errors.append(
            "GeoTIFF and PNG filenames do not match"
        )

    try:

        with rasterio.open(tif_path) as src:

            record["width"] = src.width
            record["height"] = src.height
            record["crs"] = str(src.crs)

            if (
                src.width != EXPECTED_PIXELS
                or src.height != EXPECTED_PIXELS
            ):
                errors.append(
                    "Incorrect image dimensions"
                )

            if src.count != 3:
                errors.append(
                    f"Expected 3 bands, found {src.count}"
                )

            if src.crs is None:
                errors.append(
                    "GeoTIFF has no CRS"
                )

            if not src.transform.is_rectilinear:
                errors.append(
                    "Unexpected rotated/sheared transform"
                )

            res_x = abs(src.res[0])
            res_y = abs(src.res[1])

            record["resolution_m"] = round(
                res_x, 4
            )

            if (
                abs(res_x - EXPECTED_RESOLUTION) > 0.01
                or abs(res_y - EXPECTED_RESOLUTION) > 0.01
            ):
                errors.append(
                    "Resolution is not 10 m/pixel"
                )

            if src.crs is not None:

                if not src.crs.is_projected:
                    errors.append(
                        "Expected projected CRS in meters"
                    )

                transformer = Transformer.from_crs(
                    "EPSG:4326",
                    src.crs,
                    always_xy=True
                )

                expected_x, expected_y = (
                    transformer.transform(
                        site["lon"],
                        site["lat"]
                    )
                )

                actual_x, actual_y = (
                    src.transform * (
                        src.width / 2,
                        src.height / 2
                    )
                )

                center_error = (
                    (actual_x - expected_x) ** 2
                    + (actual_y - expected_y) ** 2
                ) ** 0.5

                record["center_error_m"] = round(
                    center_error, 3
                )

                if (
                    center_error
                    > MAX_CENTER_ERROR_METERS
                ):
                    errors.append(
                        "Image center does not match site"
                    )

            width_m = src.width * res_x
            height_m = src.height * res_y

            if (
                abs(width_m - EXPECTED_SIZE_METERS) > 1
                or abs(height_m - EXPECTED_SIZE_METERS) > 1
            ):
                errors.append(
                    "Image does not cover 10 x 10 km"
                )

            if src.count != 3:
                rgb = None
            else:
                rgb = src.read([1, 2, 3])

            mask = src.dataset_mask()

            valid = mask > 0

            valid_percent = (
                np.mean(valid) * 100
            )

            record["valid_percent"] = round(
                float(valid_percent), 3
            )

            if (
                100 - valid_percent
                > INVALID_WARNING_PERCENT
            ):
                warnings.append(
                    "Some pixels are invalid"
                )

            if rgb is not None:

                if rgb.dtype != np.uint8:
                    errors.append(
                        "RGB data is not uint8"
                    )

                black = np.all(
                    rgb == 0,
                    axis=0
                )

                black_percent = (
                    np.mean(black) * 100
                )

                record["black_percent"] = round(
                    float(black_percent), 3
                )

                if (
                    black_percent
                    > BLACK_WARNING_PERCENT
                ):
                    warnings.append(
                        "Large black area in image"
                    )

                if np.all(rgb == rgb[:, :1, :1]):
                    warnings.append(
                        "Image has constant RGB values"
                    )

        # Check PNG outside the rasterio context.
        with Image.open(png_path) as image:

            if image.size != (
                EXPECTED_PIXELS,
                EXPECTED_PIXELS
            ):
                errors.append(
                    "Incorrect PNG dimensions"
                )

            if image.mode != "RGB":
                errors.append(
                    f"PNG mode is {image.mode}, not RGB"
                )

            if rgb is not None:

                png_rgb = np.asarray(
                    image.convert("RGB")
                )

                tif_rgb = np.moveaxis(
                    rgb,
                    0,
                    -1
                )

                matches = (
                    png_rgb.shape == tif_rgb.shape
                    and np.array_equal(
                        png_rgb,
                        tif_rgb
                    )
                )

                record["png_matches_tif"] = (
                    "YES" if matches else "NO"
                )

                if not matches:
                    errors.append(
                        "PNG pixels differ from GeoTIFF"
                    )

    except Exception as error:
        errors.append(
            f"Cannot read image: {error}"
        )

    if errors:
        record["status"] = "FAIL"
    elif warnings:
        record["status"] = "WARNING"
    else:
        record["status"] = "PASS"

    record["errors"] = "; ".join(errors)
    record["warnings"] = "; ".join(warnings)

    return record


# ==========================================
# 5. SAVE EXCEL REPORT
# ==========================================

def save_report(records):

    REPORT.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    wb = Workbook()

    ws = wb.active
    ws.title = "validation"

    headers = [
        "site_id",
        "site_name",
        "tif_file",
        "png_file",
        "width",
        "height",
        "resolution_m",
        "crs",
        "center_error_m",
        "valid_percent",
        "black_percent",
        "png_matches_tif",
        "status",
        "errors",
        "warnings"
    ]

    ws.append(headers)

    colors = {
        "PASS": "E2F0D9",
        "WARNING": "FFF2CC",
        "FAIL": "FCE4D6"
    }

    for record in records:

        ws.append([
            record.get(key)
            for key in headers
        ])

        row_number = ws.max_row

        status_cell = ws.cell(
            row=row_number,
            column=headers.index("status") + 1
        )

        status_cell.fill = PatternFill(
            "solid",
            fgColor=colors[
                record["status"]
            ]
        )

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    widths = {
        "A": 12,
        "B": 35,
        "C": 38,
        "D": 38,
        "E": 12,
        "F": 12,
        "G": 16,
        "H": 18,
        "I": 18,
        "J": 18,
        "K": 18,
        "L": 20,
        "M": 15,
        "N": 45,
        "O": 45
    }

    for letter, width in widths.items():
        ws.column_dimensions[
            letter
        ].width = width

    for cell in ws[1]:
        cell.font = Font(
            bold=True,
            color="FFFFFF"
        )
        cell.fill = PatternFill(
            "solid",
            fgColor="17365D"
        )

    summary = wb.create_sheet("summary")

    counts = Counter(
        record["status"]
        for record in records
    )

    summary.append(["Metric", "Value"])
    summary.append([
        "Total sites", len(records)
    ])
    summary.append([
        "PASS", counts["PASS"]
    ])
    summary.append([
        "WARNING", counts["WARNING"]
    ])
    summary.append([
        "FAIL", counts["FAIL"]
    ])

    summary.column_dimensions["A"].width = 24
    summary.column_dimensions["B"].width = 18

    wb.save(REPORT)

    return counts


# ==========================================
# 6. MAIN
# ==========================================

def main():

    print("=" * 55)
    print("KARLAG S60 - DATASET VALIDATION")
    print("=" * 55)

    sites = load_sites()

    tif_index = find_images(
        TIF_DIR, "tif"
    )

    png_index = find_images(
        PNG_DIR, "png"
    )

    print("\nSites in database:", len(sites))
    print(
        "GeoTIFF files:",
        sum(map(len, tif_index.values()))
    )
    print(
        "PNG files:",
        sum(map(len, png_index.values()))
    )

    records = []

    for site_id in sorted(sites):

        record = validate_site(
            site_id,
            sites[site_id],
            tif_index.get(site_id, []),
            png_index.get(site_id, [])
        )

        records.append(record)

        print(
            site_id,
            "->",
            record["status"]
        )

        if record["errors"]:
            print(
                "  ERROR:",
                record["errors"]
            )

        if record["warnings"]:
            print(
                "  WARNING:",
                record["warnings"]
            )

    counts = save_report(records)

    print("\n" + "=" * 55)
    print("VALIDATION COMPLETE")
    print("=" * 55)

    print("Total sites:", len(records))
    print("PASS:", counts["PASS"])
    print("WARNING:", counts["WARNING"])
    print("FAIL:", counts["FAIL"])

    if len(sites) != EXPECTED_SITES:
        print(
            "WARNING: Expected 60 sites"
        )

    print("\nExcel report:")
    print(REPORT)


if __name__ == "__main__":
    main()
