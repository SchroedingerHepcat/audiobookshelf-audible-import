import logging
import pathlib
import urllib.parse
from urllib.parse import urljoin

import requests

logger = logging.getLogger(__name__)

class AudioBookShelf:
    def __init__(self, config):
        self.config = config
        self.base_url = config['base_url']
        self.api_url = urljoin(self.base_url, '/api/')
        self.api_token = config['api_token']
        self.api_headers = {"Authorization": f"Bearer {self.api_token}"}
        self.audiobooks_dir = pathlib.Path(config['audiobooks_dir'])

    def list_libraries(self):
        """
        Fetch and return the list of libraries from Audiobookshelf.

        Returns:
            List of library dicts as returned by the API.
        """
        url = urljoin(self.api_url, "libraries")
        response = requests.get(url, headers=self.api_headers)
        response.raise_for_status()
        data = response.json()
        return data.get("libraries", [])

    def find_book_library(self, libraries):
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

    def get_book_library_id(self):
        libraries = self.list_libraries()
        return self.find_book_library(libraries)

    def list_library_items(self, library_id):
        """
        Fetch and return all items in the given library.

        Args:
            library_id (str): The ID of the library to fetch items from.
        Returns:
            list: List of item dicts.
        """
        params = {"limit": 0, "minified": False}
        url = urljoin(self.api_url, f"libraries/{library_id}/items")
        response = requests.get(url, headers=self.api_headers, params=params)
        response.raise_for_status()
        data = response.json()
        return data.get("results", [])

    def fetch_library_item(self, item_id):
        url = urljoin(self.api_url, f"items/{item_id}")
        response = requests.get(url, headers=self.api_headers)
        response.raise_for_status()
        data = response.json()
        return data

    def fetch_chapters(self, asin, region="us"):
        """
        Fetch chapter metadata for the given ASIN via Audiobookshelf's search/chapters API.

        Args:
            asin (str): Audible ASIN of the book.
            region (str): Region code, default "us".
        Returns:
            list: List of raw chapter dicts as returned by the API.
        """
        params = {"asin": asin, "region": region}
        url = urljoin(self.api_url, "search/chapters")
        response = requests.get(url, headers=self.api_headers, params=params)
        response.raise_for_status()
        data = response.json()
        return data.get("chapters", [])

    def build_chapter_payload(self, chapters):
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

    def update_item_chapters(self, library_item_id, chapters):
        """
        Send the chapter payload to Audiobookshelf to update a library item's chapters.

        Args:
            library_item_id (str): ID of the library item.
            payload (list): List of payload dicts.
        Returns:
            dict: JSON response from the API.
        """
        payload = self.build_chapter_payload(chapters)
        url = urljoin(self.api_url, f"items/{library_item_id}/chapters")
        response = requests.post(url
                                ,headers={**self.api_headers
                                         ,"Content-Type": "application/json"
                                         }
                                ,json={"chapters": payload}
                                )
        response.raise_for_status()
        return response.json()

    def update_item_asin(self, library_item_id, asin):
        """
        Update the ASIN metadata for a library item via API.

        Args:
            library_item_id (str): ID of the library item.
            asin (str): The Audible ASIN to set.
        Returns:
            dict: JSON response from the API.
        """
        url = urljoin(self.api_url, f"items/{library_item_id}/media")
        body = {"metadata": {"asin": asin}}
        response = requests.patch(url
                                 ,headers={**self.api_headers
                                          ,"Content-Type": "application/json"
                                          }
                                 ,json=body
                                 )
        response.raise_for_status()
        return response.json()

    def get_item_id_for_folder(self, library_id, folder_path):
        """
        Retrieve the item id for the specified folder in the specified library.

        Args:
            library_id (str): ID of the library to search
            folder_path (pathlib.Path): Path of the item for which to search
        Returns:
            str: item ID for the path
        """
        logger.debug("library_id = %s", library_id)
        logger.debug("folder_path = %s", folder_path)
        folder_relative = folder_path.relative_to(self.audiobooks_dir)
        logger.debug("folder_relative = %s", folder_relative)
        items = self.list_library_items(library_id)
        for item in items:
            item_path = pathlib.Path(*pathlib.Path(item["path"]).parts[2:])
            logger.debug("item_path = %s", item_path)
            if item_path == folder_relative:
                return item["id"]
        return None

    def trigger_library_rescan(self, library_id):
        """
        Call the audiobookshelf API to trigger a library rescan.

        Args:
            library_id (str): ID of the library to scan
        """
        params = {"force": 1}
        url = urljoin(self.api_url, f"libraries/{library_id}/scan")
        response = requests.get(url, headers=self.api_headers, params=params)
        return response.status_code == 200
