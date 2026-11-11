
from pathlib import Path
from datetime import date
import csv
import json
import shutil

import numpy as np
import rasterio
from PIL import Image
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter


# ============================================
# 1. PATHS
# ============================================

PROJECT = Path(r"D:\Karlag_SI_ML")

SOURCE_TIF = PROJECT / "S029_Karazhar_RGB.tif"

DATASET = PROJECT / "dataset"

RAW = DATASET / "images" / "raw_crops"
PREVIEWS = DATASET / "images" / "previews"
TILES = DATASET / "images" / "tiles"
METADATA = DATASET / "metadata"
ANNOTATIONS = DATASET / "annotations"

for folder in [RAW, PREVIEWS, TILES, METADATA, ANNOTATIONS]:
    folder.mkdir(parents=True, exist_ok=True)


# ============================================
# 2. IDENTIFIERS
# ============================================

IMAGE_ID = "S029_20261104_RGB_001"
PRODUCT_ID = (
    "S2C_MSIL2A_20261104T062021_"
    "N0513_R034_T43UCR_20261104T094421"
)

TIF_NAME = IMAGE_ID + ".tif"
PNG_NAME = IMAGE_ID + ".png"

OUTPUT_TIF = RAW / TIF_NAME
OUTPUT_PNG = PREVIEWS / PNG_NAME


# ============================================
# 3. CHECK AND COPY GEOTIFF
# ============================================

if not SOURCE_TIF.is_file():
    raise FileNotFoundError(
        f"Cannot find source image: {SOURCE_TIF}"
    )

shutil.copy2(SOURCE_TIF, OUTPUT_TIF)

with rasterio.open(OUTPUT_TIF) as src:
    data = src.read()

    if src.count != 3:
        raise ValueError("Expected 3 RGB channels")

    width = src.width
    height = src.height
    crs = str(src.crs)
    resolution_x = abs(src.res[0])
    resolution_y = abs(src.res[1])
    bounds = src.bounds

    # TCI is an 8-bit RGB visualization.
    if data.dtype != np.uint8:
        raise ValueError(
            f"Expected uint8 TCI image, got {data.dtype}"
        )

    # Rasterio: bands, rows, columns
    # Pillow: rows, columns, channels
    rgb = np.moveaxis(data, 0, -1)

    Image.fromarray(rgb, mode="RGB").save(
        OUTPUT_PNG
    )

print("GeoTIFF copied:", OUTPUT_TIF)
print("PNG preview created:", OUTPUT_PNG)


# ============================================
# 4. IMAGE METADATA
# ============================================

record = {
    "image_id": IMAGE_ID,
    "site_id": "S029",
    "site_name": "Karazhar",
    "center_lat": 49.861306,
    "center_lon": 72.583222,
    "acquisition_date": "2026-11-04",
    "satellite": "Sentinel-2C",
    "product_level": "L2A",
    "product_id": PRODUCT_ID,
    "tile_id": "T43UCR",
    "image_type": "TCI RGB crop",
    "width_px": width,
    "height_px": height,
    "resolution_x_m": resolution_x,
    "resolution_y_m": resolution_y,
    "crs": crs,
    "bounds_left": bounds.left,
    "bounds_bottom": bounds.bottom,
    "bounds_right": bounds.right,
    "bounds_top": bounds.top,
    "geotiff_path": f"images/raw_crops/{TIF_NAME}",
    "preview_path": f"images/previews/{PNG_NAME}",
    "annotation_status": "not_started",
    "split": "unassigned",
    "quality_status": "needs_review",
    "source_platform": "Copernicus Browser",
    "notes": (
        "Sentinel-2 10m RGB. Archaeological "
        "features not yet verified in imagery."
    ),
}


# ============================================
# 5. SAVE CSV
# ============================================

csv_path = METADATA / "images.csv"

with open(
    csv_path, "w", newline="", encoding="utf-8-sig"
) as f:
    writer = csv.DictWriter(
        f, fieldnames=list(record.keys())
    )
    writer.writeheader()
    writer.writerow(record)


# ============================================
# 6. CREATE EXCEL WORKBOOK
# ============================================

workbook = Workbook()

readme = workbook.active
readme.title = "START_HERE"

readme.append(["KARLAG PILOT DATASET"])
readme.append(["Site", "S029 Karazhar"])
readme.append(["Source", "Sentinel-2C L2A"])
readme.append(["Acquisition date", "2026-11-04"])
readme.append(["Number of image crops", 1])
readme.append(["Confirmed annotations", 0])
readme.append(["Dataset status", "Pilot / preparation"])
readme.append([
    "Important",
    "Satellite imagery does not independently "
    "confirm archaeological identity."
])

images_sheet = workbook.create_sheet("images")
images_sheet.append(list(record.keys()))
images_sheet.append(list(record.values()))

sites_sheet = workbook.create_sheet("sites")
sites_sheet.append([
    "site_id",
    "site_name",
    "latitude",
    "longitude",
    "coordinate_status",
    "review_status",
    "notes"
])
sites_sheet.append([
    "S029",
    "Karazhar",
    49.861306,
    72.583222,
    "check_against_source_database",
    "needs_review",
    "Pilot center coordinate"
])

annotation_sheet = workbook.create_sheet("annotations")
annotation_sheet.append([
    "annotation_id",
    "image_id",
    "class_name",
    "bbox_xmin",
    "bbox_ymin",
    "bbox_xmax",
    "bbox_ymax",
    "reviewed_by",
    "review_status",
    "notes"
])

classes_sheet = workbook.create_sheet("classes")
classes_sheet.append([
    "class_name",
    "description",
    "status"
])
classes_sheet.append([
    "building_traces",
    "Possible visible traces of buildings",
    "provisional"
])
classes_sheet.append([
    "mudbrick_building",
    "Mudbrick structure",
    "provisional"
])
classes_sheet.append([
    "annular_pit",
    "Circular or annular pit",
    "provisional"
])
classes_sheet.append([
    "ground_mark",
    "Surface mark requiring interpretation",
    "provisional"
])


# ============================================
# 7. SIMPLE EXCEL FORMATTING
# ============================================

for sheet in workbook.worksheets:
    sheet.freeze_panes = "B2"

    for cell in sheet[1]:
        cell.fill = PatternFill(
            "solid", fgColor="17365D"
        )
        cell.font = Font(
            bold=True, color="FFFFFF"
        )
        cell.alignment = Alignment(
            vertical="center"
        )

    sheet.row_dimensions[1].height = 28

    for column in sheet.columns:
        letter = get_column_letter(column[0].column)

        max_length = max(
            len(str(cell.value or ""))
            for cell in column
        )

        sheet.column_dimensions[letter].width = min(
            max(max_length + 3, 15),
            45
        )

    if sheet.title != "START_HERE":
        sheet.auto_filter.ref = sheet.dimensions


xlsx_path = METADATA / "Karlag_Pilot_Dataset.xlsx"
workbook.save(xlsx_path)


# ============================================
# 8. EMPTY ANNOTATION TEMPLATE
# ============================================

annotation_csv = ANNOTATIONS / "annotations.csv"

with open(
    annotation_csv, "w", newline="", encoding="utf-8-sig"
) as f:
    writer = csv.writer(f)
    writer.writerow([
        "annotation_id",
        "image_id",
        "class_name",
        "bbox_xmin",
        "bbox_ymin",
        "bbox_xmax",
        "bbox_ymax",
        "reviewed_by",
        "review_status",
        "notes"
    ])


# ============================================
# 9. JSON METADATA
# ============================================

with open(
    METADATA / "image_metadata.json",
    "w",
    encoding="utf-8"
) as f:
    json.dump(
        record, f, ensure_ascii=False, indent=2
    )


print("\nSUCCESS! PILOT DATASET CREATED")
print("Dataset folder:", DATASET)
print("Excel file:", xlsx_path)
print("Image size:", width, "x", height)
print("CRS:", crs)
print("Resolution:", resolution_x, resolution_y)
