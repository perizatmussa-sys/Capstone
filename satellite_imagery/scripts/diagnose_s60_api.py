
from pathlib import Path
from getpass import getpass
import json

import requests

from download_s60_crops import (
    load_tasks,
    get_token,
    make_bounds,
    make_request,
    PROCESS_URL,
)


PROJECT = Path(r"D:\Karlag_SI_ML")

REPORT_FILE = (
    PROJECT
    / "dataset"
    / "sites_S60"
    / "metadata"
    / "S60_API_Diagnostic.txt"
)


def run_test(task, token, pixels):

    bbox, epsg = make_bounds(
        task["lon"],
        task["lat"]
    )

    payload = make_request(
        task,
        bbox,
        epsg
    )

    # Smaller diagnostic request first.
    payload["output"]["width"] = pixels
    payload["output"]["height"] = pixels

    print(f"\nTesting {pixels} x {pixels} pixels...")

    try:
        response = requests.post(
            PROCESS_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "image/tiff"
            },
            json=payload,
            timeout=(30, 180)
        )

    except requests.RequestException as error:
        print("Network error:", error)

        return {
            "size": pixels,
            "status": "network_error",
            "details": str(error)
        }

    print("HTTP status:", response.status_code)
    print(
        "Content-Type:",
        response.headers.get(
            "Content-Type", ""
        )
    )

    request_id = (
        response.headers.get("x-request-id")
        or response.headers.get("x-amzn-requestid")
        or ""
    )

    print("Request ID:", request_id or "not provided")

    if response.status_code == 200:

        print(
            "SUCCESS! Response size:",
            len(response.content),
            "bytes"
        )

        return {
            "size": pixels,
            "status": 200,
            "details": "Success",
            "bytes": len(response.content),
            "request_id": request_id
        }

    # The important part:
    # show the actual API error message.
    details = response.text[:3000]

    print("\nSERVER RESPONSE:")
    print(details)

    return {
        "size": pixels,
        "status": response.status_code,
        "details": details,
        "request_id": request_id
    }


def main():

    print("STEP 1: Loading S60 tasks...")

    tasks = load_tasks()

    task = tasks[0]

    print("Site:", task["site_id"])
    print("Tile:", task["tile_id"])
    print("Date:", task["date"])

    print("\nSTEP 2: Authentication...")

    client_id = input(
        "OAuth Client ID: "
    ).strip()

    client_secret = getpass(
        "OAuth Client Secret: "
    )

    token, _ = get_token(
        client_id,
        client_secret
    )

    print("Authentication successful.")

    results = []

    # First check a lightweight request.
    result = run_test(
        task,
        token,
        pixels=128
    )

    results.append(result)

    # Only test the original size if
    # the small request succeeded.
    if result["status"] == 200:

        result = run_test(
            task,
            token,
            pixels=1000
        )

        results.append(result)

    REPORT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with open(
        REPORT_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        file.write(
            "S60 API DIAGNOSTIC\n\n"
        )

        file.write(
            f"Site: {task['site_id']}\n"
        )

        file.write(
            f"Tile: {task['tile_id']}\n"
        )

        file.write(
            f"Date: {task['date']}\n\n"
        )

        file.write(
            json.dumps(
                results,
                indent=2,
                ensure_ascii=False
            )
        )

    print("\nDiagnostic saved:")
    print(REPORT_FILE)


if __name__ == "__main__":
    main()
