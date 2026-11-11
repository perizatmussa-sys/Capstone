
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict
import re
import time

import requests
from openpyxl import load_workbook, Workbook
from openpyxl.styles import Font, PatternFill, Alignment


# ==========================================
# 1. PROJECT SETTINGS
# ==========================================

PROJECT = Path(r"D:\Karlag_SI_ML")

INPUT_FILE = (
    PROJECT
    / "dataset"
    / "sites_S60"
    / "metadata"
    / "S60_Image_Manifest.xlsx"
)

OUTPUT_FILE = (
    PROJECT
    / "dataset"
    / "sites_S60"
    / "metadata"
    / "S60_Missing_Tiles_Plan.xlsx"
)

# Official Copernicus Data Space catalogue
API_URL = (
    "https://catalogue.dataspace.copernicus.eu"
    "/odata/v1/Products"
)

# Search close to the existing image date
DATE_START = "2026-10-20"
DATE_END = "2026-11-06"

TARGET_DATE = datetime(
    2026, 11, 4,
    tzinfo=timezone.utc
)

PREFERRED_CLOUD_PERCENT = 20.0

# Number of products checked per site
MAX_PRODUCTS = 100

REQUEST_TIMEOUT = 45

# Small pause between requests
REQUEST_DELAY = 0.3


# ==========================================
# 2. HELPERS
# ==========================================

def read_records(path, sheet_name):
    wb = load_workbook(
        path,
        read_only=True,
        data_only=True
    )

    try:
        ws = wb[sheet_name]

        rows = ws.iter_rows(values_only=True)

        headers = [
            str(value).strip()
            if value is not None else ""
            for value in next(rows)
        ]

        records = []

        for values in rows:
            record = dict(zip(headers, values))
            records.append(record)

        return records

    finally:
        wb.close()


def extract_tile(name):
    match = re.search(
        r"_T(\d{2}[A-Z]{3})_",
        name or ""
    )

    if match:
        return "T" + match.group(1)

    return None


def extract_cloud(product):
    attributes = product.get(
        "Attributes"
    ) or []

    for attribute in attributes:
        name = str(
            attribute.get("Name", "")
        ).lower()

        if name in (
            "cloudcover",
            "cloud_cover",
            "cloudcoverpercentage"
        ):
            value = attribute.get(
                "Value"
            )

            try:
                return float(value)
            except (ValueError, TypeError):
                pass

    return None


def parse_product_date(product):
    content_date = product.get(
        "ContentDate"
    ) or {}

    date_string = content_date.get(
        "Start"
    )

    if not date_string:
        return None

    try:
        return datetime.fromisoformat(
            date_string.replace(
                "Z", "+00:00"
            )
        )
    except ValueError:
        return None


def cloud_rank(cloud):
    if cloud is None:
        return 999.0
    return cloud


def product_rank(product):
    cloud = product["cloud_percent"]

    date = product["acquisition_datetime"]

    if date is None:
        days_difference = 9999
    else:
        days_difference = abs(
            (date - TARGET_DATE).total_seconds()
        ) / 86400

    preferred = (
        cloud is not None
        and cloud <= PREFERRED_CLOUD_PERCENT
    )

    return (
        0 if preferred else 1,
        cloud_rank(cloud),
        days_difference
    )


def search_products(session, lon, lat):
    """
    Search actual Sentinel-2 products
    intersecting the site centre.

    The result is a candidate list.
    Full 10 km coverage must be checked
    later against raster footprints.
    """

    point = (
        f"POINT ({lon:.8f} {lat:.8f})"
    )

    filters = [
        "Collection/Name eq 'SENTINEL-2'",
        "contains(Name,'MSIL2A')",
        (
            "ContentDate/Start ge "
            f"{DATE_START}T00:00:00.000Z"
        ),
        (
            "ContentDate/Start le "
            f"{DATE_END}T23:59:59.999Z"
        ),
        (
            "OData.CSC.Intersects("
            "area=geography'SRID=4326;"
            f"{point}'"
            ")"
        )
    ]

    params = {
        "$filter": " and ".join(filters),
        "$top": MAX_PRODUCTS,
        "$orderby": "ContentDate/Start desc",
        "$expand": "Attributes",
        "$select": (
            "Id,Name,ContentDate,"
            "Attributes,GeoFootprint"
        )
    }

    response = session.get(
        API_URL,
        params=params,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    data = response.json()

    products = []

    for item in data.get("value", []):

        name = item.get(
            "Name", ""
        )

        tile = extract_tile(name)

        if not tile:
            continue

        cloud = extract_cloud(item)

        products.append({
            "product_id": item.get("Id"),
            "product_name": name,
            "tile_id": tile,
            "cloud_percent": cloud,
            "acquisition_datetime":
                parse_product_date(item)
        })

    return products, bool(
        data.get("@odata.nextLink")
    )


def save_excel(sheets):
    wb = Workbook()
    wb.remove(wb.active)

    for sheet_name, records in sheets.items():

        ws = wb.create_sheet(
            sheet_name
        )

        if not records:
            ws.append(["No records"])
            continue

        headers = list(
            records[0].keys()
        )

        ws.append(headers)

        for record in records:
            ws.append([
                record.get(key)
                for key in headers
            ])

        ws.freeze_panes = "A2"

        if ws.max_row > 1:
            ws.auto_filter.ref = (
                ws.dimensions
            )

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

        for column in ws.columns:
            letter = column[0].column_letter

            ws.column_dimensions[
                letter
            ].width = 26

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    wb.save(OUTPUT_FILE)


# ==========================================
# 3. CHECK INPUT
# ==========================================

print("STEP 1: Checking manifest...")

if not INPUT_FILE.is_file():
    raise FileNotFoundError(
        f"Manifest not found:\n{INPUT_FILE}"
    )

if OUTPUT_FILE.exists():
    raise FileExistsError(
        "Output Excel already exists. "
        "Close it and move or rename it "
        "before running again:\n"
        f"{OUTPUT_FILE}"
    )

all_sites = read_records(
    INPUT_FILE,
    "all_sites"
)

missing_sites = [
    site for site in all_sites
    if str(
        site.get("processing_status", "")
    ).strip() == "needs_other_tile"
]

print("Total sites:", len(all_sites))
print(
    "Sites needing imagery:",
    len(missing_sites)
)


# ==========================================
# 4. QUERY COPERNICUS
# ==========================================

print("\nSTEP 2: Searching Copernicus...")

session = requests.Session()

session.headers.update({
    "Accept": "application/json",
    "User-Agent": "Karlag-Research-Pilot/1.0"
})

site_results = []
product_candidates = []
product_index = {}
errors = []

for index, site in enumerate(
    missing_sites,
    start=1
):

    site_id = str(
        site.get("site_id", "")
    ).strip()

    site_name = str(
        site.get("name") or ""
    ).strip()

    print(
        f"\n[{index}/{len(missing_sites)}] "
        f"{site_id} - {site_name}"
    )

    try:
        lon = float(
            site["lon"]
        )

        lat = float(
            site["lat"]
        )

        if not (
            -180 <= lon <= 180
            and -90 <= lat <= 90
        ):
            raise ValueError(
                "Coordinates outside valid range"
            )

        products, truncated = search_products(
            session, lon, lat
        )

        if not products:
            site_results.append({
                "site_id": site_id,
                "site_name": site_name,
                "lon": lon,
                "lat": lat,
                "search_status": "no_products_found",
                "candidate_tile": "",
                "candidate_product": "",
                "cloud_percent": None,
                "acquisition_date": "",
                "candidate_count": 0,
                "result_truncated": truncated,
                "full_10km_coverage_verified": False
            })

            print("No products found")
            continue

        products.sort(
            key=product_rank
        )

        best = products[0]

        date = best[
            "acquisition_datetime"
        ]

        date_text = (
            date.strftime("%Y-%m-%d")
            if date else ""
        )

        site_results.append({
            "site_id": site_id,
            "site_name": site_name,
            "lon": lon,
            "lat": lat,
            "search_status": "candidate_found",
            "candidate_tile": best["tile_id"],
            "candidate_product":
                best["product_name"],
            "cloud_percent":
                best["cloud_percent"],
            "acquisition_date": date_text,
            "candidate_count": len(products),
            "result_truncated": truncated,
            "full_10km_coverage_verified": False
        })

        print(
            "Candidate tile:",
            best["tile_id"]
        )

        print(
            "Cloud:",
            best["cloud_percent"]
        )

        # Save every candidate found for the site
        for product in products:

            acquisition = product[
                "acquisition_datetime"
            ]

            product_candidates.append({
                "site_id": site_id,
                "tile_id": product["tile_id"],
                "product_id": product["product_id"],
                "product_name": product["product_name"],
                "acquisition_date": (
                    acquisition.strftime("%Y-%m-%d")
                    if acquisition else ""
                ),
                "cloud_percent":
                    product["cloud_percent"]
            })

            key = product["product_id"]

            if key:
                if key not in product_index:
                    product_index[key] = {
                        "tile_id": product["tile_id"],
                        "product_id": key,
                        "product_name":
                            product["product_name"],
                        "cloud_percent":
                            product["cloud_percent"],
                        "site_ids": set()
                    }

                product_index[key][
                    "site_ids"
                ].add(site_id)

    except (
        requests.RequestException,
        ValueError,
        TypeError,
        KeyError
    ) as error:

        message = str(error)

        print("SEARCH ERROR:", message)

        site_results.append({
            "site_id": site_id,
            "site_name": site_name,
            "lon": site.get("lon"),
            "lat": site.get("lat"),
            "search_status": "search_error",
            "candidate_tile": "",
            "candidate_product": "",
            "cloud_percent": None,
            "acquisition_date": "",
            "candidate_count": 0,
            "result_truncated": False,
            "full_10km_coverage_verified": False
        })

        errors.append({
            "site_id": site_id,
            "error": message
        })

    time.sleep(REQUEST_DELAY)


# ==========================================
# 5. GROUP BY TILES
# ==========================================

print("\nSTEP 3: Grouping tile candidates...")

tile_groups = defaultdict(set)

for site in site_results:

    if site["search_status"] != "candidate_found":
        continue

    tile_groups[
        site["candidate_tile"]
    ].add(site["site_id"])

tile_summary = []

for tile_id, site_ids in sorted(
    tile_groups.items()
):

    tile_summary.append({
        "tile_id": tile_id,
        "candidate_site_count": len(site_ids),
        "site_ids": ", ".join(
            sorted(site_ids)
        ),
        "download_status": "not_downloaded",
        "coverage_status": "needs_verification"
    })


# ==========================================
# 6. GROUP BY ACTUAL PRODUCTS
# ==========================================

product_summary = []

for product in product_index.values():

    product_summary.append({
        "tile_id": product["tile_id"],
        "product_id": product["product_id"],
        "product_name": product["product_name"],
        "cloud_percent": product["cloud_percent"],
        "site_count": len(product["site_ids"]),
        "site_ids": ", ".join(
            sorted(product["site_ids"])
        )
    })

product_summary.sort(
    key=lambda item: (
        -item["site_count"],
        cloud_rank(item["cloud_percent"])
    )
)


# ==========================================
# 7. SAVE RESULTS
# ==========================================

print("\nSTEP 4: Saving Excel...")

save_excel({
    "missing_sites": site_results,
    "tile_groups": tile_summary,
    "product_groups": product_summary,
    "all_candidates": product_candidates,
    "errors": errors
})


# ==========================================
# 8. FINAL SUMMARY
# ==========================================

found = sum(
    item["search_status"] == "candidate_found"
    for item in site_results
)

not_found = sum(
    item["search_status"] == "no_products_found"
    for item in site_results
)

print("\n====================================")
print("MISSING TILE SEARCH COMPLETE")
print("====================================")

print("Sites searched:", len(missing_sites))
print("Sites with candidates:", found)
print("Sites without products:", not_found)
print("Search errors:", len(errors))

print(
    "Candidate tile groups:",
    len(tile_groups)
)

print(
    "Distinct products found:",
    len(product_index)
)

print("\nSaved Excel:")
print(OUTPUT_FILE)

print("\nIMPORTANT:")
print(
    "Candidate tiles cover site centres, "
    "but full 10 km crop coverage "
    "is not yet verified."
)
print(
    "Cloud cover is product-level metadata, "
    "not a measurement over each site."
)
