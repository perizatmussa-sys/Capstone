
from getpass import getpass
import requests

from download_s60_crops import (
    load_tasks,
    get_token,
    make_bounds,
    make_request,
    PROCESS_URL,
)


def main():
    tasks = load_tasks()

    first = tasks[0]

    # Choose a different product from the
    # one that failed for S001.
    second = next(
        (
            task for task in tasks
            if task["product_id"]
            != first["product_id"]
        ),
        None
    )

    if second is None:
        raise RuntimeError(
            "No alternative product found."
        )

    print("Products to test:")

    for task in (first, second):
        print(
            task["site_id"],
            task["tile_id"],
            task["date"]
        )

    client_id = input(
        "\nOAuth Client ID: "
    ).strip()

    client_secret = getpass(
        "OAuth Client Secret: "
    )

    token, _ = get_token(
        client_id,
        client_secret
    )

    print("\nAuthentication successful.")

    for task in (first, second):

        print("\n" + "=" * 50)
        print("Site:", task["site_id"])
        print("Tile:", task["tile_id"])
        print("Date:", task["date"])

        bbox, epsg = make_bounds(
            task["lon"],
            task["lat"]
        )

        payload = make_request(
            task,
            bbox,
            epsg
        )

        payload["output"]["width"] = 128
        payload["output"]["height"] = 128

        try:
            response = requests.post(
                PROCESS_URL,
                headers={
                    "Authorization":
                        f"Bearer {token}",
                    "Accept": "image/tiff"
                },
                json=payload,
                timeout=(30, 180)
            )

            print(
                "HTTP status:",
                response.status_code
            )

            if response.status_code == 200:
                print(
                    "SUCCESS: satellite data returned"
                )
            else:
                print(
                    "Server message:"
                )
                print(
                    response.text[:1500]
                )

        except requests.RequestException as error:
            print("Network error:", error)

    print("\nTEST COMPLETE")


if __name__ == "__main__":
    main()
