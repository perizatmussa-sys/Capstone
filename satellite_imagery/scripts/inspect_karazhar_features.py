
from pathlib import Path
import math

import numpy as np
import pandas as pd
import rasterio
import matplotlib.pyplot as plt

from PIL import Image, ImageDraw, ImageFont
from openpyxl import Workbook
from openpyxl.styles import (
    Font,
    PatternFill,
    Alignment
)
from openpyxl.utils import get_column_letter


# ==========================================
# 1. PROJECT SETTINGS
# ==========================================

PROJECT = Path(r"D:\Karlag_SI_ML")

IMAGE_FILE = (
    PROJECT / "S029_Karazhar_10km_RGB.tif"
)

MATCHES_FILE = (
    PROJECT
    / "dataset"
    / "metadata"
    / "Karazhar_10km_Spatial_Matches.xlsx"
)

OUTPUT_FOLDER = (
    PROJECT
    / "dataset"
    / "images"
    / "feature_inspection"
    / "S029"
)

EXCEL_OUTPUT = (
    PROJECT
    / "dataset"
    / "metadata"
    / "S029_Feature_Inspection.xlsx"
)

CONTACT_SHEET = (
    OUTPUT_FOLDER
    / "S029_All_Features_Contact_Sheet.png"
)

SITE_ID = "S029"
IMAGE_ID = "S029_20261104_RGB_10km"

# Each crop covers 500 x 500 metres.
CROP_SIZE_METERS = 500

# Display size only; does not add detail.
PREVIEW_SIZE_PIXELS = 400


# ==========================================
# 2. CHECK FILES
# ==========================================

print("STEP 1: Checking files...")

if not IMAGE_FILE.is_file():
    raise FileNotFoundError(
        f"Satellite image missing:\n{IMAGE_FILE}"
    )

if not MATCHES_FILE.is_file():
    raise FileNotFoundError(
        f"Spatial matches Excel missing:\n"
        f"{MATCHES_FILE}"
    )

OUTPUT_FOLDER.mkdir(
    parents=True,
    exist_ok=True
)

EXCEL_OUTPUT.parent.mkdir(
    parents=True,
    exist_ok=True
)


# ==========================================
# 3. LOAD FEATURES
# ==========================================

print("\nSTEP 2: Loading archaeological features...")

features = pd.read_excel(
    MATCHES_FILE,
    sheet_name="S029_inside_image"
)

required_columns = [
    "feature_id",
    "site_id",
    "ml_class",
    "pixel_x",
    "pixel_y",
    "lon",
    "lat"
]

missing_columns = [
    col for col in required_columns
    if col not in features.columns
]

if missing_columns:
    raise ValueError(
        f"Missing Excel columns: {missing_columns}"
    )

features = features[
    features["site_id"]
    .astype(str)
    .str.strip()
    .str.upper()
    == SITE_ID
].copy()

features = features.sort_values(
    "feature_id"
).reset_index(drop=True)

print("Features found:", len(features))

if features.empty:
    raise ValueError(
        "No S029 features found inside image."
    )


# ==========================================
# 4. HELPER FUNCTIONS
# ==========================================

def normalize_rgb(data):
    """
    Convert raster bands into displayable RGB.
    TCI is usually already uint8.
    """

    rgb = np.moveaxis(data, 0, -1)

    if rgb.dtype == np.uint8:
        return rgb

    rgb = rgb.astype(np.float32)

    result = np.zeros_like(
        rgb,
        dtype=np.uint8
    )

    for band in range(min(3, rgb.shape[2])):

        channel = rgb[:, :, band]

        low = np.nanpercentile(
            channel, 2
        )

        high = np.nanpercentile(
            channel, 98
        )

        if high > low:
            scaled = (
                (channel - low)
                / (high - low)
            )

            result[:, :, band] = (
                np.clip(scaled, 0, 1) * 255
            ).astype(np.uint8)

    return result


def make_preview(rgb_array, feature_id):
    """
    Enlarge the image for inspection.
    Use nearest-neighbour interpolation,
    so original pixels remain distinguishable.
    """

    image = Image.fromarray(
        rgb_array,
        mode="RGB"
    )

    image = image.resize(
        (
            PREVIEW_SIZE_PIXELS,
            PREVIEW_SIZE_PIXELS
        ),
        resample=Image.Resampling.NEAREST
    )

    draw = ImageDraw.Draw(image)

    center = PREVIEW_SIZE_PIXELS // 2

    # Crosshair at the feature coordinate.
    # The marker is a reference point,
    # not a confirmed object boundary.
    marker_color = (255, 255, 0)

    draw.line(
        (center - 16, center,
         center + 16, center),
        fill=marker_color,
        width=3
    )

    draw.line(
        (center, center - 16,
         center, center + 16),
        fill=marker_color,
        width=3
    )

    draw.ellipse(
        (
            center - 6,
            center - 6,
            center + 6,
            center + 6
        ),
        outline=(0, 0, 0),
        width=2
    )

    return image


# ==========================================
# 5. READ RASTER AND CROP FEATURES
# ==========================================

print("\nSTEP 3: Creating feature crops...")

inspection_records = []
preview_images = []

with rasterio.open(IMAGE_FILE) as src:

    if src.count < 3:
        raise ValueError(
            "Image must contain RGB bands."
        )

    pixel_size_x = abs(
        src.transform.a
    )

    pixel_size_y = abs(
        src.transform.e
    )

    if pixel_size_x <= 0 or pixel_size_y <= 0:
        raise ValueError(
            "Invalid raster pixel resolution."
        )

    crop_width = round(
        CROP_SIZE_METERS / pixel_size_x
    )

    crop_height = round(
        CROP_SIZE_METERS / pixel_size_y
    )

    print(
        "Crop dimensions:",
        crop_width,
        "x",
        crop_height,
        "pixels"
    )

    for _, feature in features.iterrows():

        feature_id = str(
            feature["feature_id"]
        )

        ml_class = str(
            feature["ml_class"]
        )

        lon = float(feature["lon"])
        lat = float(feature["lat"])

        # Recalculate pixel location from
        # geographic coordinates to avoid
        # rounding the feature centre.
        from rasterio.warp import transform

        xs, ys = transform(
            "EPSG:4326",
            src.crs,
            [lon],
            [lat]
        )

        col_float, row_float = (
            ~src.transform
        ) * (xs[0], ys[0])

        col_start = round(
            col_float - crop_width / 2
        )

        row_start = round(
            row_float - crop_height / 2
        )

        from rasterio.windows import Window

        window = Window(
            col_off=col_start,
            row_off=row_start,
            width=crop_width,
            height=crop_height
        )

        # Read with padding so the crop
        # remains 50 x 50 even at edges.
        data = src.read(
            [1, 2, 3],
            window=window,
            boundless=True,
            fill_value=0
        )

        rgb = normalize_rgb(data)

        # Save the original-resolution crop.
        raw_png = (
            OUTPUT_FOLDER
            / f"{feature_id}_raw_50px.png"
        )

        Image.fromarray(rgb).save(
            raw_png
        )

        # Create enlarged preview.
        preview = make_preview(
            rgb,
            feature_id
        )

        preview_path = (
            OUTPUT_FOLDER
            / f"{feature_id}_preview_400px.png"
        )

        preview.save(
            preview_path
        )

        preview_images.append(
            (
                feature_id,
                ml_class,
                preview.copy()
            )
        )

        inspection_records.append({
            "feature_id": feature_id,
            "site_id": SITE_ID,
            "image_id": IMAGE_ID,
            "ml_class": ml_class,
            "lon": lon,
            "lat": lat,
            "pixel_x": col_float,
            "pixel_y": row_float,
            "crop_size_m": CROP_SIZE_METERS,
            "crop_resolution_m": pixel_size_x,
            "raw_crop_path": str(raw_png),
            "preview_path": str(preview_path),
            "visibility": "not_reviewed",
            "annotation_status": "needs_review",
            "usable_for_ml": "uncertain",
            "review_notes": ""
        })

        print(
            f"Created: {feature_id} | {ml_class}"
        )


# ==========================================
# 6. CREATE CONTACT SHEET
# ==========================================

print("\nSTEP 4: Creating contact sheet...")

columns = 4

rows = math.ceil(
    len(preview_images) / columns
)

tile_width = 300
tile_height = 340

contact = Image.new(
    "RGB",
    (
        columns * tile_width,
        rows * tile_height
    ),
    color=(245, 245, 245)
)

draw = ImageDraw.Draw(contact)

for index, item in enumerate(
    preview_images
):

    feature_id, ml_class, preview = item

    col = index % columns
    row = index // columns

    x = col * tile_width
    y = row * tile_height

    small_preview = preview.resize(
        (280, 280),
        resample=Image.Resampling.NEAREST
    )

    contact.paste(
        small_preview,
        (x + 10, y + 10)
    )

    draw.text(
        (x + 12, y + 295),
        feature_id,
        fill=(0, 0, 0)
    )

    draw.text(
        (x + 12, y + 313),
        ml_class,
        fill=(50, 50, 50)
    )

contact.save(
    CONTACT_SHEET
)

print(
    "Contact sheet saved:",
    CONTACT_SHEET
)


# ==========================================
# 7. CREATE INSPECTION EXCEL
# ==========================================

print("\nSTEP 5: Creating inspection Excel...")

df = pd.DataFrame(
    inspection_records
)

wb = Workbook()

ws = wb.active
ws.title = "feature_review"

headers = list(df.columns)

ws.append(headers)

for _, record in df.iterrows():

    ws.append([
        None if pd.isna(value) else value
        for value in record.tolist()
    ])

# Format header
for cell in ws[1]:

    cell.fill = PatternFill(
        fill_type="solid",
        fgColor="17365D"
    )

    cell.font = Font(
        bold=True,
        color="FFFFFF"
    )

    cell.alignment = Alignment(
        wrap_text=True
    )

ws.freeze_panes = "A2"
ws.auto_filter.ref = ws.dimensions

for col_index, name in enumerate(
    headers,
    start=1
):

    letter = get_column_letter(
        col_index
    )

    if "path" in name:
        width = 55
    elif name == "review_notes":
        width = 45
    else:
        width = 20

    ws.column_dimensions[
        letter
    ].width = width

# Create instructions sheet
instructions = wb.create_sheet(
    "instructions"
)

instructions.append([
    "Field",
    "Meaning"
])

instruction_rows = [
    (
        "visibility",
        "visible / unclear / not_visible"
    ),
    (
        "annotation_status",
        "needs_review / reviewed"
    ),
    (
        "usable_for_ml",
        "yes / no / uncertain"
    ),
    (
        "review_notes",
        "Explain what can be seen"
    ),
    (
        "IMPORTANT",
        "Database coordinates are not "
        "confirmed object labels."
    ),
    (
        "RESOLUTION",
        "Sentinel-2 has 10 m pixels. "
        "Upscaling does not improve detail."
    )
]

for row in instruction_rows:
    instructions.append(row)

instructions.column_dimensions["A"].width = 25
instructions.column_dimensions["B"].width = 85

for cell in instructions[1]:
    cell.font = Font(
        bold=True,
        color="FFFFFF"
    )
    cell.fill = PatternFill(
        fill_type="solid",
        fgColor="17365D"
    )

wb.save(EXCEL_OUTPUT)


# ==========================================
# 8. FINAL SUMMARY
# ==========================================

print("\n====================================")
print("FEATURE INSPECTION DATASET READY!")
print("====================================")

print(
    "Features processed:",
    len(inspection_records)
)

print(
    "Original-resolution crops:",
    len(inspection_records)
)

print(
    "Enlarged previews:",
    len(inspection_records)
)

print("\nContact sheet:")
print(CONTACT_SHEET)

print("\nReview Excel:")
print(EXCEL_OUTPUT)

print("\nIMPORTANT:")
print(
    "All features are marked not_reviewed. "
    "No ML labels have been confirmed."
)
