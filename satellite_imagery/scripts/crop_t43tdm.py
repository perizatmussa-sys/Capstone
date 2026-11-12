
from pathlib import Path

import numpy as np
import rasterio

from rasterio.windows import from_bounds
from rasterio.windows import Window
from rasterio.windows import transform as window_transform

from pyproj import Transformer
from PIL import Image
from openpyxl import load_workbook


PROJECT = Path(r"D:\Karlag_SI_ML")

SOURCE = (
    PROJECT / "satellite_sources"
    / "single_jp2"
    / "T43TDM_20261029T055941_TCI_10m.jp2"
)

DATABASE = PROJECT / "karlag_database.xlsx"

OUTPUT = PROJECT / "dataset" / "sites_S60"

TIF_DIR = OUTPUT / "geotiff"
PNG_DIR = OUTPUT / "previews"

SITE_ID = "S001"
SIZE_METERS = 10000
EXPECTED_PIXELS = 1000


def get_site_coordinates():

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
            for value in next(rows)
        ]

        for values in rows:
            row = dict(zip(headers, values))

            if str(row.get("site_id")).strip() == SITE_ID:
                return (
                    float(row["lon"]),
                    float(row["lat"])
                )

    finally:
        wb.close()

    raise ValueError(
        f"Site {SITE_ID} not found"
    )


def main():

    if not SOURCE.exists():
        raise FileNotFoundError(
            f"JP2 file not found:\n{SOURCE}"
        )

    lon, lat = get_site_coordinates()

    print("Site:", SITE_ID)
    print("Longitude:", lon)
    print("Latitude:", lat)

    TIF_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    PNG_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    with rasterio.open(SOURCE) as src:

        print("\nSource CRS:", src.crs)
        print("Source size:", src.width, src.height)

        transformer = Transformer.from_crs(
            "EPSG:4326",
            src.crs,
            always_xy=True
        )

        x, y = transformer.transform(lon, lat)

        half = SIZE_METERS / 2

        left = x - half
        bottom = y - half
        right = x + half
        top = y + half

        bounds = src.bounds

        # Do not create incomplete images.
        if not (
            left >= bounds.left
            and right <= bounds.right
            and bottom >= bounds.bottom
            and top <= bounds.top
        ):
            raise ValueError(
                "The 10 km square is not fully "
                "inside this satellite tile."
            )

        # Create a pixel-aligned 1000x1000
        # window centred near the site.
        raw_window = from_bounds(
            left,
            bottom,
            right,
            top,
            transform=src.transform
        )

        col = round(
            raw_window.col_off
        )

        row = round(
            raw_window.row_off
        )

        window = Window(
            col_off=col,
            row_off=row,
            width=EXPECTED_PIXELS,
            height=EXPECTED_PIXELS
        )

        if (
            col < 0
            or row < 0
            or col + EXPECTED_PIXELS > src.width
            or row + EXPECTED_PIXELS > src.height
        ):
            raise ValueError(
                "Crop exceeds raster bounds."
            )

        rgb = src.read(
            indexes=[1, 2, 3],
            window=window
        )

        mask = src.dataset_mask(
            window=window
        )

        valid_percent = (
            np.mean(mask > 0) * 100
        )

        if valid_percent < 99.9:
            raise ValueError(
                "Insufficient valid pixels: "
                f"{valid_percent:.2f}%"
            )

        transform = window_transform(
            window,
            src.transform
        )

        image_name = (
            f"{SITE_ID}_20261029_RGB_10km"
        )

        tif_path = (
            TIF_DIR / f"{image_name}.tif"
        )

        png_path = (
            PNG_DIR / f"{image_name}.png"
        )

        # Protect previously created files.
        if tif_path.exists() or png_path.exists():
            raise FileExistsError(
                "Output already exists. "
                "No files were overwritten."
            )

        with rasterio.open(
            tif_path,
            "w",
            driver="GTiff",
            width=EXPECTED_PIXELS,
            height=EXPECTED_PIXELS,
            count=3,
            dtype="uint8",
            crs=src.crs,
            transform=transform,
            compress="deflate"
        ) as dst:
            dst.write(rgb)

        preview = np.moveaxis(
            rgb,
            0,
            -1
        )

        Image.fromarray(
            preview
        ).save(png_path)

        print("\nSUCCESS!")
        print("GeoTIFF:", tif_path)
        print("PNG:", png_path)
        print(
            "Valid pixels:",
            f"{valid_percent:.2f}%"
        )
        print(
            "Output size:",
            EXPECTED_PIXELS,
            "x",
            EXPECTED_PIXELS
        )


if __name__ == "__main__":
    main()
