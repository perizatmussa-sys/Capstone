from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from pathlib import Path
from datetime import datetime
import re

import numpy as np
import pandas as pd
import rasterio

from PIL import Image
from rasterio.windows import Window
from rasterio.windows import transform as window_transform
from rasterio.warp import transform as convert_coords


# ==========================================
# 1. PROJECT SETTINGS
# ==========================================

PROJECT = Path(r"D:\Karlag_SI_ML")

DATABASE = PROJECT / "karlag_database.xlsx"

SATELLITE_FOLDER = Path(
    r"D:\Загрузка\S2C_MSIL2A_20261104T062021_N0513_R034_T43UCR_20261104T094421.SAFE\S2C_MSIL2A_20261104T062021_N0513_R034_T43UCR_20261104T094421.SAFE\GRANULE\L2A_T43UCR_A011301_20261104T062024\IMG_DATA\R10m"
)

SATELLITE_FILE = (
    SATELLITE_FOLDER
    / "T43UCR_20261104T062021_TCI_10m.jp2"
)

OUTPUT_ROOT = PROJECT / "dataset" / "sites_S60"

TIFF_FOLDER = OUTPUT_ROOT / "geotiff"
PNG_FOLDER = OUTPUT_ROOT / "previews"
METADATA_FOLDER = OUTPUT_ROOT / "metadata"

MANIFEST = METADATA_FOLDER / "S60_Image_Manifest.xlsx"

# ==========================================
# 2. SATELLITE METADATA
# ==========================================

IMAGE_DATE = "2026-11-04"
SATELLITE = "Sentinel-2C"
PRODUCT_LEVEL = "L2A"
TILE_ID = "T43UCR"

# Same area as our successful Karazhar crop.
CROP_SIZE_METERS = 10000

# Never overwrite previously created images.
OVERWRITE_IMAGES = False


# ==========================================
# 3. HELPERS
# ==========================================

def safe_filename(value):
    text = str(value).strip()
    return re.sub(
        r"[^A-Za-z0-9_-]",
        "_",
        text
    )


def to_rgb(data):
    """
    Convert 3-band raster to PNG RGB.
    Sentinel-2 TCI is normally uint8.
    """

    rgb = np.moveaxis(data, 0, -1)

    if rgb.dtype == np.uint8:
        return rgb

    result = np.zeros(
        rgb.shape,
        dtype=np.uint8
    )

    for band in range(3):
        channel = rgb[:, :, band].astype(
            np.float32
        )

        low = np.nanpercentile(channel, 2)
        high = np.nanpercentile(channel, 98)

        if high > low:
            scaled = (
                (channel - low)
                / (high - low)
            )

            result[:, :, band] = (
                np.clip(scaled, 0, 1) * 255
            ).astype(np.uint8)

    return result


def build_window(src, x, y):
    """
    Create a 10 km square crop.
    Return None if the entire square
    does not fit inside the satellite tile.
    """

    half = CROP_SIZE_METERS / 2

    left = x - half
    right = x + half
    bottom = y - half
    top = y + half

    bounds = src.bounds

    fits = (
        left >= bounds.left
        and right <= bounds.right
        and bottom >= bounds.bottom
        and top <= bounds.top
    )

    if not fits:
        return None

    row_start, col_start = src.index(
        left, top
    )

    row_end, col_end = src.index(
        right, bottom
    )

    width = col_end - col_start
    height = row_end - row_start

    if width <= 0 or height <= 0:
        return None

    return Window(
        col_off=col_start,
        row_off=row_start,
        width=width,
        height=height
    )


# ==========================================
# 4. CHECK INPUTS
# ==========================================

print("STEP 1: Checking files...")

if not DATABASE.is_file():
    raise FileNotFoundError(
        f"Database not found:\n{DATABASE}"
    )

if not SATELLITE_FILE.is_file():
    raise FileNotFoundError(
        f"Satellite file not found:\n"
        f"{SATELLITE_FILE}"
    )

for folder in [
    TIFF_FOLDER,
    PNG_FOLDER,
    METADATA_FOLDER
]:
    folder.mkdir(
        parents=True,
        exist_ok=True
    )


# ==========================================
# 5. LOAD ALL SITES
# ==========================================

print("\nSTEP 2: Reading S60 database...")

sites = pd.read_excel(
    DATABASE,
    sheet_name="sites"
)

required = [
    "site_id",
    "name",
    "lon",
    "lat"
]

for column in required:
    if column not in sites.columns:
        raise ValueError(
            f"Missing database column: {column}"
        )

print("Total sites:", len(sites))

if len(sites) != 60:
    print(
        "WARNING: Expected 60 sites, "
        f"found {len(sites)}"
    )

if sites["site_id"].duplicated().any():
    raise ValueError(
        "Duplicate site_id values in database."
    )


# ==========================================
# 6. PROCESS ALL SITES
# ==========================================

print("\nSTEP 3: Opening Sentinel-2 tile...")

records = []

with rasterio.open(SATELLITE_FILE) as src:

    print("CRS:", src.crs)
    print("Resolution:", src.res)
    print("Tile bounds:", src.bounds)

    if src.crs is None:
        raise ValueError(
            "Satellite image has no CRS."
        )

    if src.crs.to_epsg() != 32643:
        raise ValueError(
            "Expected EPSG:32643 for this tile."
        )

    if src.count < 3:
        raise ValueError(
            "Expected RGB satellite image."
        )

    for index, site in sites.iterrows():

        site_id = str(
            site["site_id"]
        ).strip()

        site_name = str(
            site["name"]
        ).strip()

        lon = pd.to_numeric(
            site["lon"],
            errors="coerce"
        )

        lat = pd.to_numeric(
            site["lat"],
            errors="coerce"
        )

        print(
            f"\n[{index + 1}/{len(sites)}] "
            f"{site_id} - {site_name}"
        )

        record = site.to_dict()

        record.update({
            "image_id": "",
            "acquisition_date": IMAGE_DATE,
            "satellite": SATELLITE,
            "product_level": PRODUCT_LEVEL,
            "tile_id": TILE_ID,
            "crop_size_m": CROP_SIZE_METERS,
            "image_width": None,
            "image_height": None,
            "resolution_m": None,
            "crs": "",
            "tif_path": "",
            "png_path": "",
            "processing_status": "",
            "quality_status": "needs_review",
            "annotation_status": "not_started",
            "notes": ""
        })

        if pd.isna(lon) or pd.isna(lat):
            record["processing_status"] = (
                "invalid_coordinates"
            )
            record["notes"] = (
                "Missing or invalid coordinates"
            )
            records.append(record)

            print("SKIP: invalid coordinates")
            continue

        if not (
            -180 <= float(lon) <= 180
            and -90 <= float(lat) <= 90
        ):
            record["processing_status"] = (
                "invalid_coordinates"
            )
            record["notes"] = (
                "Coordinates outside valid GPS range"
            )
            records.append(record)

            print("SKIP: invalid GPS range")
            continue

        try:
            xs, ys = convert_coords(
                "EPSG:4326",
                src.crs,
                [float(lon)],
                [float(lat)]
            )

            x = xs[0]
            y = ys[0]

            window = build_window(
                src, x, y
            )

            if window is None:
                record["processing_status"] = (
                    "needs_other_tile"
                )
                record["notes"] = (
                    "Full 10 km crop is outside "
                    "the available Sentinel-2 tile"
                )

                records.append(record)

                print("SKIP: needs another tile")
                continue

            image_id = (
                f"{safe_filename(site_id)}"
                f"_{IMAGE_DATE.replace('-', '')}"
                f"_RGB_10km"
            )

            tif_path = (
                TIFF_FOLDER
                / f"{image_id}.tif"
            )

            png_path = (
                PNG_FOLDER
                / f"{image_id}.png"
            )

            record["image_id"] = image_id
            record["tif_path"] = str(tif_path)
            record["png_path"] = str(png_path)

            # Read only the selected 10 km crop.
            data = src.read(
                [1, 2, 3],
                window=window
            )

            if data.size == 0:
                raise ValueError(
                    "Empty satellite crop"
                )

            record["image_width"] = (
                int(data.shape[2])
            )

            record["image_height"] = (
                int(data.shape[1])
            )

            record["resolution_m"] = (
                float(abs(src.res[0]))
            )

            record["crs"] = str(src.crs)

            # Save georeferenced GeoTIFF.
            if (
                OVERWRITE_IMAGES
                or not tif_path.exists()
            ):
                profile = src.profile.copy()

                profile.update(
                    driver="GTiff",
                    count=3,
                    height=data.shape[1],
                    width=data.shape[2],
                    transform=window_transform(
                        window,
                        src.transform
                    ),
                    compress="deflate"
                )

                with rasterio.open(
                    tif_path,
                    "w",
                    **profile
                ) as dst:
                    dst.write(data)

            # Save RGB PNG preview.
            if (
                OVERWRITE_IMAGES
                or not png_path.exists()
            ):
                rgb = to_rgb(data)

                Image.fromarray(
                    rgb
                ).save(png_path)

            record["processing_status"] = (
                "created"
            )

            record["notes"] = (
                "Full 10 km crop available. "
                "Visual quality not yet reviewed."
            )

            print(
                "SUCCESS:",
                image_id
            )

        except Exception as error:
            record["processing_status"] = (
                "error"
            )
            record["notes"] = str(error)

            print(
                "ERROR:",
                error
            )

        records.append(record)


# ==========================================
# 7. BUILD METADATA
# ==========================================

print("\nSTEP 4: Creating metadata...")

manifest_df = pd.DataFrame(
    records
)

summary = (
    manifest_df["processing_status"]
    .value_counts()
    .reset_index()
)

summary.columns = [
    "processing_status",
    "site_count"
]

# The manifest is generated from scratch.
# Do not enter manual review notes here:
# future reruns will replace this file.
with pd.ExcelWriter(
    MANIFEST,
    engine="openpyxl"
) as writer:

    manifest_df.to_excel(
        writer,
        sheet_name="all_sites",
        index=False
    )

    summary.to_excel(
        writer,
        sheet_name="summary",
        index=False
    )

    for sheet in writer.book.worksheets:
        sheet.freeze_panes = "A2"

        for cell in sheet[1]:
            cell.font = Font(
                bold=True,
                color="FFFFFF"
            )
            cell.fill = PatternFill(
                "solid",
                fgColor="17365D"
            )

        if sheet.max_row > 1:
            sheet.auto_filter.ref = (
                sheet.dimensions
            )

        for column in sheet.columns:
            letter = get_column_letter(
                column[0].column
            )
            sheet.column_dimensions[
                letter
            ].width = 24


# ==========================================
# 8. FINAL RESULTS
# ==========================================

print("\n====================================")
print("S60 SATELLITE PROCESSING COMPLETE")
print("====================================")

for _, row in summary.iterrows():
    print(
        f"{row['processing_status']}: "
        f"{row['site_count']}"
    )

print("\nManifest:")
print(MANIFEST)

print("\nGeoTIFF folder:")
print(TIFF_FOLDER)

print("\nPNG folder:")
print(PNG_FOLDER)

print("\nIMPORTANT:")
print(
    "Only sites covered by the current "
    "Sentinel-2 tile were processed."
)
print(
    "Other sites require additional "
    "satellite products."
)
