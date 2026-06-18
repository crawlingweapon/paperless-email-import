"""Paperless-ngx API client with OneCLI proxy support."""
import json
import logging
import os
from pathlib import Path
from typing import Optional

import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)


class PaperlessClient:
    """HTTP client for Paperless-ngx REST API via OneCLI proxy."""

    def __init__(self, base_url: str, proxy_url: str, ca_cert: str):
        self.base_url = base_url.rstrip("/")
        self.proxies = {"http": proxy_url, "https": proxy_url}
        self.verify = ca_cert

    def _get(self, path: str, params: dict = None) -> Optional[dict]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            r = requests.get(url, params=params, proxies=self.proxies, verify=self.verify, timeout=30)
            return r.json() if r.status_code == 200 else None
        except Exception:
            return None

    def _post(self, path: str, files: dict = None, data: dict = None) -> requests.Response:
        url = f"{self.base_url}/{path.lstrip('/')}"
        return requests.post(url, files=files, data=data, proxies=self.proxies, verify=self.verify, timeout=60)

    def upload_document(
        self,
        pdf_path: str,
        title: str,
        correspondent_id: int,
        document_type_id: int,
        tags: list[int],
        created: str = "",
        custom_fields: dict = None,
    ) -> bool:
        """Upload a PDF document with metadata. Returns True on HTTP 200."""
        with open(pdf_path, "rb") as f:
            files = {"document": ("order.pdf", f, "application/pdf")}
            data = {
                "title": title,
                "correspondent": correspondent_id,
                "document_type": document_type_id,
                "tags": tags,
            }
            if created:
                data["created"] = created
            if custom_fields:
                data["custom_fields"] = json.dumps(custom_fields)

            r = self._post("documents/post_document/", files=files, data=data)
            return r.status_code == 200

    def get_document(self, doc_id: int) -> Optional[dict]:
        return self._get(f"documents/{doc_id}/")

    def list_tags(self) -> list[dict]:
        result = self._get("tags/")
        return result.get("results", []) if result else []

    def list_correspondents(self) -> list[dict]:
        result = self._get("correspondents/")
        return result.get("results", []) if result else []

    def create_tag(self, tag_data: dict) -> Optional[dict]:
        """Create a new tag. tag_data: {name, color, is_inbox_tag}. Returns created tag or None."""
        url = f"{self.base_url}/tags/"
        try:
            r = requests.post(
                url,
                json=tag_data,
                proxies=self.proxies,
                verify=self.verify,
                timeout=30,
                headers={"Content-Type": "application/json"},
            )
            return r.json() if r.status_code in (200, 201) else None
        except Exception as e:
            logger.warning(f"Failed to create tag: {e}")
            return None
