"""Profile one collected snapshot run: completeness, nulls, and business-key uniqueness."""
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LANDING_DIR = REPO_ROOT / "landing" / "azure_retail"

KEY_FIELDS = ["meterId", "skuId", "type", "reservationTerm", "tierMinimumUnits", "currencyCode", "armRegionName"]
REQUIRED_FIELDS = ["meterId", "skuId", "type", "retailPrice", "unitPrice", "unitOfMeasure",
                   "currencyCode", "armRegionName", "armSkuName", "effectiveStartDate"]


def latest_complete_run():
    for manifest_path in sorted(LANDING_DIR.glob("run_date=*/run_id=*/_manifest.json"), reverse=True):
        manifest = json.loads(manifest_path.read_text())
        if manifest["status"] == "complete" and manifest["sku_filter"] is None:
            return manifest_path.parent
    sys.exit("No complete full run found under " + str(LANDING_DIR))


def load_items(run_dir, manifest):
    items = []
    for region in manifest["regions"]:
        for page in region["pages"]:
            body = json.loads((run_dir / page["file"]).read_text(encoding="utf-8"))
            if len(body["Items"]) != page["item_count"]:
                print(f"MISMATCH {page['file']}: manifest {page['item_count']}, file {len(body['Items'])}")
            items.extend(body["Items"])
    return items


def duplicate_keys(items, fields):
    keys = Counter(tuple("" if item.get(f) is None else str(item.get(f)) for f in fields) for item in items)
    return sum(1 for count in keys.values() if count > 1)


def main():
    run_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else latest_complete_run()
    manifest = json.loads((run_dir / "_manifest.json").read_text())
    items = load_items(run_dir, manifest)

    print(f"Run {manifest['run_id']} ({manifest['status']})")
    print(f"Records: {len(items)} loaded, {manifest['record_count']} in manifest")
    for region in manifest["regions"]:
        print(f"  {region['region']}: {region['record_count']} records, {region['page_count']} pages")

    print("\nNulls in required fields:")
    for field in REQUIRED_FIELDS:
        missing = sum(1 for item in items if item.get(field) in (None, ""))
        print(f"  {field}: {missing}")

    print("\nValues:")
    for field in ["currencyCode", "type", "unitOfMeasure", "reservationTerm", "isPrimaryMeterRegion"]:
        print(f"  {field}: {dict(Counter(item.get(field) for item in items).most_common())}")
    print(f"  retailPrice < 0: {sum(1 for item in items if item['retailPrice'] < 0)}")
    print(f"  retailPrice == 0: {sum(1 for item in items if item['retailPrice'] == 0)}")
    print(f"  retailPrice != unitPrice: {sum(1 for item in items if item['retailPrice'] != item['unitPrice'])}")
    print(f"  rows with savingsPlan: {sum(1 for item in items if item.get('savingsPlan'))}")

    print("\nBusiness key:")
    print(f"  meterId alone: {duplicate_keys(items, ['meterId'])} duplicated keys")
    print(f"  full key: {duplicate_keys(items, KEY_FIELDS)} duplicated keys")
    # Dropping a field and still getting zero duplicates means that field adds nothing to uniqueness.
    for field in KEY_FIELDS:
        reduced = [f for f in KEY_FIELDS if f != field]
        print(f"  without {field}: {duplicate_keys(items, reduced)} duplicated keys")


if __name__ == "__main__":
    main()