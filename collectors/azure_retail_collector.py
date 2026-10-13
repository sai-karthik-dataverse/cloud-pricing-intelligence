"""Collect Azure retail VM prices and save the raw API pages as one snapshot run."""
import argparse
import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "configs" / "collector.json"
RETRY_STATUSES = {429, 500, 502, 503, 504}
# A full region is under 20 pages today, so hitting this means paging is stuck in a loop.
MAX_PAGES_PER_REGION = 200

log = logging.getLogger("collector")


def utc_now():
    return datetime.now(timezone.utc)


def get_with_retry(session, url, params, config):
    max_retries = config["max_retries"]
    for attempt in range(1, max_retries + 1):
        wait = min(2 ** attempt, 60)
        try:
            response = session.get(url, params=params, timeout=config["timeout_seconds"])
            if response.status_code not in RETRY_STATUSES:
                response.raise_for_status()
                return response
            reason = f"HTTP {response.status_code}"
            retry_after = response.headers.get("Retry-After", "")
            if retry_after.isdigit():
                wait = int(retry_after)
        except (requests.ConnectionError, requests.Timeout) as exc:
            reason = type(exc).__name__

        if attempt < max_retries:
            log.warning("%s on attempt %d, retrying in %ds", reason, attempt, wait)
            time.sleep(wait)

    raise RuntimeError(f"Gave up after {max_retries} attempts ({reason}): {url}")


def collect_region(session, config, region, run_dir, sku=None):
    filter_text = f"serviceName eq '{config['service_name']}' and armRegionName eq '{region}'"
    if sku:
        filter_text += f" and armSkuName eq '{sku}'"
    params = {
        "api-version": config["api_version"],
        "currencyCode": f"'{config['currency']}'",
        "$filter": filter_text,
    }

    region_dir = run_dir / region
    region_dir.mkdir()
    pages = []
    url = config["api_url"]

    while url:
        if len(pages) >= MAX_PAGES_PER_REGION:
            raise RuntimeError(f"{region}: more than {MAX_PAGES_PER_REGION} pages, stopping")

        response = get_with_retry(session, url, params, config)
        body = response.json()
        page_number = len(pages) + 1
        file_name = f"page_{page_number:04d}.json"
        (region_dir / file_name).write_bytes(response.content)

        pages.append({
            "page_number": page_number,
            "file": f"{region}/{file_name}",
            "request_url": response.url,
            "http_status": response.status_code,
            "item_count": len(body.get("Items", [])),
            "fetched_at": utc_now().isoformat(),
            "sha256": hashlib.sha256(response.content).hexdigest(),
        })
        # NextPageLink already carries the filter and api-version, so send no extra params.
        url, params = body.get("NextPageLink"), None

    record_count = sum(p["item_count"] for p in pages)
    log.info("%s: %d records across %d pages", region, record_count, len(pages))
    return {
        "region": region,
        "filter": filter_text,
        "page_count": len(pages),
        "record_count": record_count,
        "pages": pages,
    }


def write_manifest(run_dir, manifest):
    (run_dir / "_manifest.json").write_text(json.dumps(manifest, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sku", help="limit to one armSkuName for a quick test, e.g. Standard_D2s_v5")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = json.loads(CONFIG_PATH.read_text())

    started_at = utc_now()
    run_id = started_at.strftime("%Y%m%dT%H%M%SZ")
    run_dir = REPO_ROOT / config["landing_dir"] / f"run_date={started_at:%Y-%m-%d}" / f"run_id={run_id}"
    # No exist_ok: a run folder is never reused, so earlier snapshots can't be overwritten.
    run_dir.mkdir(parents=True)

    manifest = {
        "run_id": run_id,
        "source": "azure_retail_prices",
        "collector": "local_python",
        "api_version": config["api_version"],
        "currency": config["currency"],
        "sku_filter": args.sku,
        "started_at": started_at.isoformat(),
        "status": "running",
        "regions": [],
    }
    write_manifest(run_dir, manifest)

    try:
        with requests.Session() as session:
            for region in config["regions"]:
                manifest["regions"].append(collect_region(session, config, region, run_dir, args.sku))
                write_manifest(run_dir, manifest)
        manifest["status"] = "complete"
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["error"] = repr(exc)
        raise
    finally:
        manifest["finished_at"] = utc_now().isoformat()
        manifest["record_count"] = sum(r["record_count"] for r in manifest["regions"])
        write_manifest(run_dir, manifest)
        log.info("Run %s %s: %d records, saved to %s",
                 run_id, manifest["status"], manifest["record_count"], run_dir)


if __name__ == "__main__":
    main()