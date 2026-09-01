import argparse
import json
import os
import time

import requests
from pathlib import Path

import release_config
from credentials import HUAWEI_ACCOUNTS


# ── Per-run switches (cycle, version and paths live in release_config.py) ──
UPLOAD_BUNDLE = True       # push the APK
UPLOAD_MATERIALS = True    # push icon, screenshots and listing text
AUTO_SUBMIT = False        # submit for review
# Take the AppGallery app name from listing.txt instead of the fixed name below.
# Those are Google Play titles and currently carry campaign names, so this is off
# unless you actually want the campaign name in the store listing.
USE_LISTING_TITLE = False
# 1 = FULL RELEASE; 3 = PHASED RELEASE
RELEASE_TYPE = 1
PHASE_RELEASE_START_TIME = "2024-07-22T11:00:00+0800"
PHASE_RELEASE_END_TIME = "2024-07-22T11:05:00+0800"
PHASE_RELEASE_PERCENTAGE = "10"
PHASE_RELEASE_DESC = "Phased Release"
suffix = "apk"
waiting_time = 60

STORE = "Huawei"
STORE_DIR = "HUAWEI"

parser = argparse.ArgumentParser(
    description="Upload Shopee builds and marketing materials to Huawei AppGallery.")
release_config.add_release_args(parser)
args = parser.parse_args()
release_config.apply_args(args)

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
            "100914881": {"region": "tw", "bundle": f"shopee-taiwan-release-huawei-{_ver('tw')}.{suffix}", "langs": ["zh-TW"], "package_name": "com.shopee.tw"},
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
    resp = s.send(prep)
    s.close()
    return resp


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

    response = requests.request("POST", url, headers=headers, data=payload)
    response.raise_for_status()
    token = response.json()["access_token"]

    print(f"\n{'─' * 56}")
    print(f"Account  {client_id}  ({len(app_id_dic)} app{'s' if len(app_id_dic) > 1 else ''})")
    print(f"{'─' * 56}")

    # Check if need to update app presence
    if UPLOAD_MATERIALS:
        for app_id in app_id_dic:
            lang_list = app_id_dic.get(app_id).get("langs")
            package_name = app_id_dic.get(app_id).get("package_name")

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

                    # listing.txt titles are Google Play titles, which carry
                    # campaign names ("Shopee 8.8 Merdeka Sale") that go stale
                    # between upload and Huawei's approval. Off by default, so
                    # AppGallery keeps the stable name it has today.
                    app_name = "蝦皮購物 | 花得更少買得更好" if package_name.endswith(".tw") else "Shopee"
                    if USE_LISTING_TITLE:
                        app_name = (lang_listing.get("title") or "").strip() or app_name
                    payload = json.dumps({
                        "lang": lang,
                        "appName": app_name,
                        "appDesc":lang_listing.get("fullDescription"),
                        "briefInfo":lang_listing.get("shortDescription")
                    })

                    response = requests.request("PUT", url, headers=headers, data=payload)
                    response.raise_for_status()
                    # Huawei reports failures as a ret code inside a 200, so
                    # raise_for_status alone used to print OK over any error.
                    ret = response.json().get("ret", {})
                    if ret.get("code", 0) == 0:
                        print(f"  App info ({lang}) .. OK  ({app_name})")
                    else:
                        print(f"  App info ({lang}) .. FAILED  {ret.get('code')}: {ret.get('msg')}")

                    # Update App Icon
                    for filename in os.listdir(_gps_dir(package_name)):
                        f = os.path.join(_gps_dir(package_name), filename)

                        if os.path.isfile(f) and lang in filename and "icon" in filename:

                            file_full_path = Path(f)
                            file_extension = file_full_path.suffix
                            # NOTE: Huawei support suggested ".../upload-url/icon", but that path
                            # returns 404 — it does not exist in the API. The documented endpoint
                            # is the generic /upload-url (same one used for the APK bundle).
                            upload_url_req = "https://connect-api.cloud.huawei.com/api/publish/v2/upload-url?appId=" + app_id + "&suffix=png"
                            print(f"  Icon  {filename}")
                            print(f"    [1] GET {upload_url_req}")

                            headers = {
                                'client_id': client_id,
                                'Authorization': 'Bearer ' + token
                            }

                            response = requests.request("GET", upload_url_req, headers=headers)
                            print(f"    [1] Response {response.status_code}: {response.text[:300]}")
                            response.raise_for_status()
                            check_huawei_api(response)

                            upload_url = response.json()["uploadUrl"]
                            upload_auth_code = response.json()["authCode"]

                            print(f"    [2] POST {upload_url[:80]}")
                            payload = {'authCode': upload_auth_code,
                                       'fileCount': '1',
                                       'parseType': '1'}
                            with open(f, 'rb') as icon_fh:
                                files = [('file', (filename, icon_fh, 'application/octet-stream'))]
                                response = requests.request("POST", upload_url, headers={}, data=payload, files=files)
                            response.raise_for_status()
                            print(f"    [2] Response: {response.text[:500]}")

                            rsp = response.json()["result"]["UploadFileRsp"]
                            if rsp["ifSuccess"] == 1:
                                file_info = rsp["fileInfoList"][0]
                                file_uploaded_url = file_info["fileDestUlr"]
                                file_size = file_info["size"]
                                file_resolution = file_info.get("imageResolution", "")
                                file_sign = file_info.get("imageResolutionSingature", "")

                                # Link App Icon File with App
                                link_url = "https://connect-api.cloud.huawei.com/api/publish/v2/app-file-info?appId=" + app_id
                                link_payload = {
                                    "fileType": 0,
                                    "files": [{
                                        "fileDestUrl": file_uploaded_url,
                                        "size": file_size,
                                        "imageResolution": file_resolution,
                                        "imageResolutionSingature": file_sign,
                                    }],
                                    "lang": lang
                                }
                                print(f"    [3] PUT {link_url}")
                                print(f"    [3] Body: {json.dumps(link_payload)[:500]}")
                                headers = {
                                    'client_id': client_id,
                                    'Content-Type': 'application/json',
                                    'Authorization': 'Bearer ' + token
                                }

                                response = requests.request("PUT", link_url, headers=headers, data=json.dumps(link_payload))
                                response.raise_for_status()
                                print(f"    [3] Response: {response.text[:300]}")
                                ret = response.json().get("ret", {})
                                if ret.get("code", 0) == 0:
                                    print(f"  Icon .. OK ({file_resolution})")
                                else:
                                    print(f"  Icon .. FAILED (code={ret.get('code')}: {ret.get('msg')})")
                            else:
                                print(f"  Icon .. Upload FAILED")

                    #Update App Screenshots
                    for filename in os.listdir(_gps_dir(package_name)):
                        f = os.path.join(_gps_dir(package_name), filename)

                        if os.path.isfile(f) and lang in filename and "phoneScreenshots" in filename:
                            screenshot_local_file_list.append(f)

                    screenshot_local_file_list.sort()

                    failed_screenshots = []
                    total_screenshots = len(screenshot_local_file_list)
                    print(f"  Screenshots  uploading {total_screenshots} files...", end='', flush=True)
                    for i, screenshot_local_file in enumerate(screenshot_local_file_list):
                        file_full_path = Path(screenshot_local_file)
                        file_extension = file_full_path.suffix
                        # Generic /upload-url endpoint (same as icon and APK bundle). There is no
                        # typed /upload-url/screenshot path in the API.
                        url = "https://connect-api.cloud.huawei.com/api/publish/v2/upload-url?appId=" + app_id + "&suffix=" + file_extension.replace(".", "")

                        headers = {
                            'client_id': client_id,
                            'Authorization': 'Bearer ' + token
                        }

                        response = requests.request("GET", url, headers=headers)
                        if response.status_code != 200:
                            print(f"\n    Screenshot upload-url GET {response.status_code}: {response.text[:300]}")
                        response.raise_for_status()
                        check_huawei_api(response)

                        upload_url = response.json()["uploadUrl"]
                        upload_auth_code = response.json()["authCode"]

                        # parseType=1 makes Huawei parse the image and return its real
                        # imageResolution + imageResolutionSingature, which the link step
                        # below requires. (Previously parseType=0 with a fabricated signature,
                        # which caused the link to fail.)
                        payload = {'authCode': upload_auth_code,
                                   'fileCount': '1',
                                   'parseType': '1'}
                        with open(screenshot_local_file, 'rb') as shot_fh:
                            files = [('file', (file_full_path.name, shot_fh, 'application/octet-stream'))]
                            response = requests.request("POST", upload_url, headers={}, data=payload, files=files)

                        response.raise_for_status()

                        rsp = response.json()["result"]["UploadFileRsp"]
                        if rsp["ifSuccess"] == 1:
                            file_info = rsp["fileInfoList"][0]
                            dest_url = file_info["fileDestUlr"]
                            screenshot_remote_file_list.append({
                                "fileDestUrl": dest_url,
                                "size": file_info["size"],
                                "imageResolution": file_info.get("imageResolution", ""),
                                "imageResolutionSingature": file_info.get("imageResolutionSingature", ""),
                            })
                        else:
                            failed_screenshots.append(file_full_path.name)

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

                    response = requests.request("PUT", url, headers=headers, data=payload)
                    response.raise_for_status()
                    ok_count = len(screenshot_remote_file_list)
                    ret = response.json().get("ret", {})
                    if ret.get("code", 0) == 0:
                        link_status = "OK" if not failed_screenshots else f"FAILED: {', '.join(failed_screenshots)}"
                    else:
                        link_status = f"FAILED (code={ret.get('code')}: {ret.get('msg')})"
                    print(f'\r  Screenshots  {ok_count}/{total_screenshots} linked .. {link_status}{" " * 10}')


    # App bundle upload
    if UPLOAD_BUNDLE:
        for app_id in app_id_dic:
            file_name = app_id_dic.get(app_id).get("bundle")
            full_file_path = _bundle_path(file_name)
            package_name = app_id_dic.get(app_id).get("package_name")

            if not UPLOAD_MATERIALS:
                print(f"\n[{app_id}] {package_name}")

            url = "https://connect-api.cloud.huawei.com/api/publish/v2/upload-url?appId=" + app_id + "&suffix=" + suffix

            headers = {
                'client_id': client_id,
                'Authorization': 'Bearer ' + token
            }

            response = requests.request("GET", url, headers=headers)
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

                response = requests.request("PUT", url, headers=headers, data=payload)
                response.raise_for_status()
                # A failed link is reported as a ret code inside a 200, so this
                # used to print OK for a bundle that never attached to the app.
                ret = response.json().get("ret", {})
                if ret.get("code", 0) == 0:
                    print(f'\r          Upload & link .. OK{" " * 30}')
                else:
                    print(f'\r          Link FAILED  {ret.get("code")}: {ret.get("msg")}{" " * 10}')
            else:
                print(f'\r          Upload FAILED{" " * 30}')

        if AUTO_SUBMIT:
            print(f"\n  Waiting {waiting_time}s for Huawei to parse package...")
            time.sleep(waiting_time)

    # Check if can auto submit the app without further config
    if AUTO_SUBMIT:
        release_label = "Full Release" if RELEASE_TYPE == 1 else f"Phased {PHASE_RELEASE_PERCENTAGE}%"
        for app_id in app_id_dic:
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
            if RELEASE_TYPE == 1:
                response = requests.request("POST", url, headers=headers)
            elif RELEASE_TYPE == 3:
                response = requests.request("POST", url, headers=headers, data=payload)

            ret = response.json().get("ret", {})
            if ret.get("code", 0) == 0:
                print(f"  Submit ({release_label}) .. OK")
            else:
                print(f"  Submit ({release_label}) .. FAILED  {ret.get('code')}: {ret.get('msg')}")
