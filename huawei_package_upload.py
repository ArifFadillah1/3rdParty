import argparse
import json
import os
import time

import requests

import release_config
from credentials import HUAWEI_ACCOUNTS


# ── Per-run switches (cycle, version and paths live in release_config.py) ──
UPLOAD_BUNDLE = True       # push the APK
UPLOAD_MATERIALS = True    # push icon, screenshots and listing text
AUTO_SUBMIT = False        # submit for review
# Take the AppGallery app name from listing.txt (the release's per-region
# campaign titles). Off = don't touch the name: appName is omitted and the
# store keeps whatever it currently shows.
USE_LISTING_TITLE = True
# 1 = FULL RELEASE; 3 = PHASED RELEASE
RELEASE_TYPE = 1
PHASE_RELEASE_START_TIME = "2024-07-22T11:00:00+0800"
PHASE_RELEASE_END_TIME = "2024-07-22T11:05:00+0800"
PHASE_RELEASE_PERCENTAGE = "10"
PHASE_RELEASE_DESC = "Phased Release"
suffix = "apk"
waiting_time = 60

# (connect, read) timeouts in seconds. Uploads get a long read timeout because
# Huawei only answers once it has received and parsed the whole file.
API_TIMEOUT = (10, 60)
UPLOAD_TIMEOUT = (10, 900)

STORE = "Huawei"
STORE_DIR = "HUAWEI"

parser = argparse.ArgumentParser(
    description="Upload Shopee builds and marketing materials to Huawei AppGallery.")
release_config.add_release_args(parser)
_submit = parser.add_mutually_exclusive_group()
_submit.add_argument("--submit", dest="submit", action="store_true", default=None,
                     help=f"submit for review after upload (default: {AUTO_SUBMIT}, "
                          f"from AUTO_SUBMIT in this script)")
_submit.add_argument("--no-submit", dest="submit", action="store_false",
                     help="upload only, do not submit")
args = parser.parse_args()
release_config.apply_args(args)
if args.submit is not None:
    AUTO_SUBMIT = args.submit

if RELEASE_TYPE not in (1, 3):
    raise SystemExit(f"RELEASE_TYPE must be 1 (full release) or 3 (phased), got {RELEASE_TYPE!r}")

file_path = release_config.root()
app_version = release_config.VERSION

# Per-region version overrides, for when one region ships a different build
version_overrides = {
    # "id": "3.75.24",
    # "my": "3.76.27",
    # "vn": "3.75.24",
    # "tw": "3.75.24",
    # "ph": "3.75.24",
    # "sg": "3.75.24",
    # "th": "3.76.26",
    # "mx": "3.75.24",
}


def _ver(region):
    return version_overrides.get(region, app_version)


def _gps_dir(package_name):
    return os.path.join(materials_dir, package_name)


def _bundle_path(bundle_name):
    return release_config.bundle_path(STORE_DIR, bundle_name, file_path)


accounts = [
    {
        **HUAWEI_ACCOUNTS[0],  # Shopee main account (ID/MY/VN/TW/PH/SG/TH)
        "app_id_dic": {
            "101018311": {"region": "id", "bundle": f"shopee-indonesia-release-huawei-{_ver('id')}.{suffix}", "langs": ["id"], "package_name": "com.shopee.id"},
            "100576469": {"region": "my", "bundle": f"shopee-malaysia-release-huawei-{_ver('my')}.{suffix}", "langs": ["en-US"], "package_name": "com.shopee.my"},
            "101433653": {"region": "vn", "bundle": f"shopee-vietnam-release-huawei-{_ver('vn')}.{suffix}", "langs": ["vi"], "package_name": "com.shopee.vn"},
            "100914881": {"region": "tw", "bundle": f"shopee-taiwan-release-huawei-{_ver('tw')}.{suffix}", "langs": ["zh-TW"], "package_name": "com.shopee.tw", "app_name": "蝦皮購物 | 花得更少買得更好"},
            "100706415": {"region": "ph", "bundle": f"shopee-philipines-release-huawei-{_ver('ph')}.{suffix}", "langs": ["en-US"], "package_name": "com.shopee.ph"},
            "100936781": {"region": "sg", "bundle": f"shopee-singapore-release-huawei-{_ver('sg')}.{suffix}", "langs": ["en-US"], "package_name": "com.shopee.sg"},
            "100447193": {"region": "th", "bundle": f"shopee-thailand-release-huawei-{_ver('th')}.{suffix}", "langs": ["th", "en-US"], "package_name": "com.shopee.th"},
        }
    },
    {
        **HUAWEI_ACCOUNTS[1],  # Shopee MX account
        "app_id_dic": {
            "104985179": {"region": "mx", "bundle": f"shopee-mexico-release-huawei-{_ver('mx')}.{suffix}", "langs": ["es-419"], "package_name": "com.shopee.mx"},
        }
    },
]


def check_huawei_api(response):
    body = response.json()
    ret = body.get("ret", {})
    if ret.get("code", 0) != 0:
        raise RuntimeError(f"Huawei API error {ret.get('code')}: {ret.get('msg')}")


class _UploadProgress:
    BAR_WIDTH = 25

    def __init__(self, data, prefix):
        self._data = data if isinstance(data, (bytes, bytearray)) else data.encode()
        self._pos = 0
        self._size = len(self._data)
        self._prefix = prefix

    def read(self, size=-1):
        if size == -1:
            chunk = self._data[self._pos:]
            self._pos = self._size
        else:
            chunk = self._data[self._pos:self._pos + size]
            self._pos += len(chunk)
        pct = self._pos * 100 // self._size if self._size else 100
        filled = pct * self.BAR_WIDTH // 100
        bar = '█' * filled + '░' * (self.BAR_WIDTH - filled)
        print(f'\r{self._prefix}[{bar}] {pct:3d}%', end='', flush=True)
        return chunk

    def __len__(self):
        return self._size


def _upload_with_progress(upload_url, payload, fh, filename, prefix):
    """POST multipart upload with inline progress bar. Returns response."""
    files = [('file', (filename, fh, 'application/octet-stream'))]
    s = requests.Session()
    prep = s.prepare_request(requests.Request("POST", upload_url, data=payload, files=files))
    prep.body = _UploadProgress(prep.body, prefix)
    resp = s.send(prep, timeout=UPLOAD_TIMEOUT)
    s.close()
    return resp


def _obs_upload(app_id, api_headers, path):
    """Upload one file via the OBS flow and return its objectId for binding.

    Huawei moved materials to this flow (~Oct 2026): upload-url/for-obs hands
    out a pre-signed PUT URL plus an objectId, the raw bytes go to that URL,
    and app-file-info takes the objectId as fileDestUrl. The legacy
    /upload-url + imageResolutionSingature flow now fails every image link
    with 204144641 'Failed to verifySignature'.
    """
    size = os.path.getsize(path)
    name = os.path.basename(path)
    response = requests.get(
        "https://connect-api.cloud.huawei.com/api/publish/v2/upload-url/for-obs"
        f"?appId={app_id}&fileName={name}&contentLength={size}",
        headers=api_headers, timeout=API_TIMEOUT)
    response.raise_for_status()
    check_huawei_api(response)
    url_info = response.json()["urlInfo"]
    with open(path, "rb") as fh:
        response = requests.request(url_info["method"], url_info["url"],
                                    headers=url_info["headers"], data=fh,
                                    timeout=UPLOAD_TIMEOUT)
    response.raise_for_status()
    return url_info["objectId"]


# ── Resolve this run ───────────────────────────────────────────────────
SUPPORTED_REGIONS = {app["region"]
                     for account in accounts
                     for app in account["app_id_dic"].values()}

# Only demanded when this run actually pushes materials, so an APK-only run is
# not blocked by a missing materials folder.
materials_dir = release_config.find_materials(file_path) if UPLOAD_MATERIALS else None

_flags = f"bundle={UPLOAD_BUNDLE}  materials={UPLOAD_MATERIALS}  submit={AUTO_SUBMIT}"
if AUTO_SUBMIT:
    _flags += f"  ({'full release' if RELEASE_TYPE == 1 else PHASE_RELEASE_PERCENTAGE + '% phased'})"
release_config.print_header(
    STORE, materials_dir or "(not needed — UPLOAD_MATERIALS is off)", _flags)

selected_regions = release_config.resolve_regions(SUPPORTED_REGIONS, STORE)

# ── Preflight ──────────────────────────────────────────────────────────
# Runs on every run, not just --dry-run, and before the first access token is
# fetched. Huawei asks for a specific language per app (langs), and the uploader
# finds nothing at all if listing.txt does not carry it.
findings = []
for account in accounts:
    for app in account["app_id_dic"].values():
        if app["region"] not in selected_regions:
            continue
        package_name = app["package_name"]
        problems, apk = [], None
        if UPLOAD_BUNDLE:
            try:
                apk = os.path.basename(_bundle_path(app["bundle"]))
            except FileNotFoundError:
                problems.append(f"no build found for {app['bundle']}")
        if UPLOAD_MATERIALS:
            problems += release_config.check_package(_gps_dir(package_name), app["langs"])
        findings.append((app["region"], package_name, apk, problems))
findings.sort(key=lambda row: selected_regions.index(row[0]))

ready = release_config.report_preflight(findings, STORE, verbose=release_config.DRY_RUN)
if release_config.DRY_RUN or not ready:
    raise SystemExit(0 if ready else 1)

# Apps that failed any step this run. Later stages skip them, auto-submit
# refuses them, and the run ends non-zero with a ready-made rerun command —
# so one region's failure no longer kills or taints the rest of the release.
failed_apps = {}  # app_id -> region


def _fail(app_id, app, stage, exc=None):
    failed_apps[app_id] = app["region"]
    if exc is not None:
        print(f"\n  FAILED during {stage} — {type(exc).__name__}: {exc}")
        print("  Continuing with the remaining apps.")


for account in accounts:
    client_id = account["client_id"]
    client_secret = account["client_secret"]
    app_id_dic = {k: v for k, v in account["app_id_dic"].items()
                  if v.get("region") in selected_regions}
    if not app_id_dic:
        continue

    # Get Access Token
    url = "https://connect-api.cloud.huawei.com/api/oauth2/v1/token"

    payload = json.dumps({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret
    })
    headers = {
        'Content-Type': 'application/json'
    }

    try:
        response = requests.request("POST", url, headers=headers, data=payload,
                                    timeout=API_TIMEOUT)
        response.raise_for_status()
        token = response.json()["access_token"]
    except Exception as exc:
        print(f"\nAccount {client_id}: token request FAILED — {exc}")
        print("Skipping this account's apps; any other account still runs.")
        for app_id, app in app_id_dic.items():
            failed_apps[app_id] = app["region"]
        continue

    print(f"\n{'─' * 56}")
    print(f"Account  {client_id}  ({len(app_id_dic)} app{'s' if len(app_id_dic) > 1 else ''})")
    print(f"{'─' * 56}")

    # Check if need to update app presence
    if UPLOAD_MATERIALS:
        def upload_app_materials(app_id, app):
            """Push one app's listing text, icon and screenshots. Returns False
            when any step reported FAILED, so the caller can gate submit on it."""
            ok = True
            lang_list = app["langs"]
            package_name = app["package_name"]

            print(f"\n[{app_id}] {package_name}")

            with open(os.path.join(_gps_dir(package_name), "listing.txt"), encoding='utf-8') as json_data:
                listing_data = json.load(json_data, strict=False)

            for lang in lang_list:
                #Update App Basic Info
                url = "https://connect-api.cloud.huawei.com/api/publish/v2/app-language-info?appId=" + app_id
                lang_listing = None
                screenshot_local_file_list = []
                screenshot_remote_file_list = []

                for temp_lang_listing in listing_data.get("listings"):
                    if temp_lang_listing.get("language") == lang:
                        lang_listing = temp_lang_listing
                        break

                if lang_listing is not None:
                    headers = {
                        'client_id': client_id,
                        'Authorization': 'Bearer ' + token
                    }

                    # listing.txt titles are the release's per-region names
                    # (usually campaign names, "Shopee 10.10 Brands Festival").
                    # With USE_LISTING_TITLE off, appName is omitted so the
                    # store keeps its current name — the old fallback of
                    # sending "Shopee" literally renamed every app to that.
                    app_name = app.get("app_name")
                    if USE_LISTING_TITLE:
                        app_name = (lang_listing.get("title") or "").strip() or app_name
                    body = {
                        "lang": lang,
                        "appDesc": lang_listing.get("fullDescription"),
                        "briefInfo": lang_listing.get("shortDescription")
                    }
                    if app_name:
                        body["appName"] = app_name
                    payload = json.dumps(body)

                    response = requests.request("PUT", url, headers=headers, data=payload, timeout=API_TIMEOUT)
                    response.raise_for_status()
                    # Huawei reports failures as a ret code inside a 200, so
                    # raise_for_status alone used to print OK over any error.
                    ret = response.json().get("ret", {})
                    if ret.get("code", 0) == 0:
                        print(f"  App info ({lang}) .. OK  ({app_name or 'name kept'})")
                    else:
                        print(f"  App info ({lang}) .. FAILED  {ret.get('code')}: {ret.get('msg')}")
                        ok = False

                    # Update App Icon
                    for filename in os.listdir(_gps_dir(package_name)):
                        f = os.path.join(_gps_dir(package_name), filename)

                        if os.path.isfile(f) and lang in filename and "icon" in filename:
                            print(f"  Icon  {filename}")

                            api_headers = {
                                'client_id': client_id,
                                'Authorization': 'Bearer ' + token
                            }
                            object_id = _obs_upload(app_id, api_headers, f)

                            # Link App Icon File with App
                            link_url = "https://connect-api.cloud.huawei.com/api/publish/v2/app-file-info?appId=" + app_id
                            link_payload = {
                                "fileType": 0,
                                "files": [{
                                    "fileName": filename,
                                    "fileDestUrl": object_id,
                                }],
                                "lang": lang
                            }
                            response = requests.request(
                                "PUT", link_url,
                                headers={**api_headers, 'Content-Type': 'application/json'},
                                data=json.dumps(link_payload), timeout=API_TIMEOUT)
                            response.raise_for_status()
                            ret = response.json().get("ret", {})
                            if ret.get("code", 0) == 0:
                                print(f"  Icon .. OK")
                            else:
                                print(f"  Icon .. FAILED (code={ret.get('code')}: {ret.get('msg')})")
                                ok = False

                    #Update App Screenshots
                    for filename in os.listdir(_gps_dir(package_name)):
                        f = os.path.join(_gps_dir(package_name), filename)

                        if os.path.isfile(f) and lang in filename and "phoneScreenshots" in filename:
                            screenshot_local_file_list.append(f)

                    screenshot_local_file_list.sort()

                    total_screenshots = len(screenshot_local_file_list)
                    print(f"  Screenshots  uploading {total_screenshots} files...", end='', flush=True)
                    api_headers = {
                        'client_id': client_id,
                        'Authorization': 'Bearer ' + token
                    }
                    for screenshot_local_file in screenshot_local_file_list:
                        object_id = _obs_upload(app_id, api_headers, screenshot_local_file)
                        screenshot_remote_file_list.append({
                            "fileName": os.path.basename(screenshot_local_file),
                            "fileDestUrl": object_id,
                        })

                    # Link Screenshot File with App
                    url = "https://connect-api.cloud.huawei.com/api/publish/v2/app-file-info?appId=" + app_id

                    payload = json.dumps({
                        "fileType": 2,
                        "files": screenshot_remote_file_list,
                        "lang": lang,
                        "imgShowType": 1
                    })
                    headers = {
                        'client_id': client_id,
                        'Content-Type': 'application/json',
                        'Authorization': 'Bearer ' + token
                    }

                    response = requests.request("PUT", url, headers=headers, data=payload, timeout=API_TIMEOUT)
                    response.raise_for_status()
                    ok_count = len(screenshot_remote_file_list)
                    ret = response.json().get("ret", {})
                    if ret.get("code", 0) == 0:
                        link_status = "OK"
                    else:
                        link_status = f"FAILED (code={ret.get('code')}: {ret.get('msg')})"
                        ok = False
                    print(f'\r  Screenshots  {ok_count}/{total_screenshots} linked .. {link_status}{" " * 10}')
            return ok

        for app_id, app in app_id_dic.items():
            try:
                if not upload_app_materials(app_id, app):
                    _fail(app_id, app, "materials")
            except Exception as exc:
                _fail(app_id, app, "materials", exc)

    # App bundle upload
    if UPLOAD_BUNDLE:
        def upload_app_bundle(app_id, app):
            """Upload and link one APK. Returns False when a step reported FAILED."""
            ok = True
            file_name = app["bundle"]
            full_file_path = _bundle_path(file_name)
            package_name = app["package_name"]

            if not UPLOAD_MATERIALS:
                print(f"\n[{app_id}] {package_name}")

            url = "https://connect-api.cloud.huawei.com/api/publish/v2/upload-url?appId=" + app_id + "&suffix=" + suffix

            headers = {
                'client_id': client_id,
                'Authorization': 'Bearer ' + token
            }

            response = requests.request("GET", url, headers=headers, timeout=API_TIMEOUT)
            response.raise_for_status()
            check_huawei_api(response)

            upload_url = response.json()["uploadUrl"]
            upload_auth_code = response.json()["authCode"]

            print(f"  Bundle  {file_name}")
            payload = {'authCode': upload_auth_code,
                       'fileCount': '1'}
            with open(full_file_path, 'rb') as fh:
                response = _upload_with_progress(upload_url, payload, fh, file_name, "          ")

            rsp = response.json()["result"]["UploadFileRsp"]
            if rsp["ifSuccess"] == 1:
                file_uploaded_url = rsp["fileInfoList"][0]["fileDestUlr"]

                # Link File with App
                url = "https://connect-api.cloud.huawei.com/api/publish/v2/app-file-info?appId=" + app_id

                payload = json.dumps({
                    "fileType": 5,
                    "files": {
                        "fileName": file_name,
                        "fileDestUrl": file_uploaded_url
                    }
                })
                headers = {
                    'client_id': client_id,
                    'Content-Type': 'application/json',
                    'Authorization': 'Bearer ' + token
                }

                response = requests.request("PUT", url, headers=headers, data=payload, timeout=API_TIMEOUT)
                response.raise_for_status()
                # A failed link is reported as a ret code inside a 200, so this
                # used to print OK for a bundle that never attached to the app.
                ret = response.json().get("ret", {})
                if ret.get("code", 0) == 0:
                    print(f'\r          Upload & link .. OK{" " * 30}')
                else:
                    print(f'\r          Link FAILED  {ret.get("code")}: {ret.get("msg")}{" " * 10}')
                    ok = False
            else:
                print(f'\r          Upload FAILED{" " * 30}')
                ok = False
            return ok

        for app_id, app in app_id_dic.items():
            if app_id in failed_apps:
                print(f"\n[{app_id}] {app['package_name']}  bundle SKIPPED — an earlier step failed")
                continue
            try:
                if not upload_app_bundle(app_id, app):
                    _fail(app_id, app, "bundle")
            except Exception as exc:
                _fail(app_id, app, "bundle", exc)

        if AUTO_SUBMIT and any(a not in failed_apps for a in app_id_dic):
            print(f"\n  Waiting {waiting_time}s for Huawei to parse package...")
            time.sleep(waiting_time)

    # Check if can auto submit the app without further config
    if AUTO_SUBMIT:
        release_label = "Full Release" if RELEASE_TYPE == 1 else f"Phased {PHASE_RELEASE_PERCENTAGE}%"
        for app_id in app_id_dic:
            # Never submit an app whose upload or link failed — Huawei would
            # send whatever stale draft is staged for review.
            if app_id in failed_apps:
                print(f"  Submit SKIPPED for {app_id_dic[app_id]['package_name']} — an earlier "
                      f"step failed; fix it and rerun with --regions "
                      f"{app_id_dic[app_id]['region']} --submit")
                continue
            url = "https://connect-api.cloud.huawei.com/api/publish/v2/app-submit?appId=" + app_id + "&releaseType=" + str(RELEASE_TYPE)

            headers = {
                'client_id': client_id,
                'Authorization': 'Bearer ' + token
            }

            payload = json.dumps({
                "state": "RELEASE",
                "phasedReleaseStartTime":PHASE_RELEASE_START_TIME,
                "phasedReleaseEndTime":PHASE_RELEASE_END_TIME,
                "phasedReleasePercent":PHASE_RELEASE_PERCENTAGE,
                "phasedReleaseDescription":PHASE_RELEASE_DESC
            })
            try:
                if RELEASE_TYPE == 1:
                    response = requests.request("POST", url, headers=headers,
                                                timeout=API_TIMEOUT)
                else:
                    response = requests.request("POST", url, headers=headers, data=payload,
                                                timeout=API_TIMEOUT)
                ret = response.json().get("ret", {})
            except Exception as exc:
                ret = {"code": type(exc).__name__, "msg": str(exc)}
            if ret.get("code", 0) == 0:
                print(f"  Submit ({release_label}) .. OK")
            else:
                print(f"  Submit ({release_label}) .. FAILED  {ret.get('code')}: {ret.get('msg')}")
                failed_apps[app_id] = app_id_dic[app_id]["region"]

# ── Outcome ────────────────────────────────────────────────────────────
print()
if failed_apps:
    rerun = ",".join(dict.fromkeys(failed_apps.values()))
    print(f"{len(failed_apps)} app(s) FAILED: {rerun}")
    print(f"Fix and rerun just those with:  python3 huawei_package_upload.py --regions {rerun}"
          + (" --submit" if AUTO_SUBMIT else ""))
    raise SystemExit(1)
print(f"Done — {len(selected_regions)} region(s) processed with no failures.")
