"""
Audiobookshelf Chapter Updater
Break down into testable pieces
"""

import os
import re
import tomllib
import logging
from pathlib import Path
import pprint
from urllib.parse import urljoin

import requests
from audible import Authenticator, Client

# Load configuration
config_filename = os.getenv("AUDIBLE_AUDIOBOOKSHELF_CONFIG_FILE")
if config_filename:
    config_file = Path(config_filename)
else:
    config_file = (
        Path.home() /
        ".config" /
        "audiobookshelf" /
        "config.toml"
    )
with config_file.open("rb") as f:
    config = tomllib.load(f)

# API configuration
API_URL = config['audiobookshelf']['base_url'].rstrip("/") + "/api/"
API_TOKEN = config['audiobookshelf']['api_token']
HEADERS = {"Authorization": f"Bearer {API_TOKEN}"}
CONFIG_AUDIBLE_AUTH_FILE = config['audible']['auth_file']


def list_libraries():
    """
    Fetch and return the list of libraries from Audiobookshelf.

    Returns:
        List of library dicts as returned by the API.
    """
    url = urljoin(API_URL, "libraries")
    response = requests.get(url, headers=HEADERS)
    response.raise_for_status()
    data = response.json()
    return data.get("libraries", [])


def find_book_library(libraries):
    """
    Given a list of library objects, return the ID of the first "book" library.

    Args:
        libraries (list): List of library dicts.
    Returns:
        str: The library ID for books.
    """
    for lib in libraries:
        if lib.get("mediaType") == "book":
            return lib["id"]
    raise RuntimeError("No book library found")


def list_library_items(library_id):
    """
    Fetch and return all items in the given library.

    Args:
        library_id (str): The ID of the library to fetch items from.
    Returns:
        list: List of item dicts.
    """
    params = {"limit": 0, "minified": False}
    url = urljoin(API_URL, f"libraries/{library_id}/items")
    response = requests.get(url, headers=HEADERS, params=params)
    response.raise_for_status()
    data = response.json()
    return data.get("results", [])


def fetch_library_item(item_id):
    #TODO
    url = urljoin(API_URL, f"items/{item_id}")
    response = requests.get(url, headers=HEADERS)
    response.raise_for_status()
    data = response.json()
    return data


def fetch_chapters(asin, region="us"):
    """
    Fetch chapter metadata for the given ASIN via Audiobookshelf's search/chapters API.

    Args:
        asin (str): Audible ASIN of the book.
        region (str): Region code, default "us".
    Returns:
        list: List of raw chapter dicts as returned by the API.
    """
    params = {"asin": asin, "region": region}
    url = urljoin(API_URL, "search/chapters")
    response = requests.get(url, headers=HEADERS, params=params)
    response.raise_for_status()
    data = response.json()
    return data.get("chapters", [])


def build_payload(chapters):
    """
    Build the update payload from raw chapter data.

    Args:
        chapters (list): Raw chapter data dicts.
    Returns:
        list: List of payload dicts {id, start, end, title}.
    """
    payload = []
    for idx, ch in enumerate(chapters):
        start = ch.get("startOffsetSec")
        length = ch.get("lengthMs", 0) / 1000.0
        end = start + length if start is not None else None
        title = ch.get("title", f"Chapter {idx+1}")
        payload.append({"id": idx, "start": start, "end": end, "title": title})
    return payload


def update_item_chapters(library_item_id, payload):
    """
    Send the chapter payload to Audiobookshelf to update a library item's chapters.

    Args:
        library_item_id (str): ID of the library item.
        payload (list): List of payload dicts.
    Returns:
        dict: JSON response from the API.
    """
    url = urljoin(API_URL, f"items/{library_item_id}/chapters")
    response = requests.post(
        url,
        headers={**HEADERS, "Content-Type": "application/json"},
        json={"chapters": payload}
    )
    response.raise_for_status()
    return response.json()


def update_item_asin(library_item_id, asin):
    """
    Update the ASIN metadata for a library item via API.

    Args:
        library_item_id (str): ID of the library item.
        asin (str): The Audible ASIN to set.
    Returns:
        dict: JSON response from the API.
    """
    url = urljoin(API_URL, f"items/{library_item_id}/media")
    body = {"metadata": {"asin": asin}}
    response = requests.patch(
        url,
        headers={**HEADERS, "Content-Type": "application/json"},
        json=body
    )
    response.raise_for_status()
    return response.json()


def derive_asin_from_filename(item):
    """
    Derive the root ASIN from the item's directory by inspecting .m4b filenames.
    For multipart books, this also fetches the origin_asin via Audible API if available.

    Args:
        item (dict): Library item dict from Audiobookshelf API.
    Returns:
        str or None: Derived ASIN, or None if not found.
    """
    logger = logging.getLogger(__name__)
    lib_root = Path(config['audiobookshelf']['audiobooks_dir'])

    # Convert API path to a Path object
    rel_path = Path(item.get('path', ''))
    dir_path = lib_root / rel_path
    # If directory doesn't exist, strip leading '/' and first folder, then rebuild
    if not dir_path.is_dir():
        parts = rel_path.parts
        # Skip the empty first part (from leading slash) and library name
        if len(parts) > 2:
            dir_path = lib_root.joinpath(*parts[2:])
        if not dir_path.is_dir():
            logger.error("Directory not found for item %s: %s", item.get('id'), dir_path)
            return None

    try:
        files = list(dir_path.iterdir())
    except Exception as e:
        logger.error("Failed to list files in %s: %s", dir_path, e)
        return None

    m4b_files = [p.name for p in files if p.suffix.lower() == '.m4b']
    if not m4b_files:
        logger.warning("No .m4b files found in %s", dir_path)
        return None

    filename = m4b_files[0]
    match = re.search(
            r"(?<![0-9A-Za-z])((?:B[0-9A-Za-z]{9}|[0-9]{9}[0-9Xx]))(?![0-9A-Za-z])",
            filename
        )
    if not match:
        logger.warning("No ASIN pattern found in filename %s", filename)
        return None
    part_asin = match.group(1)

    # Fetch origin_asin if multipart
    auth = Authenticator.from_file(CONFIG_AUDIBLE_AUTH_FILE)
    with Client(auth=auth) as client:
        # Fetch full product info to get origin_asin
        product = client.get(f"1.0/catalog/products/{part_asin}")

    if isinstance(product, dict) and 'product' in product:
        product = product['product']
    root_asin = product.get('origin_asin') or part_asin
    return root_asin


def main():
    """
    Main orchestration:
      1. List libraries
      2. Find book library
      3. List items
      4. For each item, fetch chapters and update
    """
    libraries = list_libraries()
    book_lib_id = find_book_library(libraries)
    items = list_library_items(book_lib_id)

    for item in items:
        item_expanded = fetch_library_item(item["id"])
        pprint.pprint(item_expanded)
        break
        lib_id = item.get("id")
        asin = item.get("media", {}).get("metadata", {}).get("asin")
        if not asin:
            derived = derive_asin_from_filename(item)
            if not derived:
                print(f"Skipping {lib_id} (no ASIN in metadata or filename)")
                continue
            print(f"Derived ASIN {derived} from filename for item {lib_id}")
            update_item_asin(lib_id, derived)
            asin = derived

        print(f"Processing {asin} -> {lib_id}")
        chapters = fetch_chapters(asin)
        if not chapters:
            print("  No chapters found; skipping.")
            continue

        payload = build_payload(chapters)
        resp = update_item_chapters(lib_id, payload)
        print("  Updated:", resp)


if __name__ == "__main__":
    main()

