"""Download marketing materials for a specific release cycle from the internal
App Release platform (RMS), in the same folder layout as
retrieve_google_play_store_info.py so the store upload scripts can consume it
unchanged.

Usage:
    python3 retrieve_rms_materials.py                         # cycle from release_config.py
    python3 retrieve_rms_materials.py --dry-run               # what has material, download nothing
    python3 retrieve_rms_materials.py 3.76                    # a different cycle, all regions
    python3 retrieve_rms_materials.py 3.76 --regions id,th    # specific regions
    python3 retrieve_rms_materials.py 3.77.x1 --out ~/Downloads

Output:
    <out>/RMS_Materials_<cycle>/com.shopee.<region>/
        listing.txt                       (same JSON shape as the GPS script)
        <lang>_icon_1.png
        <lang>_phoneScreenshots_<n>.jpeg
        <lang>_featureGraphic_1.jpeg

<out> defaults to this release's folder, which is where the upload scripts look
for materials, so a normal run needs no copying afterwards.

Filenames and listing.txt match retrieve_google_play_store_info.py exactly, so
huawei/samsung/vivo/oppo _package_upload.py consume this folder unchanged.
Extensions come from sniffing the downloaded bytes (as the GPS script does via
imghdr), so JPEGs are ".jpeg", not ".jpg". featureGraphic is an RMS-only extra;
the uploaders ignore it because its name contains neither "icon" nor
"phoneScreenshots".

Auth: RMS_AUTH cookie from credentials.py (copy from browser DevTools on
app-release.shopee.io; the cookie is a JWT valid ~30 days).
"""

import argparse
import json
import os
import sys

import requests
from requests.utils import requote_uri

import release_config
from credentials import RMS_AUTH_COOKIE

BASE_URL = "https://app-release.shopee.io/api/v1/native_material"
APP_TYPE = "shopee"
MARKETPLACE = "play_store"

# Material statuses safe to publish. RMS also has "draft" — work in progress that
# has not been signed off, so it is excluded unless --include-draft is passed.
PUBLISHABLE_STATUSES = {"submitted", "scheduled", "approved", "released"}


def api(path, method="GET", params=None, body=None):
    response = requests.request(
        method, f"{BASE_URL}/{path}",
        params=params, json=body,
        cookies={"RMS_AUTH": RMS_AUTH_COOKIE},
        timeout=30,
    )
    if response.status_code == 401:
        sys.exit("Unauthorized: RMS_AUTH cookie expired. Log in to "
                 "app-release.shopee.io and update RMS_AUTH_COOKIE in credentials.py.")
    response.raise_for_status()
    data = response.json()
    if data.get("error") not in (0, None):
        raise RuntimeError(f"RMS API error {data.get('error')}: {data.get('error_msg')}")
    return data["data"]


def material_list():
    """The platform's dashboard view: ONE row per region, the most recent material
    for it. It is not a full index — a cycle whose material has been superseded in
    every region does not appear here at all — so never use it to answer "does
    cycle X exist". Its one reliable use is enumerating the set of regions."""
    data = api("get_material_list", method="POST", body={
        "page_size": 200, "current_page": 1,
        "app_type": APP_TYPE, "material_marketplace": MARKETPLACE,
    })
    return data["items"]


def all_regions():
    return sorted({item["material_region"] for item in material_list()})


def recent_cycles():
    """Cycles visible in the dashboard view — a hint for typos, not a complete list."""
    return sorted({i["release_cycle"] for i in material_list() if i.get("release_cycle")})


_materials_cache = {}


def materials_for(release_cycle, region):
    key = (release_cycle, region)
    if key not in _materials_cache:
        data = api("get_android_material_info_by_release_cycle", params={
            "release_cycle": release_cycle,
            "material_region": region,
            "app_type": APP_TYPE,
        })
        _materials_cache[key] = data.get("items") or []
    return _materials_cache[key]


def discover_regions(release_cycle):
    """Regions holding material for one cycle. Has to ask per region, because the
    dashboard list only carries each region's newest material."""
    return [r for r in all_regions() if materials_for(release_cycle, r)]


def image_entries(groups):
    """Flatten [{material_image_url: [{imageUrl, fileSize: {name}, ...}]}] to a list."""
    entries = []
    for group in groups or []:
        entries.extend(group.get("material_image_url") or [])
    return entries


def fetch(url):
    response = requests.get(requote_uri(url), timeout=120)
    response.raise_for_status()
    return response.content


def sniff_ext(data, fallback):
    """Extension from the image's magic bytes, matching what the GPS script's
    imghdr.what() would return — note JPEG is spelled "jpeg", not "jpg"."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return fallback


def save_material(item, package_dir):
    lang = item["material_language"]

    for kind, key, single in [("icon", "material_app_icon", True),
                              ("phoneScreenshots", "material_screenshots", False),
                              ("featureGraphic", "material_feature_graphic", True)]:
        entries = image_entries(item.get(key))
        if single:
            entries = entries[:1]
        for n, entry in enumerate(entries, 1):
            data = fetch(entry["imageUrl"])
            meta_ext = os.path.splitext(entry["fileSize"]["name"])[1].lower()
            file_name = f"{lang}_{kind}_{n}{sniff_ext(data, meta_ext or '.png')}"
            print(f"    {file_name}")
            with open(os.path.join(package_dir, file_name), "wb") as f:
                f.write(data)

    # Same keys the androidpublisher listings response carries, so the uploaders
    # read this identically to a GPS_Content_Retrieval listing.txt.
    return {
        "language": lang,
        "title": item.get("material_app_name"),
        "fullDescription": item.get("material_full_description"),
        "shortDescription": item.get("material_short_description"),
        "video": item.get("material_video") or "",
    }


def main():
    parser = argparse.ArgumentParser(description="Download RMS marketing materials by release cycle.")
    parser.add_argument("release_cycle", nargs="?",
                        help=f"e.g. 3.76 or 3.77.x1 (default from release_config: {release_config.CYCLE})")
    parser.add_argument("--cycle", dest="cycle_opt",
                        help="same as the positional argument, for symmetry with the upload scripts")
    parser.add_argument("--regions", help="comma-separated region codes (e.g. id,th,sg); default: all")
    parser.add_argument("--out", default=None,
                        help="parent output directory (default: this release's folder)")
    parser.add_argument("--include-draft", action="store_true",
                        help="also download materials still in draft (excluded by default)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report which regions have publishable material, download nothing")
    args = parser.parse_args()

    # Command line first, then release_config.py. Downloading straight into the
    # release folder is what lets the uploaders discover this folder on their own.
    release_cycle = args.release_cycle or args.cycle_opt or release_config.CYCLE
    out = args.out or release_config.root()

    requested = args.regions or release_config.REGIONS
    if isinstance(requested, str):
        requested = [r.strip() for r in requested.split(",") if r.strip()]

    if requested:
        unknown = [r for r in requested if r.lower() not in release_config.ALL_REGIONS]
        if unknown:
            sys.exit(f"Unknown region(s): {', '.join(unknown)}\n"
                     f"  Shopee regions are: {', '.join(release_config.ALL_REGIONS)}")
        regions = [r.upper() for r in requested]
    else:
        print(f"Looking for material in cycle {release_cycle}...")
        regions = discover_regions(release_cycle)
        if not regions:
            sys.exit(f"No material for cycle {release_cycle} in any region. "
                     f"Cycles seen recently: {', '.join(recent_cycles()) or 'none'} "
                     f"(that list is not exhaustive).")
        print(f"Regions with material for cycle {release_cycle}: {', '.join(regions)}")

    args.release_cycle = release_cycle
    out_root = os.path.join(os.path.expanduser(out), f"RMS_Materials_{release_cycle}")
    # Printed before downloading so a wrong destination is obvious immediately.
    print(f"Writing to {out_root}")

    if args.dry_run:
        print("\nDry run — nothing will be downloaded.\n")
        for region in regions:
            items = [i for i in materials_for(release_cycle, region)
                     if i.get("material_marketplace") == MARKETPLACE]
            publishable = [i for i in items
                           if (i.get("material_status") or "").lower() in PUBLISHABLE_STATUSES]
            langs = sorted({i.get("material_language") for i in publishable})
            if langs:
                print(f"  {region:<3} com.shopee.{region.lower():<4} {', '.join(langs)}")
            else:
                other = sorted({(i.get('material_status') or '?').lower() for i in items})
                print(f"  {region:<3} com.shopee.{region.lower():<4} "
                      f"nothing publishable{' (' + ', '.join(other) + ')' if other else ''}")
        raise SystemExit(0)

    os.makedirs(out_root, exist_ok=True)

    done, skipped = [], []
    for region in regions:
        package_name = f"com.shopee.{region.lower()}"
        print(f"\n[{region}] {package_name}  cycle {args.release_cycle}")

        items = materials_for(args.release_cycle, region)

        # This endpoint takes no marketplace argument, and RMS also holds app_store
        # (iOS) material whose screenshots are the wrong size for Android stores.
        items = [i for i in items if i.get("material_marketplace") == MARKETPLACE]

        # Never publish unsigned-off copy by accident.
        kept = []
        for item in items:
            status = (item.get("material_status") or "").lower()
            if status in PUBLISHABLE_STATUSES or (args.include_draft and status == "draft"):
                kept.append(item)
            else:
                print(f"    skipping {item.get('material_language')} — status '{status}'"
                      f"{' (use --include-draft)' if status == 'draft' else ''}")

        # One listing entry per language, as androidpublisher returns.
        items, seen = [], set()
        for item in kept:
            lang = item.get("material_language")
            if lang in seen:
                print(f"    skipping duplicate material for language '{lang}'")
                continue
            seen.add(lang)
            items.append(item)

        if not items:
            print("    no publishable material for this cycle — skipping")
            skipped.append(region)
            continue

        package_dir = os.path.join(out_root, package_name)
        os.makedirs(package_dir, exist_ok=True)

        listings = [save_material(item, package_dir) for item in items]
        with open(os.path.join(package_dir, "listing.txt"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"kind": "androidpublisher#listingsListResponse",
                                "listings": listings}, ensure_ascii=False))
        print(f"    listing.txt ({', '.join(l['language'] for l in listings)})")
        done.append((region, [l["language"] for l in listings]))

    print(f"\nSaved to {out_root}")
    if done:
        # The uploaders find material by language code, and silently find nothing
        # if it does not match what they ask for (Samsung maps IND→id, THA→th,
        # 002→zh-TW; Huawei asks for id/en-US/vi/zh-TW/th/es-419). Check this list
        # before uploading.
        print("Languages per package:")
        for region, langs in done:
            print(f"  com.shopee.{region.lower():<4} {', '.join(langs)}")
    else:
        print("Downloaded: none")
    if skipped:
        print(f"No publishable material for cycle {release_cycle}: {', '.join(skipped)}")

    if os.path.dirname(out_root) == release_config.root():
        print("\nThe upload scripts will find this folder on their own. Check with:")
        print("  python3 huawei_package_upload.py --dry-run")
    else:
        print(f"\nThis is outside the release folder ({release_config.root()}), so the "
              f"upload scripts will not discover it.\nEither copy it there, or run them "
              f"with --materials {out_root}")


if __name__ == "__main__":
    main()
