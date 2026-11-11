
from pathlib import Path
from datetime import datetime
from getpass import getpass
import shutil
import time
import zipfile

import requests
from openpyxl import load_workbook


# ==========================================
# 1. SETTINGS
# ==========================================

PROJECT = Path(r"D:\Karlag_SI_ML")

PLAN = (
    PROJECT / "dataset" / "sites_S60"
    / "metadata" / "S60_Verified_Download_Plan.xlsx"
)

DOWNLOAD_DIR = (
    PROJECT / "satellite_sources"
    / "sentinel2_downloads"
)

LOG_FILE = (
    DOWNLOAD_DIR / "download_log.csv"
)

TOKEN_URL = (
    "https://identity.dataspace.copernicus.eu"
    "/auth/realms/CDSE/protocol/openid-connect/token"
)

DOWNLOAD_BASE = (
    "https://download.dataspace.copernicus.eu"
    "/odata/v1/Products"
)

MIN_FREE_GB = 15
CHUNK_SIZE = 1024 * 1024
MAX_RETRIES = 4

DOWNLOAD_DIR.mkdir(
    parents=True,
    exist_ok=True
)


# ==========================================
# 2. LOAD SELECTED PRODUCTS
# ==========================================

def load_products():
    if not PLAN.exists():
        raise FileNotFoundError(PLAN)

    wb = load_workbook(
        PLAN,
        read_only=True,
        data_only=True
    )

    try:
        ws = wb["selected_products"]
        rows = ws.iter_rows(values_only=True)

        headers = list(next(rows))

        products = [
            dict(zip(headers, row))
            for row in rows
            if any(value is not None for value in row)
        ]

        required = [
            "product_id",
            "product_name",
            "tile_id"
        ]

        for column in required:
            if column not in headers:
                raise ValueError(
                    f"Missing column: {column}"
                )

        return products

    finally:
        wb.close()


# ==========================================
# 3. CHECK DISK SPACE
# ==========================================

def free_disk_gb():
    usage = shutil.disk_usage(
        DOWNLOAD_DIR
    )

    return usage.free / (1024 ** 3)


# ==========================================
# 4. AUTHENTICATION
# ==========================================

def get_token(username, password):
    response = requests.post(
        TOKEN_URL,
        data={
            "client_id": "cdse-public",
            "username": username,
            "password": password,
            "grant_type": "password"
        },
        timeout=60
    )

    response.raise_for_status()

    data = response.json()

    return data["access_token"]


# ==========================================
# 5. DOWNLOAD
# ==========================================

def download_product(
    session,
    product,
    username,
    password
):

    product_id = str(
        product["product_id"]
    ).strip()

    product_name = str(
        product["product_name"]
    ).strip()

    tile_id = str(
        product["tile_id"]
    ).strip()

    zip_path = (
        DOWNLOAD_DIR
        / f"{product_name}.zip"
    )

    part_path = (
        DOWNLOAD_DIR
        / f"{product_name}.zip.part"
    )

    if zip_path.exists():
        if zipfile.is_zipfile(zip_path):
            print("Already downloaded:", tile_id)
            return "already_downloaded"

        raise RuntimeError(
            f"Existing ZIP is invalid: {zip_path}"
        )

    url = (
        f"{DOWNLOAD_BASE}"
        f"({product_id})/$value"
    )

    for attempt in range(
        1, MAX_RETRIES + 1
    ):

        try:
            print(
                f"\nAttempt {attempt}/{MAX_RETRIES}"
            )

            token = get_token(
                username, password
            )

            existing_size = (
                part_path.stat().st_size
                if part_path.exists()
                else 0
            )

            headers = {
                "Authorization":
                    f"Bearer {token}"
            }

            if existing_size > 0:
                headers["Range"] = (
                    f"bytes={existing_size}-"
                )

            with session.get(
                url,
                headers=headers,
                stream=True,
                timeout=(30, 180),
                allow_redirects=True
            ) as response:

                # If the server cannot satisfy the
                # range, retry from the beginning.
                if response.status_code == 416:
                    part_path.unlink(
                        missing_ok=True
                    )
                    print(
                        "Range unavailable; "
                        "restarting download."
                    )
                    continue

                response.raise_for_status()

                resumed = (
                    response.status_code == 206
                    and existing_size > 0
                )

                if resumed:
                    content_range = (
                        response.headers.get(
                            "Content-Range", ""
                        )
                    )

                    expected_prefix = (
                        f"bytes {existing_size}-"
                    )

                    if not content_range.startswith(
                        expected_prefix
                    ):
                        raise RuntimeError(
                            "Unexpected Content-Range: "
                            + content_range
                        )

                mode = (
                    "ab" if resumed else "wb"
                )

                downloaded = (
                    existing_size if resumed else 0
                )

                content_length = int(
                    response.headers.get(
                        "Content-Length", 0
                    )
                )

                expected_total = (
                    downloaded + content_length
                    if content_length else None
                )

                print(
                    "Downloading:",
                    product_name
                )

                with open(
                    part_path, mode
                ) as output:

                    last_print = time.monotonic()

                    for chunk in (
                        response.iter_content(
                            chunk_size=CHUNK_SIZE
                        )
                    ):
                        if not chunk:
                            continue

                        output.write(chunk)
                        downloaded += len(chunk)

                        now = time.monotonic()

                        if (
                            now - last_print >= 3
                        ):
                            gb = (
                                downloaded / 1024**3
                            )

                            if expected_total:
                                percent = (
                                    100 * downloaded
                                    / expected_total
                                )

                                print(
                                    f"\r{gb:.2f} GB "
                                    f"({percent:.1f}%)",
                                    end="",
                                    flush=True
                                )
                            else:
                                print(
                                    f"\r{gb:.2f} GB",
                                    end="",
                                    flush=True
                                )

                            last_print = now

                print()

                if (
                    expected_total is not None
                    and downloaded != expected_total
                ):
                    raise IOError(
                        "Incomplete download: "
                        f"{downloaded}/"
                        f"{expected_total} bytes"
                    )

            print("Checking ZIP structure...")

            if not zipfile.is_zipfile(
                part_path
            ):
                raise ValueError(
                    "Downloaded file is not ZIP"
                )

            with zipfile.ZipFile(
                part_path
            ) as archive:

                if not archive.namelist():
                    raise ValueError(
                        "ZIP archive is empty"
                    )

                # This checks archive integrity,
                # including CRCs. It can take time.
                bad_file = archive.testzip()

                if bad_file is not None:
                    raise ValueError(
                        "Corrupt ZIP member: "
                        + bad_file
                    )

            part_path.rename(zip_path)

            print(
                "SUCCESS:",
                zip_path.name
            )

            return "downloaded"

        except (
            requests.RequestException,
            OSError,
            ValueError,
            RuntimeError,
            zipfile.BadZipFile
        ) as error:

            print(
                "\nDownload error:",
                error
            )

            if attempt == MAX_RETRIES:
                return "failed"

            time.sleep(
                min(30, attempt * 5)
            )

    return "failed"


# ==========================================
# 6. MAIN
# ==========================================

print("STEP 1: Loading verified plan...")

products = load_products()

print(
    "Products to download:",
    len(products)
)

for product in products:
    print(
        product["tile_id"],
        "|",
        product["product_name"]
    )

print("\nSTEP 2: Checking disk...")

free_gb = free_disk_gb()

print(
    f"Free disk space: {free_gb:.2f} GB"
)

if free_gb < MIN_FREE_GB:
    raise RuntimeError(
        "Not enough free disk space. "
        f"Need at least {MIN_FREE_GB} GB."
    )

print("\nSTEP 3: Copernicus login...")

username = input(
    "Copernicus email: "
).strip()

password = getpass(
    "Copernicus password: "
)

if not username or not password:
    raise ValueError(
        "Email and password required"
    )

print("\nChecking authentication...")

get_token(username, password)

print("Authentication successful.")

session = requests.Session()

results = []

print("\nSTEP 4: Downloading products...")

for index, product in enumerate(
    products, start=1
):

    print(
        "\n================================"
    )

    print(
        f"PRODUCT {index}/{len(products)}"
    )

    print(
        "================================"
    )

    if free_disk_gb() < 2:
        print(
            "STOP: less than 2 GB free."
        )
        break

    status = download_product(
        session,
        product,
        username,
        password
    )

    results.append({
        "product_name":
            product["product_name"],
        "tile_id":
            product["tile_id"],
        "status": status,
        "timestamp":
            datetime.now().isoformat(
                timespec="seconds"
            )
    })


# ==========================================
# 7. SAVE LOG
# ==========================================

import csv

with open(
    LOG_FILE,
    "w",
    newline="",
    encoding="utf-8-sig"
) as file:

    writer = csv.DictWriter(
        file,
        fieldnames=[
            "product_name",
            "tile_id",
            "status",
            "timestamp"
        ]
    )

    writer.writeheader()
    writer.writerows(results)


# ==========================================
# 8. SUMMARY
# ==========================================

successful = sum(
    r["status"] in (
        "downloaded",
        "already_downloaded"
    )
    for r in results
)

print("\n================================")
print("DOWNLOAD PROCESS FINISHED")
print("================================")

print(
    "Successful:",
    successful
)

print(
    "Processed:",
    len(results)
)

print(
    "Download directory:",
    DOWNLOAD_DIR
)

print(
    "Log:",
    LOG_FILE
)

print(
    "\nNext step: extract RGB data "
    "and create site crops."
)
