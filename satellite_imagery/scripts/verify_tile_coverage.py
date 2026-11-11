
from pathlib import Path
from datetime import datetime
from collections import defaultdict
import json
import time

import requests
from shapely.geometry import shape, box
from shapely.ops import transform as transform_geometry
from pyproj import CRS, Transformer
from openpyxl import load_workbook, Workbook
from openpyxl.styles import Font, PatternFill


# ==========================================
# 1. SETTINGS
# ==========================================

PROJECT = Path(r"D:\Karlag_SI_ML")

INPUT_FILE = (
    PROJECT / "dataset" / "sites_S60"
    / "metadata" / "S60_Missing_Tiles_Plan.xlsx"
)

OUTPUT_FILE = (
    PROJECT / "dataset" / "sites_S60"
    / "metadata" / "S60_Verified_Download_Plan.xlsx"
)

CACHE_FILE = (
    PROJECT / "dataset" / "sites_S60"
    / "metadata" / "product_footprints_cache.json"
)

API_URL = (
    "https://catalogue.dataspace.copernicus.eu"
    "/odata/v1/Products"
)

CROP_SIZE_METERS = 10000
TARGET_DATE = datetime(2026, 11, 4)
PREFERRED_CLOUD = 20.0
REQUEST_TIMEOUT = 45


# ==========================================
# 2. READ EXCEL
# ==========================================

def read_sheet(path, sheet_name):
    wb = load_workbook(
        path, read_only=True, data_only=True
    )

    try:
        ws = wb[sheet_name]
        rows = ws.iter_rows(values_only=True)
        headers = next(rows)

        return [
            dict(zip(headers, values))
            for values in rows
            if any(v is not None for v in values)
        ]
    finally:
        wb.close()


# ==========================================
# 3. CREATE SITE SQUARE
# ==========================================

def site_square(lon, lat):
    """
    Create a 10 x 10 km square in the
    site's local UTM coordinate system.
    Return it in WGS84 coordinates.
    """

    lon = float(lon)
    lat = float(lat)

    utm_zone = int((lon + 180) / 6) + 1
    utm_zone = max(1, min(60, utm_zone))

    epsg = (
        32600 + utm_zone
        if lat >= 0
        else 32700 + utm_zone
    )

    utm = CRS.from_epsg(epsg)
    wgs84 = CRS.from_epsg(4326)

    to_utm = Transformer.from_crs(
        wgs84, utm, always_xy=True
    ).transform

    to_wgs84 = Transformer.from_crs(
        utm, wgs84, always_xy=True
    ).transform

    x, y = to_utm(lon, lat)

    half = CROP_SIZE_METERS / 2

    square_utm = box(
        x - half,
        y - half,
        x + half,
        y + half
    )

    return transform_geometry(
        to_wgs84, square_utm
    )


# ==========================================
# 4. FETCH PRODUCT FOOTPRINT
# ==========================================

session = requests.Session()

session.headers.update({
    "Accept": "application/json",
    "User-Agent": "Karlag-Research/1.0"
})


def fetch_footprint(product_id):
    url = f"{API_URL}({product_id})"

    for attempt in range(3):
        try:
            response = session.get(
                url,
                params={
                    "$select": "Id,Name,GeoFootprint"
                },
                timeout=REQUEST_TIMEOUT
            )

            response.raise_for_status()
            data = response.json()

            footprint = data.get(
                "GeoFootprint"
            )

            if not footprint:
                return None, "missing_footprint"

            if isinstance(footprint, str):
                footprint = json.loads(footprint)

            return footprint, "ok"

        except (
            requests.RequestException,
            ValueError
        ) as error:

            if attempt == 2:
                return None, str(error)

            time.sleep(2 * (attempt + 1))


# ==========================================
# 5. LOAD INPUTS
# ==========================================

print("STEP 1: Loading download candidates...")

if not INPUT_FILE.is_file():
    raise FileNotFoundError(INPUT_FILE)

sites = read_sheet(
    INPUT_FILE, "missing_sites"
)

candidates = read_sheet(
    INPUT_FILE, "all_candidates"
)

site_lookup = {
    str(s["site_id"]): s
    for s in sites
}

products = {}

for item in candidates:
    product_id = item.get("product_id")

    if product_id:
        products[str(product_id)] = item

print("Sites:", len(sites))
print("Unique products:", len(products))


# ==========================================
# 6. LOAD FOOTPRINT CACHE
# ==========================================

if CACHE_FILE.is_file():
    with open(
        CACHE_FILE, "r", encoding="utf-8"
    ) as file:
        cache = json.load(file)
else:
    cache = {}

print("\nSTEP 2: Checking product footprints...")

for index, product_id in enumerate(
    products, start=1
):

    if (
        product_id in cache
        and cache[product_id].get("status") == "ok"
    ):
        print(
            f"[{index}/{len(products)}] Cached"
        )
        continue

    footprint, status = fetch_footprint(
        product_id
    )

    cache[product_id] = {
        "status": status,
        "footprint": footprint
    }

    print(
        f"[{index}/{len(products)}] {status}"
    )

    # Save progress after each request.
    CACHE_FILE.parent.mkdir(
        parents=True, exist_ok=True
    )

    with open(
        CACHE_FILE, "w", encoding="utf-8"
    ) as file:
        json.dump(cache, file)

    time.sleep(0.2)


# ==========================================
# 7. CHECK FULL COVERAGE
# ==========================================

print("\nSTEP 3: Verifying 10 km squares...")

site_polygons = {}

for site_id, site in site_lookup.items():
    site_polygons[site_id] = site_square(
        site["lon"], site["lat"]
    )

coverage_records = []

for item in candidates:

    site_id = str(item["site_id"])
    product_id = str(item["product_id"])

    cached = cache.get(product_id, {})

    footprint = cached.get("footprint")

    full_coverage = False
    coverage_percent = 0.0

    if footprint:
        try:
            polygon = shape(footprint)

            if not polygon.is_valid:
                polygon = polygon.buffer(0)

            square = site_polygons[site_id]

            # Evaluate intersection area in a
            # local projected CRS, not degrees.
            site = site_lookup[site_id]

            lon = float(site["lon"])
            lat = float(site["lat"])

            zone = max(
                1, min(60, int((lon + 180) / 6) + 1)
            )

            epsg = (
                32600 + zone
                if lat >= 0
                else 32700 + zone
            )

            projector = Transformer.from_crs(
                4326, epsg, always_xy=True
            ).transform

            projected_square = transform_geometry(
                projector, square
            )

            projected_footprint = transform_geometry(
                projector, polygon
            )

            intersection = (
                projected_square.intersection(
                    projected_footprint
                )
            )

            coverage_percent = (
                100 * intersection.area
                / projected_square.area
            )

            # A small geometric tolerance handles
            # floating-point boundary differences.
            full_coverage = (
                coverage_percent >= 99.999
            )

        except Exception as error:
            print(
                "Geometry error:",
                site_id, product_id, error
            )

    record = dict(item)

    record["coverage_percent"] = round(
        coverage_percent, 4
    )

    record["full_10km_coverage"] = (
        full_coverage
    )

    record["footprint_status"] = (
        cached.get("status", "not_checked")
    )

    coverage_records.append(record)


# ==========================================
# 8. SELECT A SMALL PRODUCT SET
# ==========================================

print("\nSTEP 4: Selecting products...")

# Products eligible for selection.
eligible = [
    r for r in coverage_records
    if r["full_10km_coverage"]
]

product_to_sites = defaultdict(set)
product_details = {}

for record in eligible:

    product_id = str(
        record["product_id"]
    )

    product_to_sites[product_id].add(
        str(record["site_id"])
    )

    product_details[product_id] = record


def cloud_value(record):
    value = record.get("cloud_percent")

    try:
        return float(value)
    except (TypeError, ValueError):
        return 999.0


def date_distance(record):
    try:
        date = datetime.strptime(
            str(record["acquisition_date"]),
            "%Y-%m-%d"
        )
        return abs((date - TARGET_DATE).days)
    except ValueError:
        return 9999


uncovered = set(site_lookup)
selected = []

while uncovered:

    options = []

    for product_id, covered_sites in (
        product_to_sites.items()
    ):

        new_sites = covered_sites & uncovered

        if not new_sites:
            continue

        details = product_details[product_id]

        cloud = cloud_value(details)

        options.append((
            -len(new_sites),
            0 if cloud <= PREFERRED_CLOUD else 1,
            cloud,
            date_distance(details),
            product_id,
            new_sites
        ))

    if not options:
        break

    options.sort()

    best = options[0]

    product_id = best[4]
    newly_covered = best[5]

    details = product_details[product_id]

    selected.append({
        "product_id": product_id,
        "tile_id": details["tile_id"],
        "product_name": details["product_name"],
        "acquisition_date":
            details["acquisition_date"],
        "cloud_percent":
            details["cloud_percent"],
        "site_count": len(newly_covered),
        "site_ids": ", ".join(
            sorted(newly_covered)
        ),
        "download_status": "not_downloaded"
    })

    uncovered -= newly_covered


# ==========================================
# 9. BUILD SITE REPORT
# ==========================================

site_report = []

for site_id, site in site_lookup.items():

    matching = [
        r for r in eligible
        if str(r["site_id"]) == site_id
    ]

    selected_product = ""

    for product in selected:
        ids = product["site_ids"].split(", ")

        if site_id in ids:
            selected_product = (
                product["product_name"]
            )
            break

    site_report.append({
        "site_id": site_id,
        "site_name": site.get("site_name"),
        "lon": site["lon"],
        "lat": site["lat"],
        "full_coverage_candidates":
            len(matching),
        "selected_product": selected_product,
        "status": (
            "ready_for_download"
            if selected_product
            else "needs_more_search"
        )
    })


# ==========================================
# 10. SAVE EXCEL
# ==========================================

def write_sheet(wb, name, records):

    ws = wb.create_sheet(name)

    if not records:
        ws.append(["No records"])
        return

    headers = list(records[0].keys())

    ws.append(headers)

    for record in records:
        ws.append([
            record.get(key)
            for key in headers
        ])

    ws.freeze_panes = "A2"

    if ws.max_row > 1:
        ws.auto_filter.ref = ws.dimensions

    for cell in ws[1]:
        cell.fill = PatternFill(
            fill_type="solid",
            fgColor="17365D"
        )
        cell.font = Font(
            bold=True,
            color="FFFFFF"
        )

    for column in ws.columns:
        letter = column[0].column_letter
        ws.column_dimensions[letter].width = 28


wb = Workbook()
wb.remove(wb.active)

write_sheet(
    wb, "selected_products", selected
)

write_sheet(
    wb, "site_coverage", site_report
)

write_sheet(
    wb, "all_coverage_checks",
    coverage_records
)

OUTPUT_FILE.parent.mkdir(
    parents=True, exist_ok=True
)

wb.save(OUTPUT_FILE)


# ==========================================
# 11. SUMMARY
# ==========================================

ready = sum(
    s["status"] == "ready_for_download"
    for s in site_report
)

print("\n====================================")
print("COVERAGE VERIFICATION COMPLETE")
print("====================================")

print("Sites checked:", len(site_lookup))
print("Sites fully covered:", ready)
print("Sites needing more search:", len(uncovered))
print("Selected products:", len(selected))

print("\nExcel saved:")
print(OUTPUT_FILE)

print("\nIMPORTANT:")
print(
    "Footprint coverage is preliminary. "
    "Final verification must use actual "
    "raster bounds and valid pixels."
)
