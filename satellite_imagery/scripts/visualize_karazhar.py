
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
import matplotlib.pyplot as plt

from matplotlib.lines import Line2D


# ==========================================
# 1. FILE PATHS
# ==========================================

PROJECT = Path(r"D:\Karlag_SI_ML")

IMAGE = PROJECT / "S029_Karazhar_10km_RGB.tif"

MATCHES = (
    PROJECT
    / "dataset"
    / "metadata"
    / "Karazhar_10km_Spatial_Matches.xlsx"
)

OUTPUT = (
    PROJECT
    / "dataset"
    / "images"
    / "previews"
    / "S029_Karazhar_10km_annotated.png"
)


# ==========================================
# 2. CHECK INPUT FILES
# ==========================================

print("STEP 1: Checking files...")

if not IMAGE.is_file():
    raise FileNotFoundError(
        f"Image not found: {IMAGE}"
    )

if not MATCHES.is_file():
    raise FileNotFoundError(
        f"Excel not found: {MATCHES}"
    )


# ==========================================
# 3. READ MATCHED FEATURES AND SITES
# ==========================================

print("\nSTEP 2: Reading Excel...")

features = pd.read_excel(
    MATCHES,
    sheet_name="S029_inside_image"
)

sites = pd.read_excel(
    MATCHES,
    sheet_name="matched_sites"
)

print("Features:", len(features))
print("Sites:", len(sites))


# ==========================================
# 4. OPEN SATELLITE IMAGE
# ==========================================

print("\nSTEP 3: Reading satellite image...")

with rasterio.open(IMAGE) as src:

    if src.count < 3:
        raise ValueError(
            "Expected at least 3 RGB bands."
        )

    rgb = src.read([1, 2, 3])

    width = src.width
    height = src.height

print("Image size:", width, "x", height)

# Convert from (bands, height, width)
# to (height, width, bands).
rgb = np.moveaxis(rgb, 0, -1)

# Normalize only if needed.
if rgb.dtype != np.uint8:
    rgb = rgb.astype(np.float32)

    low = np.nanpercentile(rgb, 2)
    high = np.nanpercentile(rgb, 98)

    if high > low:
        rgb = np.clip(
            (rgb - low) / (high - low),
            0,
            1
        )
    else:
        rgb = np.zeros_like(rgb)


# ==========================================
# 5. PREPARE VISUALIZATION
# ==========================================

print("\nSTEP 4: Creating visualization...")

fig, ax = plt.subplots(
    figsize=(12, 12)
)

ax.imshow(
    rgb,
    origin="upper"
)

# Colors are for visual distinction only.
class_colors = {
    "mudbrick_building": "#FF6B35",
    "building_other": "#FFD166",
    "building_traces": "#EF476F",
    "annular_pit": "#06D6A0",
    "later_overbuild": "#B388EB"
}

default_color = "#FFFFFF"

legend_classes = set()


# ==========================================
# 6. DRAW ARCHAEOLOGICAL FEATURES
# ==========================================

for _, feature in features.iterrows():

    x = float(feature["pixel_x"])
    y = float(feature["pixel_y"])

    feature_id = str(feature["feature_id"])
    ml_class = str(feature["ml_class"])

    color = class_colors.get(
        ml_class,
        default_color
    )

    ax.scatter(
        x,
        y,
        s=90,
        c=color,
        edgecolors="black",
        linewidths=1,
        marker="o",
        zorder=5
    )

    ax.annotate(
        feature_id,
        (x, y),
        xytext=(7, -7),
        textcoords="offset points",
        color="white",
        fontsize=8,
        fontweight="bold",
        bbox=dict(
            facecolor="black",
            alpha=0.65,
            edgecolor="none",
            pad=2
        ),
        zorder=6
    )

    legend_classes.add(ml_class)


# ==========================================
# 7. DRAW SITE CENTERS
# ==========================================

for _, site in sites.iterrows():

    x = float(site["pixel_x"])
    y = float(site["pixel_y"])

    site_id = str(site["site_id"])

    ax.scatter(
        x,
        y,
        s=170,
        c="#00BFFF",
        edgecolors="black",
        linewidths=1.5,
        marker="*",
        zorder=7
    )

    ax.annotate(
        site_id,
        (x, y),
        xytext=(9, 9),
        textcoords="offset points",
        color="white",
        fontsize=10,
        fontweight="bold",
        bbox=dict(
            facecolor="#006080",
            alpha=0.85,
            edgecolor="none",
            pad=3
        ),
        zorder=8
    )


# ==========================================
# 8. LEGEND AND LABELS
# ==========================================

legend_items = [
    Line2D(
        [0], [0],
        marker="*",
        color="none",
        markerfacecolor="#00BFFF",
        markeredgecolor="black",
        markersize=13,
        label="Site center"
    )
]

for ml_class in sorted(legend_classes):

    color = class_colors.get(
        ml_class,
        default_color
    )

    legend_items.append(
        Line2D(
            [0], [0],
            marker="o",
            color="none",
            markerfacecolor=color,
            markeredgecolor="black",
            markersize=9,
            label=ml_class
        )
    )

ax.legend(
    handles=legend_items,
    loc="upper right",
    fontsize=9,
    framealpha=0.9
)

ax.set_title(
    "Karazhar S029 — Archaeological Feature Locations\n"
    "Sentinel-2C | 2026-11-04 | 10 m/pixel",
    fontsize=14,
    fontweight="bold",
    pad=15
)

ax.set_xlabel("Pixel X")
ax.set_ylabel("Pixel Y")

ax.set_xlim(0, width)
ax.set_ylim(height, 0)

ax.grid(
    alpha=0.15,
    linestyle="--"
)

fig.text(
    0.5,
    0.02,
    "Points show database coordinates, "
    "NOT confirmed visible archaeological objects.",
    ha="center",
    fontsize=10
)


# ==========================================
# 9. SAVE PNG
# ==========================================

OUTPUT.parent.mkdir(
    parents=True,
    exist_ok=True
)

plt.savefig(
    OUTPUT,
    dpi=180,
    bbox_inches="tight"
)

plt.close(fig)

print("\n====================================")
print("VISUALIZATION COMPLETE!")
print("====================================")
print("Features drawn:", len(features))
print("Site centers drawn:", len(sites))
print("Saved:", OUTPUT)
