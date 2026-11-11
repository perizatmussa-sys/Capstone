
from pathlib import Path
from collections import Counter
import re
import shutil

import numpy as np
import rasterio
from rasterio.transform import rowcol
from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont

from openpyxl import load_workbook
from openpyxl.drawing.image import Image as ExcelImage
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter


# ========================================
# SETTINGS
# ========================================

ROOT = Path(r"D:\Karlag_SI_ML")

DATABASE = ROOT / "karlag_database.xlsx"

BASE = ROOT / "dataset" / "sites_S60"

TIF_DIR = BASE / "geotiff"
PNG_DIR = BASE / "previews"

OUT_DIR = BASE / "annotated"
THUMB_DIR = BASE / "metadata" / "thumbnails"

VALIDATION = (
    BASE / "metadata"
    / "S60_Validation_Report.xlsx"
)

OUTPUT_EXCEL = (
    BASE / "metadata"
    / "S60_Validation_With_Features.xlsx"
)

# Set to True if you want neighboring sites
# to appear on the same image.
SHOW_NEIGHBORING_SITES = True

# Stop if there is dangerously little disk space.
MIN_FREE_MB = 80

# Feature colors
COLORS = {
    "annular_pit": "#16D69C",
    "building_other": "#FFD166",
    "later_overbuild": "#A779D5",
    "mudbrick_building": "#FF693C"
}

OTHER_COLOR = "#F06292"
SITE_COLOR = "#00D9FF"

FONT_PATHS = [
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\calibri.ttf"
]


# ========================================
# HELPERS
# ========================================

def font(size):

    for path in FONT_PATHS:
        if Path(path).exists():
            return ImageFont.truetype(path, size)

    return ImageFont.load_default()


def normalize(value):
    return re.sub(
        r"[^a-z0-9]+", "",
        str(value or "").lower()
    )


def read_records(sheet):

    rows = sheet.iter_rows(values_only=True)

    headers = [
        str(v).strip() if v is not None else ""
        for v in next(rows)
    ]

    return [
        dict(zip(headers, values))
        for values in rows
        if any(v is not None for v in values)
    ]


def get_value(record, aliases, required=True):

    lookup = {
        normalize(k): v
        for k, v in record.items()
    }

    for alias in aliases:

        value = lookup.get(normalize(alias))

        if value is not None and str(value).strip():
            return value

    if required:
        raise ValueError(
            "Cannot find column. Expected one of: "
            + ", ".join(aliases)
            + "\nAvailable columns: "
            + ", ".join(record.keys())
        )

    return None


def load_database():

    wb = load_workbook(
        DATABASE,
        read_only=True,
        data_only=True
    )

    try:
        sites_rows = read_records(wb["sites"])
        feature_rows = read_records(wb["features"])
    finally:
        wb.close()

    sites = {}

    for row in sites_rows:

        sid = str(get_value(
            row, ["site_id", "siteid"]
        )).strip().upper()

        sites[sid] = {
            "id": sid,
            "name": str(get_value(
                row, ["name", "site_name"],
                required=False
            ) or sid),
            "lon": float(get_value(
                row, ["lon", "longitude"]
            )),
            "lat": float(get_value(
                row, ["lat", "latitude"]
            ))
        }

    features = []

    for index, row in enumerate(feature_rows, 1):

        fid = get_value(
            row,
            ["feature_id", "featureid", "id"],
            required=False
        )

        feature_type = get_value(
            row,
            [
                "feature_type", "type",
                "category", "class",
                "feature_class"
            ],
            required=False
        )

        owner = get_value(
            row,
            ["site_id", "siteid"],
            required=False
        )

        features.append({
            "id": str(fid or f"F{index:03d}"),
            "type": str(feature_type or "other"),
            "site_id": str(owner or "").strip().upper(),
            "lon": float(get_value(
                row, ["lon", "longitude"]
            )),
            "lat": float(get_value(
                row, ["lat", "latitude"]
            ))
        })

    return sites, features


def find_image(site_id, folder, suffix):

    matches = sorted(
        folder.glob(f"{site_id}_*.{suffix}")
    )

    if len(matches) != 1:
        raise ValueError(
            f"{site_id}: expected one {suffix}, "
            f"found {len(matches)}"
        )

    return matches[0]


def feature_color(feature_type):

    key = feature_type.strip().lower()

    return COLORS.get(key, OTHER_COLOR)


def star_points(x, y, outer=10, inner=4):

    import math

    points = []

    for i in range(10):

        angle = math.radians(-90 + i * 36)

        radius = outer if i % 2 == 0 else inner

        points.append((
            x + radius * math.cos(angle),
            y + radius * math.sin(angle)
        ))

    return points


def draw_label(draw, x, y, text, color):

    text_font = font(14)

    bbox = draw.textbbox(
        (0, 0), text, font=text_font
    )

    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]

    draw.rectangle(
        [
            x + 11, y - 11,
            x + width + 19, y + height + 1
        ],
        fill=(15, 18, 22, 220)
    )

    draw.text(
        (x + 15, y - 10),
        text,
        fill=color,
        font=text_font
    )


def project_to_pixel(src, lon, lat):

    from pyproj import Transformer

    transformer = Transformer.from_crs(
        "EPSG:4326",
        src.crs,
        always_xy=True
    )

    x, y = transformer.transform(lon, lat)

    row, col = rowcol(
        src.transform, x, y
    )

    return int(col), int(row)


def inside(px, py, width, height):

    return (
        0 <= px < width
        and 0 <= py < height
    )


# ========================================
# DRAW ANNOTATED IMAGE
# ========================================

def annotate_site(site_id, sites, features):

    tif = find_image(site_id, TIF_DIR, "tif")
    png = find_image(site_id, PNG_DIR, "png")

    with rasterio.open(tif) as src:

        image = PILImage.open(png).convert("RGB")
        image.load()

        image = image.convert("RGBA")

        overlay = PILImage.new(
            "RGBA", image.size,
            (0, 0, 0, 0)
        )

        draw = ImageDraw.Draw(overlay)

        width, height = image.size

        # Draw all features geographically inside crop.
        visible_features = []

        for feature in features:

            px, py = project_to_pixel(
                src,
                feature["lon"],
                feature["lat"]
            )

            if not inside(px, py, width, height):
                continue

            color = feature_color(
                feature["type"]
            )

            radius = 7

            draw.ellipse(
                (
                    px - radius, py - radius,
                    px + radius, py + radius
                ),
                fill=color,
                outline="black",
                width=2
            )

            draw_label(
                draw, px, py,
                feature["id"],
                "white"
            )

            visible_features.append({
                **feature,
                "pixel_x": px,
                "pixel_y": py
            })

        # Draw site centers last, so stars remain visible.
        visible_sites = []

        for other_id, site in sites.items():

            if (
                not SHOW_NEIGHBORING_SITES
                and other_id != site_id
            ):
                continue

            px, py = project_to_pixel(
                src, site["lon"], site["lat"]
            )

            if not inside(px, py, width, height):
                continue

            draw.polygon(
                star_points(px, py),
                fill=SITE_COLOR
            )

            draw.line(
                star_points(px, py)
                + [star_points(px, py)[0]],
                fill="black",
                width=2
            )

            draw_label(
                draw, px, py,
                other_id,
                "white"
            )

            visible_sites.append(other_id)

        image = PILImage.alpha_composite(
            image, overlay
        ).convert("RGB")

        # Legend
        legend_items = [
            ("Site center", SITE_COLOR)
        ]

        types_present = sorted({
            f["type"] for f in visible_features
        })

        for feature_type in types_present:
            legend_items.append((
                feature_type,
                feature_color(feature_type)
            ))

        legend_height = 18 + 26 * len(legend_items)

        legend_width = 240

        legend_x = width - legend_width - 15
        legend_y = 15

        legend = ImageDraw.Draw(image)

        legend.rectangle(
            (
                legend_x, legend_y,
                legend_x + legend_width,
                legend_y + legend_height
            ),
            fill="#F8F8F8",
            outline="#333333",
            width=2
        )

        for i, (label, color) in enumerate(legend_items):

            y = legend_y + 14 + i * 26

            legend.ellipse(
                (
                    legend_x + 12, y,
                    legend_x + 26, y + 14
                ),
                fill=color,
                outline="black",
                width=2
            )

            legend.text(
                (legend_x + 37, y - 2),
                label,
                fill="black",
                font=font(14)
            )

    OUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    THUMB_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    output = OUT_DIR / (
        f"{site_id}_10km_annotated.png"
    )

    thumbnail = THUMB_DIR / (
        f"{site_id}_thumbnail.png"
    )

    # Save a compact annotated image.
    image.save(
        output,
        format="PNG",
        optimize=True
    )

    thumb = image.copy()
    thumb.thumbnail((260, 260))

    thumb.save(
        thumbnail,
        format="PNG",
        optimize=True
    )

    return {
        "output": output,
        "thumbnail": thumbnail,
        "visible_features": visible_features,
        "visible_sites": visible_sites
    }


# ========================================
# UPDATE EXCEL
# ========================================

def build_excel(results):

    if not VALIDATION.exists():
        raise FileNotFoundError(
            f"Validation Excel missing: {VALIDATION}"
        )

    wb = load_workbook(VALIDATION)

    ws = wb["validation"]

    header_map = {
        str(cell.value).strip(): cell.column
        for cell in ws[1]
        if cell.value is not None
    }

    site_col = header_map["site_id"]

    first_col = ws.max_column + 1

    col_count = first_col
    col_link = first_col + 1
    col_preview = first_col + 2

    headers = {
        col_count: "Feature count",
        col_link: "Annotated image",
        col_preview: "Preview"
    }

    for col, title in headers.items():

        cell = ws.cell(1, col, title)

        cell.font = Font(
            bold=True,
            color="FFFFFF"
        )

        cell.fill = PatternFill(
            "solid",
            fgColor="17365D"
        )

        cell.alignment = Alignment(
            horizontal="center"
        )

    ws.column_dimensions[
        get_column_letter(col_count)
    ].width = 17

    ws.column_dimensions[
        get_column_letter(col_link)
    ].width = 29

    ws.column_dimensions[
        get_column_letter(col_preview)
    ].width = 39

    # Create detailed feature-point sheet.
    if "feature_points" in wb:
        del wb["feature_points"]

    detail = wb.create_sheet("feature_points")

    detail.append([
        "image_site_id",
        "feature_id",
        "feature_type",
        "database_site_id",
        "longitude",
        "latitude",
        "pixel_x",
        "pixel_y"
    ])

    for row_number in range(2, ws.max_row + 1):

        site_id = str(
            ws.cell(row_number, site_col).value
            or ""
        ).strip().upper()

        if site_id not in results:
            continue

        result = results[site_id]

        ws.cell(
            row_number,
            col_count,
            len(result["visible_features"])
        )

        link_cell = ws.cell(
            row_number,
            col_link,
            "Open annotated PNG"
        )

        link_cell.hyperlink = (
            result["output"].resolve().as_uri()
        )

        link_cell.style = "Hyperlink"

        excel_image = ExcelImage(
            str(result["thumbnail"])
        )

        excel_image.width = 220
        excel_image.height = 220

        anchor = (
            f"{get_column_letter(col_preview)}"
            f"{row_number}"
        )

        ws.add_image(
            excel_image,
            anchor
        )

        ws.row_dimensions[
            row_number
        ].height = 170

        for f in result["visible_features"]:

            detail.append([
                site_id,
                f["id"],
                f["type"],
                f["site_id"],
                f["lon"],
                f["lat"],
                f["pixel_x"],
                f["pixel_y"]
            ])

    detail.freeze_panes = "A2"
    detail.auto_filter.ref = detail.dimensions

    for cell in detail[1]:

        cell.font = Font(
            bold=True,
            color="FFFFFF"
        )

        cell.fill = PatternFill(
            "solid",
            fgColor="17365D"
        )

    for col in range(1, 9):
        detail.column_dimensions[
            get_column_letter(col)
        ].width = 22

    wb.save(OUTPUT_EXCEL)


# ========================================
# MAIN
# ========================================

def main():

    print("=" * 55)
    print("KARLAG S60 - FEATURE ANNOTATION")
    print("=" * 55)

    sites, features = load_database()

    print("Sites:", len(sites))
    print("Features:", len(features))

    results = {}

    for index, site_id in enumerate(
        sorted(sites), 1
    ):

        free_mb = (
            shutil.disk_usage(BASE).free
            / 1024**2
        )

        if free_mb < MIN_FREE_MB:
            print(
                "STOP: low disk space:",
                round(free_mb, 1), "MB"
            )
            break

        print(
            f"[{index}/{len(sites)}] "
            f"Annotating {site_id}..."
        )

        result = annotate_site(
            site_id, sites, features
        )

        results[site_id] = result

        print(
            "  Visible features:",
            len(result["visible_features"])
        )

    print("\nCreating Excel...")

    build_excel(results)

    print("\n" + "=" * 55)
    print("ANNOTATION COMPLETE")
    print("=" * 55)

    print("Images created:", len(results))
    print("Excel:", OUTPUT_EXCEL)
    print("Images folder:", OUT_DIR)


if __name__ == "__main__":
    main()
