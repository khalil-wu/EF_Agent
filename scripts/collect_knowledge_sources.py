"""Download an explicit first-party source list for local knowledge curation.

Each source records its actual HTTP result. Papers are opened as PDFs before
they are counted as downloaded; unavailable sources remain in the manifest.
This command does not automatically accept graph claims or ingest failed files.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata

import pymupdf
import requests


def download(source: dict, root: Path) -> dict:
    destination = root / source["path"]
    response = requests.get(source["download_url"], timeout=45,
                            headers={"User-Agent": "EFAgent-local-research/0.3"})
    result = {**source, "retrieved_at": datetime.now(timezone.utc).isoformat(),
              "resolved_url": response.url, "http_status": response.status_code,
              "content_type": response.headers.get("Content-Type", "")}
    if response.status_code != 200:
        return {**result, "status": "unavailable"}
    if source["format"] == "pdf":
        if not response.content.startswith(b"%PDF-"):
            return {**result, "status": "not_pdf"}
        with pymupdf.open(stream=response.content, filetype="pdf") as pdf:
            result["pages"] = len(pdf)
            first_page = pdf[0].get_text()
            result["first_page_excerpt"] = first_page[:1000]
            normalized = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", first_page)).lower()
            if not all(term.lower() in normalized for term in source["identity_terms"]):
                return {**result, "status": "identity_mismatch"}
            result["pages_without_native_text"] = [i + 1 for i, page in enumerate(pdf) if not page.get_text().strip()]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(response.content)
    result.update(status="downloaded", sha256=hashlib.sha256(response.content).hexdigest(),
                  bytes=len(response.content))
    return result


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("catalog", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ids", nargs="*")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    sources = [s for s in catalog if not args.ids or s["doc_id"] in args.ids]
    results = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(download, source, root) for source in sources]
        for source, future in zip(sources, futures):
            try:
                result = future.result()
            except requests.exceptions.RequestException as exc:
                result = {**source, "status": "network_error", "error": str(exc)}
            results.append(result)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"doc_id": result["doc_id"], "status": result["status"],
                              "pages": result.get("pages"), "bytes": result.get("bytes"),
                              "title_excerpt": result.get("first_page_excerpt", "")[:250]}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
