
from pathlib import Path

import pandas as pd
import rasterio

from rasterio.warp import transform as convert_coords
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter


# ==========================================
# 1. PROJECT PATHS
# ==========================================

PROJECT = Path(r"D:\Karlag_SI_ML")

DATABASE = PROJECT / "karlag_database.xlsx"

# NEW 10 x 10 km satellite image
IMAGE = PROJECT / "S029_Karazhar_10km_RGB.tif"

# NEW Excel output
OUTPUT = (
    PROJECT
    / "dataset"
    / "metadata"
    / "Karazhar_10km_Spatial_Matches.xlsx"
)

IMAGE_ID = "S029_20261104_RGB_10km"
SITE_ID = "S029"


# ==========================================
# 2. CHECK FILES
# ==========================================

print("STEP 1: Checking input files...")

if not DATABASE.is_file():
    raise FileNotFoundError(
        f"Archaeological database not found:\n{DATABASE}"
    )

if not IMAGE.is_file():
    raise FileNotFoundError(
        f"Satellite image not found:\n{IMAGE}"
    )

print("Database found!")
print("Satellite image found!")


# ==========================================
# 3. READ ARCHAEOLOGICAL DATABASE
# ==========================================

print("\nSTEP 2: Reading database...")

sites = pd.read_excel(
    DATABASE,
    sheet_name="sites"
)

features = pd.read_excel(
    DATABASE,
    sheet_name="features"
)

print("Total sites:", len(sites))
print("Total features:", len(features))

print("\nSite columns:")
print(sites.columns.tolist())

print("\nFeature columns:")
print(features.columns.tolist())


# ==========================================
# 4. FIND COORDINATE COLUMNS
# ==========================================

def find_column(df, candidates):

    available = {
        str(col).strip().lower(): col
        for col in df.columns
    }

    for candidate in candidates:
        if candidate.lower() in available:
            return available[candidate.lower()]

    raise ValueError(
        "Coordinate column not found.\n"
        f"Available columns: {df.columns.tolist()}"
    )


LAT_NAMES = [
    "lat",
    "latitude",
    "center_lat",
    "lat_wgs84",
    "y_wgs84"
]

LON_NAMES = [
    "lon",
    "lng",
    "longitude",
    "center_lon",
    "lon_wgs84",
    "x_wgs84"
]


# ==========================================
# 5. SPATIAL MATCHING FUNCTION
# ==========================================

def match_points(df, src, record_type):

    lat_col = find_column(df, LAT_NAMES)
    lon_col = find_column(df, LON_NAMES)

    # Preserve original columns even when
    # no points fall inside the image.
    output_columns = list(df.columns) + [
        "record_type",
        "image_id",
        "pixel_x",
        "pixel_y",
        "inside_image",
        "visual_status"
    ]

    matches = []

    for _, record in df.iterrows():

        latitude = pd.to_numeric(
            record[lat_col],
            errors="coerce"
        )

        longitude = pd.to_numeric(
            record[lon_col],
            errors="coerce"
        )

        if pd.isna(latitude) or pd.isna(longitude):
            continue

        if not (-90 <= latitude <= 90):
            continue

        if not (-180 <= longitude <= 180):
            continue

        # Convert GPS to image CRS
        xs, ys = convert_coords(
            "EPSG:4326",
            src.crs,
            [float(longitude)],
            [float(latitude)]
        )

        x = xs[0]
        y = ys[0]

        # Convert map coordinates to pixels
        pixel_row, pixel_col = src.index(x, y)

        inside = (
            0 <= pixel_col < src.width
            and 0 <= pixel_row < src.height
        )

        if inside:

            result = record.to_dict()

            result["record_type"] = record_type
            result["image_id"] = IMAGE_ID

            result["pixel_x"] = int(pixel_col)
            result["pixel_y"] = int(pixel_row)

            result["inside_image"] = True

            # NOT a confirmed ML annotation
            result["visual_status"] = "not_reviewed"

            matches.append(result)

    return pd.DataFrame(
        matches,
        columns=output_columns
    )


# ==========================================
# 6. OPEN SATELLITE IMAGE
# ==========================================

print("\nSTEP 3: Opening satellite image...")

with rasterio.open(IMAGE) as src:

    print("CRS:", src.crs)
    print("Width:", src.width)
    print("Height:", src.height)
    print("Resolution:", src.res)
    print("Bounds:", src.bounds)

    if src.crs is None:
        raise ValueError(
            "Satellite image has no CRS."
        )

    # Match all archaeological points
    print("\nSTEP 4: Matching site coordinates...")

    matched_sites = match_points(
        sites,
        src,
        "site"
    )

    print(
        "Sites inside image:",
        len(matched_sites)
    )

    print("\nSTEP 5: Matching feature coordinates...")

    matched_features = match_points(
        features,
        src,
        "feature"
    )

    print(
        "Features inside image:",
        len(matched_features)
    )


# ==========================================
# 7. IDENTIFY S029 FEATURES
# ==========================================

print("\nSTEP 6: Checking Karazhar features...")

site_id_col = find_column(
    features,
    ["site_id"]
)

karazhar_features = features[
    features[site_id_col]
    .astype(str)
    .str.strip()
    .str.upper()
    == SITE_ID
].copy()

print(
    "Features linked to S029 in database:",
    len(karazhar_features)
)

if "site_id" in matched_features.columns:

    matched_karazhar = matched_features[
        matched_features["site_id"]
        .astype(str)
        .str.strip()
        .str.upper()
        == SITE_ID
    ].copy()

else:
    matched_karazhar = matched_features.iloc[0:0].copy()

print(
    "S029 features inside 10 km image:",
    len(matched_karazhar)
)


# ==========================================
# 8. CREATE SUMMARY
# ==========================================

print("\nSTEP 7: Creating summary...")

summary = pd.DataFrame([
    {
        "metric": "site_id",
        "value": SITE_ID
    },
    {
        "metric": "image_id",
        "value": IMAGE_ID
    },
    {
        "metric": "image_size",
        "value": "10 km x 10 km"
    },
    {
        "metric": "acquisition_date",
        "value": "2026-11-04"
    },
    {
        "metric": "total_sites",
        "value": len(sites)
    },
    {
        "metric": "total_features",
        "value": len(features)
    },
    {
        "metric": "sites_inside_image",
        "value": len(matched_sites)
    },
    {
        "metric": "features_inside_image",
        "value": len(matched_features)
    },
    {
        "metric": "S029_features_in_database",
        "value": len(karazhar_features)
    },
    {
        "metric": "S029_features_inside_image",
        "value": len(matched_karazhar)
    },
    {
        "metric": "annotation_status",
        "value": "not_reviewed"
    },
    {
        "metric": "important_note",
        "value": (
            "Spatial matches are not "
            "confirmed visible objects."
        )
    }
])


# ==========================================
# 9. SAVE EXCEL RESULTS
# ==========================================

print("\nSTEP 8: Saving Excel...")

OUTPUT.parent.mkdir(
    parents=True,
    exist_ok=True
)

with pd.ExcelWriter(
    OUTPUT,
    engine="openpyxl"
) as writer:

    summary.to_excel(
        writer,
        sheet_name="summary",
        index=False
    )

    matched_sites.to_excel(
        writer,
        sheet_name="matched_sites",
        index=False
    )

    matched_features.to_excel(
        writer,
        sheet_name="matched_features",
        index=False
    )

    karazhar_features.to_excel(
        writer,
        sheet_name="S029_all_features",
        index=False
    )

    matched_karazhar.to_excel(
        writer,
        sheet_name="S029_inside_image",
        index=False
    )

    # Excel formatting
    for sheet in writer.book.worksheets:

        sheet.freeze_panes = "A2"

        for cell in sheet[1]:

            cell.fill = PatternFill(
                fill_type="solid",
                fgColor="17365D"
            )

            cell.font = Font(
                bold=True,
                color="FFFFFF"
            )

        for col in sheet.columns:

            letter = get_column_letter(
                col[0].column
            )

            sheet.column_dimensions[
                letter
            ].width = 23

        if sheet.max_row > 1:
            sheet.auto_filter.ref = (
                sheet.dimensions
            )


# ==========================================
# 10. FINAL RESULTS
# ==========================================

print("\n====================================")
print("SPATIAL MATCHING COMPLETE!")
print("====================================")

print("Image:", IMAGE_ID)
print("Total sites:", len(sites))
print("Total features:", len(features))

print(
    "Sites inside image:",
    len(matched_sites)
)

print(
    "Features inside image:",
    len(matched_features)
)

print(
    "S029 features in database:",
    len(karazhar_features)
)

print(
    "S029 features inside image:",
    len(matched_karazhar)
)

print("\nExcel saved to:")
print(OUTPUT)

print("\nIMPORTANT:")
print(
    "Spatial matching does not confirm "
    "that objects are visible in the image."
)
