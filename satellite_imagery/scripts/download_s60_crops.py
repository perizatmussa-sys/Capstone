
from pathlib import Path
from datetime import datetime, timedelta, timezone
from getpass import getpass
from io import BytesIO
import json
import shutil
import time

import numpy as np
import requests
import rasterio

from rasterio.io import MemoryFile
from rasterio.crs import CRS
from rasterio.transform import from_bounds
from pyproj import Transformer
from PIL import Image
from openpyxl import load_workbook, Workbook


# ==========================================
# 1. SETTINGS
# ==========================================

PROJECT = Path(r"D:\Karlag_SI_ML")

META = (
    PROJECT / "dataset"
    / "sites_S60" / "metadata"
)

MISSING_PLAN = (
    META / "S60_Missing_Tiles_Plan.xlsx"
)

VERIFIED_PLAN = (
    META / "S60_Verified_Download_Plan.xlsx"
)

OUTPUT = (
    PROJECT / "dataset" / "sites_S60"
)

TIF_DIR = OUTPUT / "geotiff"
PNG_DIR = OUTPUT / "previews"

RESULT_FILE = (
    META / "S60_API_Download_Results.xlsx"
)

# IMPORTANT:
# 1 = test one site
# None = process all 44 sites
MAX_SITES = 1

SIZE_METERS = 10000
PIXELS = 1000

MIN_FREE_MB = 300

TOKEN_URL = (
    "https://identity.dataspace.copernicus.eu"
    "/auth/realms/CDSE/protocol/openid-connect/token"
)

PROCESS_URL = (
    "https://sh.dataspace.copernicus.eu"
    "/process/v1"
)


# ==========================================
# 2. RGB EVALSCRIPT
# ==========================================

# True colour bands:
# B04 = Red
# B03 = Green
# B02 = Blue
#
# dataMask marks valid pixels.
#
# The reflectance multiplier is used only
# for RGB visualization, not scientific
# reflectance analysis.

EVALSCRIPT = """
//VERSION=3

function setup() {
    return {
        input: [{
            bands: [
                "B04",
                "B03",
                "B02",
                "dataMask"
            ]
        }],
        output: {
            bands: 4,
            sampleType: "UINT8"
        }
    };
}

function evaluatePixel(sample) {
    let factor = 2.5;

    return [
        Math.min(255, Math.max(0,
            sample.B04 * factor * 255)),
        Math.min(255, Math.max(0,
            sample.B03 * factor * 255)),
        Math.min(255, Math.max(0,
            sample.B02 * factor * 255)),
        sample.dataMask * 255
    ];
}
"""


# ==========================================
# 3. EXCEL READER
# ==========================================

def read_excel(path, sheet_name):

    if not path.is_file():
        raise FileNotFoundError(path)

    wb = load_workbook(
        path,
        read_only=True,
        data_only=True
    )

    try:
        ws = wb[sheet_name]
        rows = ws.iter_rows(
            values_only=True
        )

        headers = list(next(rows))

        records = []

        for values in rows:
            if not any(
                v is not None for v in values
            ):
                continue

            records.append(
                dict(zip(headers, values))
            )

        return records

    finally:
        wb.close()


# ==========================================
# 4. SITE / PRODUCT MAPPING
# ==========================================

def load_tasks():

    sites = read_excel(
        MISSING_PLAN,
        "missing_sites"
    )

    products = read_excel(
        VERIFIED_PLAN,
        "selected_products"
    )

    site_lookup = {
        str(s["site_id"]).strip(): s
        for s in sites
    }

    tasks = []
    assigned = set()

    for product in products:

        site_ids = str(
            product.get("site_ids") or ""
        ).split(",")

        for raw_id in site_ids:

            site_id = raw_id.strip()

            if not site_id:
                continue

            if site_id in assigned:
                raise ValueError(
                    f"Duplicate site: {site_id}"
                )

            if site_id not in site_lookup:
                raise ValueError(
                    f"Unknown site: {site_id}"
                )

            site = site_lookup[site_id]

            tasks.append({
                "site_id": site_id,
                "lon": float(site["lon"]),
                "lat": float(site["lat"]),
                "product_name": str(
                    product["product_name"]
                ),
                "product_id": str(
                    product["product_id"]
                ),
                "tile_id": str(
                    product["tile_id"]
                ),
                "date": str(
                    product["acquisition_date"]
                )[:10]
            })

            assigned.add(site_id)

    expected = set(site_lookup)

    missing = expected - assigned

    if missing:
        raise ValueError(
            "Sites without selected products: "
            + ", ".join(sorted(missing))
        )

    return sorted(
        tasks,
        key=lambda t: t["site_id"]
    )


# ==========================================
# 5. AUTHENTICATION
# ==========================================

def get_token(client_id, client_secret):

    response = requests.post(
        TOKEN_URL,
        data={
            "grant_type":
                "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret
        },
        timeout=45
    )

    response.raise_for_status()

    data = response.json()

    return (
        data["access_token"],
        time.monotonic()
        + int(data.get("expires_in", 300))
        - 60
    )


# ==========================================
# 6. CREATE GEOGRAPHIC BOUNDS
# ==========================================

def make_bounds(lon, lat):

    zone = int(
        (lon + 180) / 6
    ) + 1

    zone = max(
        1, min(60, zone)
    )

    epsg = (
        32600 + zone
        if lat >= 0
        else 32700 + zone
    )

    transformer = Transformer.from_crs(
        "EPSG:4326",
        f"EPSG:{epsg}",
        always_xy=True
    )

    x, y = transformer.transform(
        lon, lat
    )

    half = SIZE_METERS / 2

    bbox = [
        x - half,
        y - half,
        x + half,
        y + half
    ]

    return bbox, epsg


# ==========================================
# 7. BUILD PROCESS REQUEST
# ==========================================

def make_request(task, bbox, epsg):

    date = datetime.strptime(
        task["date"],
        "%Y-%m-%d"
    )

    start = date.strftime(
        "%Y-%m-%dT00:00:00Z"
    )

    end = (
        date + timedelta(days=1)
        - timedelta(seconds=1)
    ).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    return {
        "input": {
            "bounds": {
                "bbox": bbox,
                "properties": {
                    "crs": (
                        "http://www.opengis.net"
                        f"/def/crs/EPSG/0/{epsg}"
                    )
                }
            },
            "data": [{
                "type": "sentinel-2-l2a",
                "dataFilter": {
                    "timeRange": {
                        "from": start,
                        "to": end
                    },
                    "mosaickingOrder":
                        "leastCC"
                }
            }]
        },
        "output": {
            "width": PIXELS,
            "height": PIXELS,
            "responses": [{
                "identifier": "default",
                "format": {
                    "type": "image/tiff"
                }
            }]
        },
        "evalscript": EVALSCRIPT
    }


# ==========================================
# 8. DOWNLOAD A SINGLE CROP
# ==========================================

def request_crop(
    session,
    payload,
    token
):

    for attempt in range(1, 4):

        try:
            response = session.post(
                PROCESS_URL,
                headers={
                    "Authorization":
                        f"Bearer {token}",
                    "Accept": "image/tiff"
                },
                json=payload,
                timeout=(30, 180)
            )

            if response.status_code != 200:

                detail = response.text[:600]

                # Authentication / quota / request
                # errors should be inspected,
                # not retried repeatedly.
                if response.status_code in (
                    400, 401, 402, 403, 429
                ):
                    raise RuntimeError(
                        f"API HTTP "
                        f"{response.status_code}: "
                        f"{detail}"
                    )

                response.raise_for_status()

            return response.content

        except requests.RequestException:

            if attempt == 3:
                raise

            time.sleep(attempt * 3)

    raise RuntimeError(
        "Processing request failed"
    )


# ==========================================
# 9. SAVE GEOTIFF AND PNG
# ==========================================

def save_crop(
    content,
    tif_path,
    png_path,
    bbox,
    epsg
):

    # Read TIFF returned by API.
    with MemoryFile(content) as mem:

        with mem.open() as src:

            data = src.read()

            if (
                data.shape[0] < 4
                or data.shape[1] != PIXELS
                or data.shape[2] != PIXELS
            ):
                raise ValueError(
                    "Unexpected TIFF dimensions: "
                    + str(data.shape)
                )

            rgb = data[:3]

            mask = data[3] > 0

            valid_percent = (
                float(np.mean(mask)) * 100
            )

            if valid_percent < 99.9:
                raise ValueError(
                    "Insufficient valid pixels: "
                    f"{valid_percent:.2f}%"
                )

            if not np.any(rgb):
                raise ValueError(
                    "RGB image appears empty"
                )

    # The requested area is defined in
    # projected UTM coordinates.
    transform = from_bounds(
        *bbox,
        PIXELS,
        PIXELS
    )

    # Write to temporary files first.
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
            height=PIXELS,
            width=PIXELS,
            count=3,
            dtype="uint8",
            crs=CRS.from_epsg(epsg),
            transform=transform,
            compress="deflate"
        ) as dst:

            dst.write(
                rgb.astype(np.uint8)
            )

        rgb_array = np.moveaxis(
            rgb,
            0,
            -1
        ).astype(np.uint8)

        Image.fromarray(
            rgb_array
        ).save(
            temp_png,
            format="PNG"
        )

        temp_tif.replace(tif_path)
        temp_png.replace(png_path)

    finally:
        temp_tif.unlink(
            missing_ok=True
        )
        temp_png.unlink(
            missing_ok=True
        )

    return valid_percent


# ==========================================
# 10. SAVE RESULTS EXCEL
# ==========================================

def save_results(records):

    wb = Workbook()

    ws = wb.active
    ws.title = "api_results"

    if records:

        headers = list(
            records[0].keys()
        )

        ws.append(headers)

        for record in records:
            ws.append([
                record.get(h)
                for h in headers
            ])

        ws.freeze_panes = "A2"
        ws.auto_filter.ref = (
            ws.dimensions
        )

        for col in ws.columns:
            letter = col[0].column_letter
            ws.column_dimensions[
                letter
            ].width = 25

    wb.save(RESULT_FILE)


# ==========================================
# 11. MAIN
# ==========================================

def main():

    print(
        "STEP 1: Loading S60 plans..."
    )

    tasks = load_tasks()

    print(
        "Total pending sites:",
        len(tasks)
    )

    if MAX_SITES is not None:
        tasks = tasks[:MAX_SITES]

    print(
        "Sites in this run:",
        len(tasks)
    )

    TIF_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    PNG_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    free_mb = (
        shutil.disk_usage(OUTPUT).free
        / (1024 ** 2)
    )

    print(
        f"Free disk: {free_mb:.0f} MB"
    )

    if free_mb < MIN_FREE_MB:
        raise RuntimeError(
            "Not enough free space."
        )

    print(
        "\nSTEP 2: Sentinel Hub OAuth..."
    )

    client_id = input(
        "OAuth Client ID: "
    ).strip()

    client_secret = getpass(
        "OAuth Client Secret: "
    )

    token, expires_at = get_token(
        client_id,
        client_secret
    )

    print(
        "Authentication successful."
    )

    session = requests.Session()

    results = []

    # Preserve previous run results.
    if RESULT_FILE.exists():
        results = read_excel(
            RESULT_FILE,
            "api_results"
        )

    previous = {
        str(r.get("site_id")): r
        for r in results
    }

    print(
        "\nSTEP 3: Downloading crops..."
    )

    for index, task in enumerate(
        tasks,
        start=1
    ):

        site_id = task["site_id"]

        image_id = (
            f"{site_id}_"
            f"{task['date'].replace('-', '')}"
            "_RGB_10km"
        )

        tif_path = (
            TIF_DIR / f"{image_id}.tif"
        )

        png_path = (
            PNG_DIR / f"{image_id}.png"
        )

        print(
            f"\n[{index}/{len(tasks)}] "
            f"{site_id}"
        )

        print(
            "Tile:", task["tile_id"]
        )

        print(
            "Date:", task["date"]
        )

        record = {
            "site_id": site_id,
            "image_id": image_id,
            "tile_id": task["tile_id"],
            "selected_product":
                task["product_name"],
            "requested_date": task["date"],
            "resolution_m": 10,
            "size_m": SIZE_METERS,
            "status": "",
            "valid_percent": None,
            "tif_path": str(tif_path),
            "png_path": str(png_path),
            "error": ""
        }

        try:

            if (
                tif_path.exists()
                and png_path.exists()
            ):
                record["status"] = (
                    "already_exists"
                )

                if site_id in previous:
                    old = previous[site_id]

                    record["valid_percent"] = (
                        old.get("valid_percent")
                    )

                print(
                    "SKIP: files already exist"
                )

            else:

                if (
                    shutil.disk_usage(
                        OUTPUT
                    ).free / (1024 ** 2)
                    < MIN_FREE_MB
                ):
                    raise RuntimeError(
                        "Low disk space"
                    )

                if (
                    time.monotonic()
                    >= expires_at
                ):
                    token, expires_at = (
                        get_token(
                            client_id,
                            client_secret
                        )
                    )

                bbox, epsg = make_bounds(
                    task["lon"],
                    task["lat"]
                )

                payload = make_request(
                    task,
                    bbox,
                    epsg
                )

                content = request_crop(
                    session,
                    payload,
                    token
                )

                valid = save_crop(
                    content,
                    tif_path,
                    png_path,
                    bbox,
                    epsg
                )

                record["status"] = (
                    "downloaded"
                )

                record["valid_percent"] = (
                    round(valid, 3)
                )

                print(
                    "SUCCESS:",
                    image_id
                )

                print(
                    "Valid pixels:",
                    f"{valid:.2f}%"
                )

        except Exception as error:

            record["status"] = "error"
            record["error"] = str(error)

            print(
                "ERROR:",
                error
            )

        previous[site_id] = record

        # Save progress after every site.
        save_results(
            list(previous.values())
        )

        if (
            record["status"] == "error"
            and any(
                code in record["error"]
                for code in [
                    "HTTP 401",
                    "HTTP 402",
                    "HTTP 403",
                    "HTTP 429"
                ]
            )
        ):
            print(
                "Stopping: authorization "
                "or quota issue."
            )
            break

        time.sleep(0.5)

    print(
        "\n================================"
    )

    print(
        "S60 CROP DOWNLOAD FINISHED"
    )

    print(
        "================================"
    )

    statuses = {}

    for item in previous.values():

        status = item["status"]

        statuses[status] = (
            statuses.get(status, 0) + 1
        )

    for status, count in statuses.items():
        print(
            f"{status}: {count}"
        )

    print(
        "\nResults:",
        RESULT_FILE
    )


if __name__ == "__main__":
    main()
