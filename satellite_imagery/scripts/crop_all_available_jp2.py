
from pathlib import Path
from collections import defaultdict
from datetime import datetime
import re
import shutil

import numpy as np
import rasterio
from rasterio.windows import Window
from rasterio.windows import from_bounds
from rasterio.windows import transform as window_transform
from pyproj import Transformer
from PIL import Image
from openpyxl import load_workbook, Workbook
from openpyxl.styles import Font, PatternFill


# ==========================================
# 1. PATHS AND SETTINGS
# ==========================================

PROJECT = Path(r"D:\Karlag_SI_ML")

DATABASE = PROJECT / "karlag_database.xlsx"

PLAN = (
    PROJECT / "dataset"
    / "sites_S60" / "metadata"
    / "S60_Verified_Download_Plan.xlsx"
)

SOURCE_DIR = (
    PROJECT / "satellite_sources"
    / "single_jp2"
)

DATASET_DIR = (
    PROJECT / "dataset" / "sites_S60"
)

TIF_DIR = DATASET_DIR / "geotiff"
PNG_DIR = DATASET_DIR / "previews"

REPORT = (
    DATASET_DIR / "metadata"
    / "S60_JP2_Crop_Report.xlsx"
)

CROP_METERS = 10000
PIXELS = 1000
MIN_VALID_PERCENT = 99.9

# Stop before the disk becomes completely full.
MIN_FREE_MB = 200

# Never overwrite an existing image.
OVERWRITE = False


# ==========================================
# 2. READ EXCEL
# ==========================================

def read_sheet(path, sheet_name):

    if not path.exists():
        raise FileNotFoundError(
            f"Excel not found: {path}"
        )

    wb = load_workbook(
        path,
        read_only=True,
        data_only=True
    )

    try:
        ws = wb[sheet_name]
        iterator = ws.iter_rows(
            values_only=True
        )

        headers = [
            str(value).strip()
            if value is not None else ""
            for value in next(iterator)
        ]

        result = []

        for values in iterator:
            if not any(
                value is not None
                for value in values
            ):
                continue

            result.append(
                dict(zip(headers, values))
            )

        return result

    finally:
        wb.close()


# ==========================================
# 3. LOAD ALL 60 SITES
# ==========================================

def load_sites():

    rows = read_sheet(
        DATABASE,
        "sites"
    )

    sites = {}

    for row in rows:

        site_id = str(
            row.get("site_id") or ""
        ).strip()

        if not site_id:
            continue

        if site_id in sites:
            raise ValueError(
                f"Duplicate site ID: {site_id}"
            )

        sites[site_id] = {
            "site_id": site_id,
            "name": str(
                row.get("name") or ""
            ),
            "lon": float(row["lon"]),
            "lat": float(row["lat"])
        }

    return sites


# ==========================================
# 4. LOAD VERIFIED PRODUCT ASSIGNMENTS
# ==========================================

def load_assignments():

    rows = read_sheet(
        PLAN,
        "selected_products"
    )

    assignments = {}

    for row in rows:

        product_name = str(
            row["product_name"]
        ).strip()

        tile_id = str(
            row["tile_id"]
        ).strip()

        date = str(
            row["acquisition_date"]
        )[:10]

        site_ids = str(
            row.get("site_ids") or ""
        ).split(",")

        for item in site_ids:

            site_id = item.strip()

            if not site_id:
                continue

            if site_id in assignments:
                raise ValueError(
                    "Duplicate assignment: "
                    + site_id
                )

            assignments[site_id] = {
                "product_name": product_name,
                "tile_id": tile_id,
                "date": date
            }

    return assignments


# ==========================================
# 5. FIND LOCAL JP2 FILES
# ==========================================

def find_jp2_files():

    if not SOURCE_DIR.exists():
        raise FileNotFoundError(
            f"Source folder not found: "
            f"{SOURCE_DIR}"
        )

    files = sorted(
        path for path in SOURCE_DIR.rglob("*")
        if (
            path.is_file()
            and path.suffix.lower() == ".jp2"
            and path.name.upper().endswith(
                "_TCI_10M.JP2"
            )
        )
    )

    return files


def identify_jp2(path):

    # Example:
    # T43TDM_20261029T055941_TCI_10m.jp2

    match = re.fullmatch(
        r"(T\d{2}[A-Z]{3})_"
        r"(\d{8}T\d{6})_TCI_10m\.jp2",
        path.name,
        flags=re.IGNORECASE
    )

    if not match:
        return None

    tile_id = match.group(1).upper()
    timestamp = match.group(2)

    date = datetime.strptime(
        timestamp[:8],
        "%Y%m%d"
    ).strftime("%Y-%m-%d")

    return {
        "tile_id": tile_id,
        "date": date,
        "timestamp": timestamp
    }


# ==========================================
# 6. FIND EXISTING IMAGES
# ==========================================

def existing_site_ids():

    existing = set()

    # Check only complete TIFF + PNG pairs.
    for tif_path in TIF_DIR.glob("*.tif"):

        match = re.match(
            r"^(S\d{3})_",
            tif_path.name,
            flags=re.IGNORECASE
        )

        if not match:
            continue

        site_id = match.group(1).upper()

        # Look for the matching PNG by stem.
        png_path = (
            PNG_DIR / (tif_path.stem + ".png")
        )

        if png_path.exists():
            existing.add(site_id)

    return existing


# ==========================================
# 7. DISK SPACE
# ==========================================

def free_space_mb():

    return (
        shutil.disk_usage(
            DATASET_DIR
        ).free
        / (1024 ** 2)
    )


# ==========================================
# 8. CREATE A 10 KM CROP
# ==========================================

def crop_site(
    src,
    site,
    acquisition_date
):

    site_id = site["site_id"]

    lon = site["lon"]
    lat = site["lat"]

    transformer = Transformer.from_crs(
        "EPSG:4326",
        src.crs,
        always_xy=True
    )

    x, y = transformer.transform(
        lon, lat
    )

    half = CROP_METERS / 2

    left = x - half
    right = x + half
    bottom = y - half
    top = y + half

    bounds = src.bounds

    if not (
        left >= bounds.left
        and right <= bounds.right
        and bottom >= bounds.bottom
        and top <= bounds.top
    ):
        return (
            "outside_raster",
            None,
            "10 km square exceeds raster bounds"
        )

    raw_window = from_bounds(
        left,
        bottom,
        right,
        top,
        transform=src.transform
    )

    col = round(raw_window.col_off)
    row = round(raw_window.row_off)

    window = Window(
        col_off=col,
        row_off=row,
        width=PIXELS,
        height=PIXELS
    )

    if (
        col < 0
        or row < 0
        or col + PIXELS > src.width
        or row + PIXELS > src.height
    ):
        return (
            "outside_raster",
            None,
            "Pixel window exceeds raster bounds"
        )

    if free_space_mb() < MIN_FREE_MB:
        return (
            "low_disk_space",
            None,
            "Insufficient free disk space"
        )

    rgb = src.read(
        indexes=[1, 2, 3],
        window=window
    )

    if rgb.shape != (
        3, PIXELS, PIXELS
    ):
        return (
            "invalid_shape",
            None,
            f"Unexpected RGB shape: {rgb.shape}"
        )

    mask = src.dataset_mask(
        window=window
    )

    valid_percent = float(
        np.mean(mask > 0) * 100
    )

    if valid_percent < MIN_VALID_PERCENT:
        return (
            "invalid_pixels",
            valid_percent,
            "Too many invalid pixels"
        )

    if not np.any(rgb):
        return (
            "empty_image",
            valid_percent,
            "RGB data is empty"
        )

    if rgb.dtype != np.uint8:
        return (
            "unsupported_dtype",
            valid_percent,
            f"Expected uint8, got {rgb.dtype}"
        )

    transform = window_transform(
        window,
        src.transform
    )

    date_text = acquisition_date.replace(
        "-", ""
    )

    image_name = (
        f"{site_id}_{date_text}_RGB_10km"
    )

    tif_path = (
        TIF_DIR / f"{image_name}.tif"
    )

    png_path = (
        PNG_DIR / f"{image_name}.png"
    )

    if not OVERWRITE and (
        tif_path.exists()
        or png_path.exists()
    ):
        return (
            "already_exists",
            valid_percent,
            "Output filename already exists"
        )

    temp_tif = Path(
        str(tif_path) + ".part"
    )

    temp_png = Path(
        str(png_path) + ".part"
    )

    try:

        with rasterio.open(
            temp_tif,
            "w",
            driver="GTiff",
            width=PIXELS,
            height=PIXELS,
            count=3,
            dtype="uint8",
            crs=src.crs,
            transform=transform,
            compress="deflate"
        ) as dst:

            dst.write(rgb)

            dst.write_mask(
                mask
            )

        preview = np.moveaxis(
            rgb,
            0,
            -1
        )

        Image.fromarray(
            preview
        ).save(
            temp_png,
            format="PNG"
        )

        # Check outputs before publishing them.
        with rasterio.open(
            temp_tif
        ) as check:

            if (
                check.width != PIXELS
                or check.height != PIXELS
                or check.count != 3
                or check.crs != src.crs
            ):
                raise ValueError(
                    "GeoTIFF validation failed"
                )

        # Keep existing output files safe.
        if (
            not OVERWRITE
            and (
                tif_path.exists()
                or png_path.exists()
            )
        ):
            return (
                "already_exists",
                valid_percent,
                "Output appeared during processing"
            )

        temp_tif.replace(tif_path)

        try:
            temp_png.replace(png_path)

        except Exception:
            tif_path.unlink(
                missing_ok=True
            )
            raise

        return (
            "created",
            valid_percent,
            str(tif_path)
        )

    finally:

        temp_tif.unlink(
            missing_ok=True
        )

        temp_png.unlink(
            missing_ok=True
        )


# ==========================================
# 9. CREATE EXCEL REPORT
# ==========================================

def save_report(records, summary):

    REPORT.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    wb = Workbook()

    ws = wb.active
    ws.title = "site_results"

    headers = [
        "site_id",
        "site_name",
        "assigned_tile",
        "assigned_date",
        "local_jp2",
        "status",
        "valid_percent",
        "details"
    ]

    ws.append(headers)

    for record in records:
        ws.append([
            record.get(header)
            for header in headers
        ])

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    ws2 = wb.create_sheet(
        "summary"
    )

    ws2.append([
        "metric",
        "value"
    ])

    for key, value in summary.items():
        ws2.append([key, value])

    for sheet in (ws, ws2):

        for cell in sheet[1]:

            cell.font = Font(
                bold=True,
                color="FFFFFF"
            )

            cell.fill = PatternFill(
                "solid",
                fgColor="17365D"
            )

        for column in sheet.columns:

            letter = (
                column[0].column_letter
            )

            sheet.column_dimensions[
                letter
            ].width = min(
                65,
                max(
                    16,
                    max(
                        len(str(c.value or ""))
                        for c in column
                    ) + 2
                )
            )

    wb.save(REPORT)


# ==========================================
# 10. MAIN
# ==========================================

def main():

    print("=" * 50)
    print("KARLAG S60 - AUTOMATIC JP2 CROPPING")
    print("=" * 50)

    TIF_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    PNG_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    print("\nSTEP 1: Reading database...")

    sites = load_sites()
    assignments = load_assignments()

    print("Sites in database:", len(sites))
    print(
        "Sites in verified plan:",
        len(assignments)
    )

    unknown = (
        set(assignments) - set(sites)
    )

    if unknown:
        raise ValueError(
            "Unknown sites in plan: "
            + ", ".join(sorted(unknown))
        )

    print("\nSTEP 2: Finding JP2 files...")

    jp2_files = find_jp2_files()

    print(
        "TCI JP2 files found:",
        len(jp2_files)
    )

    for path in jp2_files:
        print(" -", path.name)

    existing_before = existing_site_ids()

    print(
        "\nComplete image pairs before:",
        len(existing_before)
    )

    records_by_site = {}

    for site_id, site in sites.items():

        assignment = assignments.get(
            site_id
        )

        records_by_site[site_id] = {
            "site_id": site_id,
            "site_name": site["name"],
            "assigned_tile": (
                assignment["tile_id"]
                if assignment else ""
            ),
            "assigned_date": (
                assignment["date"]
                if assignment else ""
            ),
            "local_jp2": "",
            "status": (
                "already_complete"
                if site_id in existing_before
                else "waiting_for_jp2"
            ),
            "valid_percent": None,
            "details": ""
        }

    print("\nSTEP 3: Processing local JP2...")

    for jp2_path in jp2_files:

        info = identify_jp2(
            jp2_path
        )

        if info is None:
            print(
                "SKIP unrecognized filename:",
                jp2_path.name
            )
            continue

        tile_id = info["tile_id"]
        date = info["date"]

        print("\n" + "-" * 50)
        print("JP2:", jp2_path.name)
        print("Tile:", tile_id)
        print("Date:", date)

        matching = []

        for site_id, assignment in (
            assignments.items()
        ):

            if (
                assignment["tile_id"] == tile_id
                and assignment["date"] == date
                and site_id not in existing_before
            ):
                matching.append(site_id)

        print(
            "Pending matching sites:",
            len(matching)
        )

        if not matching:
            continue

        try:

            with rasterio.open(
                jp2_path
            ) as src:

                print("CRS:", src.crs)
                print(
                    "Raster:",
                    src.width,
                    "x",
                    src.height
                )

                if src.crs is None:
                    raise ValueError(
                        "JP2 has no CRS"
                    )

                if src.count < 3:
                    raise ValueError(
                        "JP2 has fewer than 3 bands"
                    )

                if (
                    abs(src.res[0] - 10) > 0.01
                    or abs(src.res[1] - 10) > 0.01
                ):
                    raise ValueError(
                        "JP2 resolution is not 10m"
                    )

                for site_id in matching:

                    print(
                        "\nProcessing:",
                        site_id
                    )

                    record = (
                        records_by_site[site_id]
                    )

                    record["local_jp2"] = (
                        str(jp2_path)
                    )

                    try:

                        status, valid, details = (
                            crop_site(
                                src,
                                sites[site_id],
                                date
                            )
                        )

                        record["status"] = status
                        record["valid_percent"] = (
                            round(valid, 3)
                            if valid is not None
                            else None
                        )
                        record["details"] = details

                        print("Status:", status)

                        if valid is not None:
                            print(
                                "Valid pixels:",
                                f"{valid:.2f}%"
                            )

                    except Exception as error:

                        record["status"] = "error"
                        record["details"] = str(
                            error
                        )

                        print(
                            "ERROR:",
                            error
                        )

        except Exception as error:

            print(
                "Cannot open/process JP2:",
                error
            )

            for site_id in matching:

                record = (
                    records_by_site[site_id]
                )

                if record["status"] == (
                    "waiting_for_jp2"
                ):
                    record["status"] = "error"
                    record["details"] = str(
                        error
                    )

    print("\nSTEP 4: Checking dataset...")

    existing_after = existing_site_ids()

    total = len(sites)
    complete = len(
        set(sites) & existing_after
    )

    missing_ids = sorted(
        set(sites) - existing_after
    )

    new_count = len(
        existing_after - existing_before
    )

    summary = {
        "database_sites": total,
        "complete_image_pairs": complete,
        "new_sites_this_run": new_count,
        "remaining_sites": len(missing_ids),
        "local_jp2_files": len(jp2_files),
        "free_disk_mb": round(
            free_space_mb(), 1
        ),
        "missing_site_ids": (
            ", ".join(missing_ids)
        )
    }

    save_report(
        [
            records_by_site[site_id]
            for site_id in sorted(sites)
        ],
        summary
    )

    print("\n" + "=" * 50)
    print("PROCESSING COMPLETE")
    print("=" * 50)

    print(
        "Sites in database:",
        total
    )

    print(
        "Complete image pairs:",
        complete
    )

    print(
        "New sites this run:",
        new_count
    )

    print(
        "Remaining sites:",
        len(missing_ids)
    )

    print(
        "Missing site IDs:",
        ", ".join(missing_ids)
    )

    print(
        "Free disk space:",
        f"{free_space_mb():.1f} MB"
    )

    print("\nExcel report:")
    print(REPORT)


if __name__ == "__main__":
    main()
