import argparse
import os
import jwt, time, requests, json
from tqdm import tqdm

import release_config
from credentials import SAMSUNG_SERVICE_ACCOUNT_ID, SAMSUNG_PRIVATE_KEY

service_account_id = SAMSUNG_SERVICE_ACCOUNT_ID

# ── Per-run switches (cycle, version and paths live in release_config.py) ──
UPLOAD_BUNDLE = True       # push the APK
UPLOAD_MATERIALS = True    # push icon, screenshots and listing text
AUTO_SUBMIT = True         # submit for review

STORE = "Samsung"
STORE_DIR = "SAMSUNG"

parser = argparse.ArgumentParser(
    description="Upload Shopee builds and marketing materials to the Samsung Galaxy Store.")
release_config.add_release_args(parser)
args = parser.parse_args()
release_config.apply_args(args)

file_path = release_config.root()
app_version = release_config.VERSION

# Per-region version overrides, for when one region ships a different build
version_overrides = {
    # "id": "3.75.28",
    # "my": "3.76.27",
    # "vn": "3.74.34",
    # "tw": "3.74.34",
    # "ph": "3.74.34",
    # "sg": "3.74.34",
    # "th": "3.76.26",
}


def _ver(region):
    return version_overrides.get(region, app_version)

def _ver_code(region):
    return _ver(region).replace(".", "")


def _gps_dir(package_name):
    return os.path.join(materials_dir, package_name)


def _bundle_path(bundle_name):
    return release_config.bundle_path(STORE_DIR, bundle_name, file_path)


lang_mapping = {"IND": "id", "ENG": "en-US", "002": "zh-TW", "VIE": "vi", "THA": "th"}

app_content_dic = {
    "000003378435": {
        "bundle": f"shopee-indonesia-release-samsung-{_ver('id')}.apk",
        "region": "id",
        "package_name": "com.shopee.id",
        "app_title": "8.8 Shopee Live Maraton",
        "default_language": "IND"
    },
    "000004313569": {
        "bundle": f"shopee-thailand-release-samsung-{_ver('th')}.apk",
        "region": "th",
        "package_name": "com.shopee.th",
        "app_title": "Shopee TH : ช้อปออนไลน์สุดคุ้ม",
        "default_language": "THA"
    },
    "000004313596": {
        "bundle": f"shopee-malaysia-release-samsung-{_ver('my')}.apk",
        "region": "my",
        "package_name": "com.shopee.my",
        "app_title": "Shopee MY: No Shipping Fee",
        "default_language": "ENG"
    },
    "000004315263": {
        "bundle": f"shopee-singapore-release-samsung-{_ver('sg')}.apk",
        "region": "sg",
        "package_name": "com.shopee.sg",
        "app_title": "Shopee: Shop and Get Cashback",
        "default_language": "ENG"
    },
    "000004315413": {
        "bundle": f"shopee-philipines-release-samsung-{_ver('ph')}.apk",
        "region": "ph",
        "package_name": "com.shopee.ph",
        "app_title": "Shopee PH: Shop Online",
        "default_language": "ENG"
    },
    "000004317641": {
        "bundle": f"shopee-taiwan-release-samsung-{_ver('tw')}.apk",
        "region": "tw",
        "package_name": "com.shopee.tw",
        "app_title": "蝦皮購物 | 花得更少買得更好",
        "default_language": "002"
    },
    "000004369137": {
        "bundle": f"shopee-vietnam-release-samsung-{_ver('vn')}.apk",
        "region": "vn",
        "package_name": "com.shopee.vn",
        "app_title": "Shopee: Mua Sắm Online",
        "default_language": "VIE"
    }
}




def samsung_json(response, path, what):
    """Read a value out of a Samsung response, failing with the server's own
    message rather than a KeyError several lines later. Samsung returns its
    errors as JSON bodies, so an expired key used to surface as
    KeyError: 'createdItem' rather than anything about credentials."""
    if not response.ok:
        raise RuntimeError(f"{what}: HTTP {response.status_code} — {response.text[:300]}")
    try:
        data = response.json()
    except ValueError:
        raise RuntimeError(f"{what}: response was not JSON — {response.text[:300]}")
    node = data
    for key in path:
        if not isinstance(node, dict) or key not in node:
            raise RuntimeError(f"{what}: expected {'.'.join(path)} in response — "
                               f"{json.dumps(data, ensure_ascii=False)[:300]}")
        node = node[key]
    return node


class _BodyProgressReader:
    """Wraps prepared-request body bytes; updates a tqdm bar as bytes are sent over the network."""
    def __init__(self, data, pbar):
        self._data = data if isinstance(data, (bytes, bytearray)) else data.encode()
        self._pos = 0
        self._pbar = pbar

    def read(self, size=-1):
        if size == -1:
            chunk = self._data[self._pos:]
            self._pos = len(self._data)
        else:
            chunk = self._data[self._pos:self._pos + size]
            self._pos += len(chunk)
        if chunk:
            self._pbar.update(len(chunk))
        return chunk

    def __len__(self):
        return len(self._data)


def upload_file_to_samsung(full_path, label=None, pbar=None):
    """Upload one file to Samsung.
    full_path: path to the file on disk; its basename is the name Samsung receives.
    pbar: pass a shared tqdm instance to update instead of creating a new bar.
    Progress tracks actual network bytes sent (via prepare+send), not disk reads.
    """
    headers = {
        'service-account-id': service_account_id,
        'Authorization': 'Bearer ' + access_token
    }
    r = requests.request("POST", "https://devapi.samsungapps.com/seller/createUploadSessionId",
                         headers=headers)
    session_id = samsung_json(r, ["sessionId"], "Create upload session")

    file_name = os.path.basename(full_path)
    payload = {'sessionId': session_id}
    upload_headers = {
        'service-account-id': service_account_id,
        'Authorization': 'Bearer ' + access_token
    }

    with open(full_path, 'rb') as fh:
        files = [('file', (file_name, fh, 'application/octet-stream'))]
        s = requests.Session()
        prep = s.prepare_request(requests.Request(
            "POST", "https://seller.samsungapps.com/galaxyapi/fileUpload",
            headers=upload_headers, data=payload, files=files
        ))

    own_pbar = None
    if pbar is None:
        desc = (label or file_name)[:42]
        own_pbar = tqdm(total=len(prep.body), desc=f"  {desc:<42}",
                        unit='B', unit_scale=True, unit_divisor=1024, ncols=100)
        pbar = own_pbar

    prep.body = _BodyProgressReader(prep.body, pbar)
    response = s.send(prep)
    s.close()

    if own_pbar is not None:
        own_pbar.close()

    return samsung_json(response, ["fileKey"], f"Upload {file_name}")


CONTENT_UPDATE_API = "https://devapi.samsungapps.com/seller/contentUpdate"
BINARY_UPDATE_API = "https://devapi.samsungapps.com/seller/v2/content/binary"
CONTENT_SUBMIT_API = "https://devapi.samsungapps.com/seller/contentSubmit"


def log_update_result(response, action="Content update"):
    try:
        data = response.json()
        if isinstance(data, list):
            data = data[0]
        status = data.get("contentStatus") or data.get("status") or response.status_code
        print(f"  {action}: {status} (HTTP {response.status_code})")
        if not response.ok:
            print("  Response body:", json.dumps(data, ensure_ascii=False))
            return False
        return True
    except Exception:
        print(f"  {action}: HTTP {response.status_code}")
        if response.text:
            print("  Response body:", response.text)
        return False


def update_app_metadata(content_id, app_title, default_language, long_description,
                        short_description, icon_key, screenshot_key_list):
    payload = {
        "contentId": content_id,
        "appTitle": app_title,
        "paid": "N",
        "defaultLanguageCode": default_language,
        "longDescription": long_description,
        "iconKey": icon_key,
        "screenshots": screenshot_key_list,
        "usExportLaws": True,
        "publicationType": "01",
    }
    if short_description is not None:
        payload["shortDescription"] = short_description
    response = requests.request(
        "POST", CONTENT_UPDATE_API,
        headers={'Content-Type': 'application/json',
                 'service-account-id': service_account_id,
                 'Authorization': 'Bearer ' + access_token},
        data=json.dumps(payload)
    )
    return response


def update_app_binary(content_id, build_name, v_code, v_no, package_name, build_key):
    payload = {
        "contentId": content_id,
        "fileName": build_name,
        "binarySeq": "1",
        "versionCode": v_code,
        "versionName": v_no,
        "packageName": package_name,
        "nativePlatforms": None,
        "apiminSdkVersion": "21",
        "apimaxSdkVersion": None,
        "iapSdk": "N",
        "gms": "Y",
        "filekey": build_key,
    }
    response = requests.request(
        "POST", BINARY_UPDATE_API,
        headers={'Content-Type': 'application/json',
                 'service-account-id': service_account_id,
                 'Authorization': 'Bearer ' + access_token},
        data=json.dumps(payload)
    )
    return response


# ── Resolve this run ───────────────────────────────────────────────────
# Ahead of auth, so a dry run needs no credentials and no network.
SUPPORTED_REGIONS = {info["region"] for info in app_content_dic.values()}
region_to_id = {info["region"]: cid for cid, info in app_content_dic.items()}

# Only demanded when this run actually pushes materials.
materials_dir = release_config.find_materials(file_path) if UPLOAD_MATERIALS else None

release_config.print_header(
    STORE,
    materials_dir or "(not needed — UPLOAD_MATERIALS is off)",
    f"bundle={UPLOAD_BUNDLE}  materials={UPLOAD_MATERIALS}  submit={AUTO_SUBMIT}")

selected_regions = release_config.resolve_regions(SUPPORTED_REGIONS, STORE)
active_apps = [(region_to_id[r], app_content_dic[region_to_id[r]]) for r in selected_regions]

# ── Preflight ──────────────────────────────────────────────────────────
# Runs on every run, not just --dry-run, and before auth. Samsung pushes exactly
# one language per app — the Google equivalent of default_language — and without
# this the uploader would carry on with an empty app_title and push it.
findings = []
for _, app in active_apps:
    package_name = app["package_name"]
    problems, apk = [], None
    if UPLOAD_BUNDLE:
        try:
            apk = os.path.basename(_bundle_path(app["bundle"]))
        except FileNotFoundError:
            problems.append(f"no build found for {app['bundle']}")
    if UPLOAD_MATERIALS:
        lang = lang_mapping.get(app["default_language"])
        if lang is None:
            problems.append(f"default_language {app['default_language']!r} is not in lang_mapping")
        else:
            problems += release_config.check_package(_gps_dir(package_name), [lang])
    findings.append((app["region"], package_name, apk, problems))

ready = release_config.report_preflight(findings, STORE, verbose=release_config.DRY_RUN)
if release_config.DRY_RUN or not ready:
    raise SystemExit(0 if ready else 1)

total_apps = len(active_apps)
count = 0

# ── Auth ───────────────────────────────────────────────────────────────
print("\n[Auth] Obtaining access token...", end=" ", flush=True)

jwt_payload = {
    "iss": service_account_id,
    "scopes": ["publishing"],
    "iat": int(time.time()),
    "exp": int(time.time()) + 600
}
secret = SAMSUNG_PRIVATE_KEY.encode("utf-8")
encoded_jwt = jwt.encode(jwt_payload, secret, algorithm="RS256")

response = requests.request(
    "POST", "https://devapi.samsungapps.com/auth/accessToken",
    headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + encoded_jwt},
    data={}
)
access_token = samsung_json(response, ["createdItem", "accessToken"], "Samsung auth")
print("OK")

# ── Upload ─────────────────────────────────────────────────────────────
# One pass, with each flag gating its own calls. The order of the API calls is
# unchanged: images, then the APK file, then metadata, then the binary record.
for idx, (app_content_id, app_content) in enumerate(active_apps, 1):
    package_name = app_content["package_name"]
    region = app_content["region"]
    default_language = app_content["default_language"]
    default_language_google = lang_mapping.get(default_language)
    v_no = _ver(region)
    v_code = _ver_code(region)
    build_name = app_content["bundle"]

    print(f"\n[{idx}/{total_apps}] {package_name}  v{v_no}  ({default_language})")

    app_title = app_content.get("app_title")
    app_desc = None
    short_desc = None
    icon_file_key = None
    screenshot_key_list = []

    if UPLOAD_MATERIALS:
        try:
            r = requests.request(
                "GET",
                "https://devapi.samsungapps.com/seller/contentInfo?contentId=" + app_content_id,
                headers={'Content-Type': 'application/json',
                         'service-account-id': service_account_id,
                         'Authorization': 'Bearer ' + access_token}
            )
            _ = len(json.loads(r.text)[0]["screenshots"])
        except Exception:
            print("  Warning: could not fetch existing app info, verify manually")

        img_folder = _gps_dir(package_name)
        with open(os.path.join(img_folder, "listing.txt"), encoding='utf-8') as f:
            listing_data = json.load(f, strict=False)

        # Preflight already confirmed this language is present.
        lang_listing = next(e for e in listing_data.get("listings", [])
                            if e.get("language") == default_language_google)

        # The ARP/RMS app name ships when this release provides one; a release
        # without a title keeps the fallback above, so an empty listing can
        # never blank the store name.
        listing_title = (lang_listing.get("title") or "").strip()
        if listing_title:
            app_title = listing_title
        print(f"  Title  {app_title}")
        app_desc = lang_listing.get("fullDescription", "").replace("--", "==")
        short_desc = lang_listing.get("shortDescription")
        if isinstance(short_desc, str):
            short_desc = short_desc.replace("--", "==")

        all_files = sorted(os.listdir(img_folder))
        icon_files = [fn for fn in all_files
                      if os.path.isfile(os.path.join(img_folder, fn))
                      and default_language_google in fn and "icon" in fn]
        screenshot_files = [fn for fn in all_files
                             if os.path.isfile(os.path.join(img_folder, fn))
                             and default_language_google in fn and "phoneScreenshots" in fn]

        image_files = icon_files + screenshot_files

        with tqdm(desc=f"  Images ({len(image_files)} files)",
                  unit='B', unit_scale=True, unit_divisor=1024, ncols=100) as pbar:
            for fn in icon_files:
                icon_file_key = upload_file_to_samsung(os.path.join(img_folder, fn), pbar=pbar)
            for fn in screenshot_files:
                key = upload_file_to_samsung(os.path.join(img_folder, fn), pbar=pbar)
                screenshot_key_list.append({"screenshotKey": key, "reuseYn": False})

    build_key = None
    if UPLOAD_BUNDLE:
        bundle_path = _bundle_path(build_name)
        build_name = os.path.basename(bundle_path)
        build_key = upload_file_to_samsung(bundle_path, label=f"APK   {build_name}")

    metadata_ok = True
    if UPLOAD_MATERIALS:
        response = update_app_metadata(
            app_content_id,
            app_title,
            default_language,
            app_desc,
            short_desc,
            icon_file_key,
            screenshot_key_list
        )
        metadata_ok = log_update_result(response, "Metadata update")

    if UPLOAD_BUNDLE:
        if metadata_ok:
            binary_response = update_app_binary(
                app_content_id,
                build_name,
                v_code,
                v_no,
                package_name,
                build_key
            )
            log_update_result(binary_response, "Binary update")
        else:
            print("  Skipping binary update due to metadata update failure")

    count += 1

print(f"\nDone. {count}/{total_apps} apps processed.")

# ── Auto-submit ────────────────────────────────────────────────────────
if AUTO_SUBMIT:
    print("\n[Submit] Auto-submitting...")
    for idx, (app_content_id, app_content) in enumerate(active_apps, 1):
        package_name = app_content["package_name"]
        response = requests.request(
            "POST", CONTENT_SUBMIT_API,
            headers={'Content-Type': 'application/json',
                     'service-account-id': service_account_id,
                     'Authorization': 'Bearer ' + access_token},
            data=json.dumps({"contentId": app_content_id})
        )
        if response.ok:
            result = "OK"
        else:
            try:
                msg = response.json().get("errorMsg") or response.json().get("message") or ""
                result = f"FAILED ({response.status_code}{': ' + msg if msg else ''})"
            except Exception:
                result = f"FAILED ({response.status_code})"
        print(f"  [{idx}/{total_apps}] {package_name}: {result}")
