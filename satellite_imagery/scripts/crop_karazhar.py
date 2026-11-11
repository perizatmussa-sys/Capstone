
from pathlib import Path

import rasterio
from rasterio.windows import from_bounds
from rasterio.windows import transform as window_transform
from rasterio.warp import transform as transform_coords


# ======================================
# 1. INPUT SATELLITE FILE
# ======================================

input_folder = Path(
    r"D:\Загрузка\S2C_MSIL2A_20261104T062021_N0513_R034_T43UCR_20261104T094421.SAFE\S2C_MSIL2A_20261104T062021_N0513_R034_T43UCR_20261104T094421.SAFE\GRANULE\L2A_T43UCR_A011301_20261104T062024\IMG_DATA\R10m"
)

input_file = input_folder / (
    "T43UCR_20261104T062021_TCI_10m.jp2"
)


# ======================================
# 2. KARAZHAR SETTINGS
# ======================================

latitude = 49.861306
longitude = 72.583222

# 10 km x 10 km
size_meters = 10000

output_file = Path(
    r"D:\Karlag_SI_ML\S029_Karazhar_10km_RGB.tif"
)


# ======================================
# 3. CHECK INPUT
# ======================================

print("STEP 1: Checking input...")

if not input_file.is_file():
    raise FileNotFoundError(
        f"Satellite image not found: {input_file}"
    )


# ======================================
# 4. OPEN AND CROP
# ======================================

with rasterio.open(input_file) as src:

    print("\nSTEP 2: Satellite opened")
    print("CRS:", src.crs)
    print("Resolution:", src.res)
    print("Bands:", src.count)

    if src.crs is None:
        raise ValueError("Input image has no CRS")

    if src.crs.to_epsg() != 32643:
        raise ValueError(
            f"Unexpected CRS: {src.crs}"
        )

    # Convert GPS coordinates to UTM
    xs, ys = transform_coords(
        "EPSG:4326",
        src.crs,
        [longitude],
        [latitude]
    )

    x = xs[0]
    y = ys[0]

    print("\nSTEP 3: Center coordinates")
    print("X:", x)
    print("Y:", y)

    # Calculate 10 km square
    half = size_meters / 2

    left = x - half
    right = x + half
    bottom = y - half
    top = y + half

    # Check entire crop is inside source image
    bounds = src.bounds

    fits = (
        left >= bounds.left
        and right <= bounds.right
        and bottom >= bounds.bottom
        and top <= bounds.top
    )

    if not fits:
        raise ValueError(
            "The 10 km square does not fit "
            "inside the source satellite image."
        )

    print("\nSTEP 4: Crop fits inside image")

    # Calculate pixel window
    window = from_bounds(
        left,
        bottom,
        right,
        top,
        transform=src.transform
    )

    # Align to pixel boundaries
    window = window.round_offsets().round_lengths()

    print("Window:", window)

    # Read cropped pixels
    print("\nSTEP 5: Reading pixels...")

    data = src.read(window=window)

    if data.size == 0:
        raise ValueError("Crop is empty")

    print("Image shape:", data.shape)

    # Prepare output metadata
    profile = src.profile.copy()

    profile.update(
        driver="GTiff",
        height=data.shape[1],
        width=data.shape[2],
        transform=window_transform(
            window,
            src.transform
        ),
        compress="deflate"
    )

    # Save new GeoTIFF
    output_file.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    print("\nSTEP 6: Saving GeoTIFF...")

    with rasterio.open(
        output_file,
        "w",
        **profile
    ) as dst:
        dst.write(data)

    print("File saved:", output_file)


# ======================================
# 5. VERIFY RESULT
# ======================================

print("\nSTEP 7: Verifying output...")

if not output_file.is_file():
    raise FileNotFoundError(
        f"Output was not created: {output_file}"
    )

with rasterio.open(output_file) as result:

    print("Width:", result.width)
    print("Height:", result.height)
    print("CRS:", result.crs)
    print("Resolution:", result.res)
    print("Bounds:", result.bounds)


print("\n=================================")
print("SUCCESS! 10 KM IMAGE CREATED")
print("=================================")
print("Site: S029 Karazhar")
print("Date: 2026-11-04")
print("Saved:", output_file)
