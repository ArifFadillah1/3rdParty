# Shopee 3rd-Party Store Release Scripts

Command-line tooling that ships Shopee Android builds to the third-party app
stores that are not Google Play — **Huawei AppGallery** and **Samsung Galaxy
Store** — using marketing materials pulled from Shopee's internal App Release
platform (**RMS**).

A release is three steps: pull materials from RMS, preflight each store, upload.
Everything is driven by two values (`CYCLE` and `VERSION`) in one file.

## Capabilities

- Download per-region icons, screenshots, feature graphics and listing text for
  a release cycle from RMS, in a folder layout both uploaders consume unchanged.
- Write extra 480x854 screenshot copies for the OPPO and Transsion manual
  submission forms (those stores have no public API).
- Upload APKs and store listings to Huawei AppGallery across 8 regions spanning
  2 developer accounts.
- Upload APKs and store listings to Samsung Galaxy Store across 7 regions.
- Preflight every run (not just `--dry-run`): a missing APK, icon, screenshot or
  description stops the run before anything is uploaded.
- Isolate failures per app, so one region's failure does not abort the release;
  the run ends non-zero and prints a ready-made `--regions ...` rerun command.
- Optionally submit for review (`--submit`), skipping any app whose upload failed.

## Architecture

Plain Python 3 scripts, no package, no framework. `release_config.py` is a
shared module the other three import; it holds the per-release values and the
pure logic (path resolution, region resolution, preflight) that both uploaders
share.

| File | Role |
|---|---|
| `release_config.py` | Shared config and helpers. Edit `CYCLE` and `VERSION` here once per release. Not executable. |
| `retrieve_rms_materials.py` | Downloads marketing materials for a cycle from RMS into the release folder. Has a `main()` and an `if __name__ == "__main__"` guard. |
| `huawei_package_upload.py` | Uploads to Huawei AppGallery. Runs at module level (see Limitations). |
| `samsung_package_upload.py` | Uploads to Samsung Galaxy Store. Runs at module level (see Limitations). |
| `sync_github.sh` | Pushes a history-free snapshot of `main` to the public GitHub mirror, refusing if the tree looks like it contains a secret. |
| `requirements.txt` | Runtime dependencies. |

### Release folder layout

Release folders live at `<BASE_DIR>/<CYCLE>/`, where `BASE_DIR` defaults to
`~/Documents/Python/3rdPartyApp`. Jenkins APKs go in the store subfolder:

```
~/Documents/Python/3rdPartyApp/3.83/
    HUAWEI/   26557-shopee-indonesia-release-huawei-3.83.31.apk
    SAMSUNG/  26557-shopee-indonesia-release-samsung-3.83.31.apk
    RMS_Materials_3.83/
        com.shopee.id/
            listing.txt
            id_icon_1.png
            id_phoneScreenshots_1.jpeg
            id_featureGraphic_1.jpeg
    Manual_480x854_3.83/      # OPPO / Transsion, written by the retrieval step
```

Build-number prefixes are handled: when several builds match a bundle name the
**highest build number wins**. An un-prefixed file at the release root or in the
store subfolder is used as-is.

## Installation

Requires Python 3 (developed and validated against 3.11).

```bash
pip3 install -r requirements.txt
```

## Configuration

### `release_config.py`

Edit these per release:

| Name | Meaning |
|---|---|
| `CYCLE` | Release cycle. Names the release folder and drives the RMS query. |
| `VERSION` | Build version the stores see. |
| `REGIONS` | `None` for every region the store carries, or a list of region codes. |
| `BASE_DIR` | Parent of the release folders. |
| `MATERIALS_DIR` | `None` to auto-discover the newest materials folder, or a folder name / absolute path. |
| `DRY_RUN` | Set by `--dry-run`; not normally edited. |
| `ALL_REGIONS` | Every region Shopee ships to. Exists only to tell a typo apart from a region a store does not carry. |

`CYCLE` and `VERSION` are deliberately independent — cycle 3.82 shipped version
3.81.29 — so neither is derived from the other.

### `credentials.py` (gitignored, never committed)

No OS environment variables are read. Credentials are module attributes in a
`credentials.py` placed next to the scripts. Required **names** only:

| Name | Used by | Shape |
|---|---|---|
| `RMS_AUTH_COOKIE` | `retrieve_rms_materials.py` | The `RMS_AUTH` cookie from a logged-in browser session on the RMS web app. A JWT, valid roughly 30 days. |
| `HUAWEI_ACCOUNTS` | `huawei_package_upload.py` | A list of two dicts, each with the keys `client_id` and `client_secret`. Index 0 is the main account (ID/MY/VN/TW/PH/SG/TH); index 1 is the MX account. |
| `SAMSUNG_SERVICE_ACCOUNT_ID` | `samsung_package_upload.py` | Galaxy Store service account identifier. |
| `SAMSUNG_PRIVATE_KEY` | `samsung_package_upload.py` | The RSA private key for that service account, as a string. Used to sign the auth JWT with RS256. |

Never commit this file or paste its values anywhere. `sync_github.sh` refuses to
push if a credential-shaped string appears in the tree being pushed.

### Per-script switches

Edited at the top of each uploader, overridable per run by flags:

| Switch | `huawei_package_upload.py` | `samsung_package_upload.py` |
|---|---|---|
| `UPLOAD_BUNDLE` | `True` | `True` |
| `UPLOAD_MATERIALS` | `True` | `True` |
| `AUTO_SUBMIT` | `False` | `False` |
| `USE_LISTING_TITLE` | `True` — take the AppGallery app name from `listing.txt` | n/a |
| `RELEASE_TYPE` | `1` (full release); `3` is phased | n/a |
| `version_overrides` | empty | pins `th` to `3.83.36` |

`version_overrides` maps a region to a different build version, for when one
region ships a build of its own.

## Running a release

```bash
# 1. Point the scripts at the release: edit CYCLE and VERSION in release_config.py

# 2. Pull marketing materials from RMS into the release folder
python3 retrieve_rms_materials.py --dry-run    # which regions have publishable material
python3 retrieve_rms_materials.py

# 3. Preflight each store — checks APKs and materials, uploads nothing
python3 huawei_package_upload.py --dry-run
python3 samsung_package_upload.py --dry-run

# 4. Upload for real
python3 huawei_package_upload.py
python3 samsung_package_upload.py
```

### Shared flags (both uploaders)

Added by `release_config.add_release_args()`, so both uploaders accept exactly
these:

```
--cycle CYCLE          release cycle              (default: CYCLE in release_config.py)
--version VERSION      build version              (default: VERSION in release_config.py)
--regions REGIONS      comma-separated region codes (default: every region the store carries)
--materials MATERIALS  materials folder name or path (default: newest in the release folder)
--dry-run              print the resolved paths and region plan, then exit without uploading
--submit               submit for review after upload (overrides AUTO_SUBMIT)
--no-submit            upload only, do not submit
```

`--submit` and `--no-submit` are mutually exclusive. Combined with `--regions`
this gives per-region submit control:

```bash
python3 samsung_package_upload.py --no-submit                # upload all, submit nothing
python3 samsung_package_upload.py --regions id,th --submit   # then submit just id and th
```

### `retrieve_rms_materials.py` flags

```
release_cycle          positional, optional, e.g. 3.76 or 3.77.x1 (default: CYCLE)
--cycle CYCLE_OPT      same as the positional argument
--regions REGIONS      comma-separated region codes (default: every region with material)
--out OUT              parent output directory (default: this release's folder)
--include-draft        also download materials still in draft (excluded by default)
--no-resize            skip the 480x854 OPPO/Transsion screenshot copies
--dry-run              report which regions have publishable material, download nothing
```

```bash
python3 retrieve_rms_materials.py 3.76                     # a different cycle, all regions
python3 retrieve_rms_materials.py 3.76 --regions id,th     # specific regions
python3 retrieve_rms_materials.py 3.77.x1 --out ~/Downloads
```

### Region handling

An **unknown** region code aborts the run before anything is uploaded (so a typo
like `tv` for `tw` can never look like a successful release). A region that is a
real Shopee region but which the store does not carry (`mx` on Samsung) is
reported and skipped.

| Store | Regions carried |
|---|---|
| Huawei | `id` `my` `vn` `tw` `ph` `sg` `th` (main account), `mx` (MX account) |
| Samsung | `id` `th` `my` `sg` `ph` `tw` `vn` |

## Public functions and classes

### `release_config.py`

| Name | Signature | Behaviour |
|---|---|---|
| `root()` | `() -> str` | The release folder for the current cycle: `BASE_DIR/CYCLE`. |
| `find_materials(release_root=None)` | `(str\|None) -> str` | Absolute path to this release's materials folder. Honours `MATERIALS_DIR` (name or absolute path); otherwise picks the newest directory matching `RMS_Materials_*` or `GPS_Content_Retrieval_*`, printing a `NOTE` naming the ones it ignored. **Exits** via `sys.exit` when `MATERIALS_DIR` points at a missing folder, or when no candidate exists. |
| `bundle_path(store_dir, bundle_name, release_root=None)` | `(str, str, str\|None) -> str` | Locates a build. Tries the exact name at the release root, then in `store_dir`, then build-number-prefixed matches (`*-<bundle_name>`) where the **highest numeric prefix wins**, printing a `NOTE` when several match. **Raises `FileNotFoundError`** when nothing matches. |
| `version_code(version)` | `(str) -> str` | Samsung versionCode: the version with dots stripped (`"3.80.37"` -> `"38037"`). Must equal the APK manifest's own versionCode. |
| `resolve_regions(supported, store)` | `(iterable, str) -> list[str]` | Decides which regions to process. `REGIONS is None` returns every supported region, sorted; otherwise keeps the requested order, skipping and reporting regions the store does not carry. Prints the plan. **Exits** on an unknown region code, or when nothing requested is on this store. |
| `add_release_args(parser)` | `(ArgumentParser) -> ArgumentParser` | Adds `--cycle`, `--version`, `--regions`, `--materials`, `--dry-run`. |
| `apply_args(args)` | `(Namespace) -> None` | **Mutates the module globals** `CYCLE`, `VERSION`, `REGIONS`, `MATERIALS_DIR`, `DRY_RUN` from parsed args. Only non-empty values override; `--regions` is split on commas and lowercased. |
| `print_header(store, materials_dir, flags="")` | `(str, str, str) -> None` | Prints the run banner (store, cycle, version, paths, flags). |
| `check_package(package_dir, languages, min_screenshots=1, max_screenshots=None)` | `-> list[str]` | Validates one package's materials; returns a list of problems, empty when ready. Checks that `listing.txt` exists and parses, that each demanded language is present with a non-blank `fullDescription`, and that an icon and enough screenshots exist. Asset lookup mirrors the uploaders (substring match on the language code and on `icon` / `phoneScreenshots`). |
| `report_preflight(findings, store, verbose=False)` | `(list[tuple], str, bool) -> bool` | Takes `(region, package, apk_or_None, problems)` rows. Returns `True` when every region is ready; otherwise prints each blocker and a narrowed `--regions` suggestion. `verbose=True` prints the full per-region table. |

### `retrieve_rms_materials.py`

| Name | Behaviour |
|---|---|
| `api(path, method="GET", params=None, body=None)` | One RMS call against `BASE_URL`, authenticated with the `RMS_AUTH` cookie. **Exits** with an "Unauthorized" hint on HTTP 401; raises on other HTTP errors; raises `RuntimeError` on a non-zero `error` field. Returns the `data` payload. |
| `material_list()` | The RMS dashboard view: one row per region, most recent material only. Reliable for enumerating regions, **not** for asking whether a cycle exists. |
| `all_regions()` | Sorted set of region codes seen in the dashboard view. |
| `recent_cycles()` | Cycles visible in the dashboard view — a hint for typos, not exhaustive. |
| `materials_for(release_cycle, region)` | Materials for one cycle and region, memoised in `_materials_cache`. |
| `discover_regions(release_cycle)` | Regions holding material for a cycle. Queries per region, because the dashboard list only carries each region's newest material. |
| `image_entries(groups)` | Flattens RMS's nested `material_image_url` groups into a flat list. |
| `fetch(url)` | GETs an image URL (re-quoted) with a 120s timeout; returns raw bytes. |
| `sniff_ext(data, fallback)` | Extension from magic bytes — `.png`, `.jpeg`, `.gif`, `.webp`, else `fallback`. JPEG is spelled `jpeg`, matching the legacy Google Play script. |
| `resize_screenshot(data, size=MANUAL_SCREENSHOT_SIZE)` | Scales and centre-crops to exactly 480x854 preserving aspect ratio. Returns `(bytes, extension)`. PNG stays PNG; everything else becomes JPEG at quality 90. Requires Pillow. |
| `save_material(item, package_dir, resized_dir=None)` | **Writes** one language's icon, screenshots and feature graphic into `package_dir`, plus resized screenshot copies into `resized_dir` when given. Returns the listing dict for `listing.txt`. |
| `main()` | CLI entry point. Resolves the cycle and regions, filters to `play_store` material in a publishable status, de-duplicates by language, downloads, and writes `listing.txt` per package. |

Material statuses treated as publishable: `submitted`, `scheduled`, `approved`,
`released`. `draft` is excluded unless `--include-draft` is passed.

### `huawei_package_upload.py`

| Name | Behaviour |
|---|---|
| `check_huawei_api(response)` | Raises `RuntimeError` when the response body's `ret.code` is non-zero. Huawei reports failures inside HTTP 200s, so this is what makes an error visible. |
| `_UploadProgress` | File-like wrapper over prepared-request body bytes that prints an inline progress bar as bytes are read. |
| `_upload_with_progress(upload_url, payload, fh, filename, prefix)` | POSTs a multipart upload with that progress bar. Returns the response. |
| `_obs_upload(app_id, api_headers, path)` | Uploads one file via Huawei's OBS flow and returns its `objectId` for binding. Huawei moved materials to this flow; the legacy `/upload-url` + signature flow now fails image links with `204144641 Failed to verifySignature`. |
| `upload_app_materials(app_id, app)` | Pushes one app's listing text, icon and screenshots. Returns `False` if any step reported FAILED. Defined only when `UPLOAD_MATERIALS` is on. |
| `upload_app_bundle(app_id, app)` | Uploads and links one APK. Returns `False` if any step reported FAILED. Defined only when `UPLOAD_BUNDLE` is on. |
| `_fail(app_id, app, stage, exc=None)` | Records an app as failed so later stages skip it and auto-submit refuses it. |

### `samsung_package_upload.py`

| Name | Behaviour |
|---|---|
| `samsung_json(response, path, what)` | Walks `path` into a Samsung JSON response, raising `RuntimeError` with the server's own message rather than a `KeyError` several lines later. |
| `_BodyProgressReader` | Wraps prepared-request body bytes and advances a `tqdm` bar as bytes go over the network. |
| `upload_file_to_samsung(full_path, label=None, pbar=None)` | Creates an upload session, uploads the file, returns its `fileKey`. Pass a shared `tqdm` instance as `pbar` to update it instead of creating a new bar. |
| `log_update_result(response, action="Content update")` | Prints the outcome and returns `True` only when the call succeeded. |
| `update_app_metadata(content_id, app_title, default_language, long_description, short_description, icon_key, screenshot_key_list)` | POSTs the listing update. Returns the response. |
| `update_app_binary(content_id, build_name, v_code, v_no, package_name, build_key)` | POSTs the binary record (versionCode, versionName, package name, file key). Returns the response. |
| `process_app(idx, app_content_id, app_content)` | Runs one app end to end: images, APK, metadata, binary. The binary update is skipped when the metadata update failed. |

Samsung pushes exactly one language per app — the app's `default_language`,
mapped to a Google-style code by `lang_mapping` (`IND`->`id`, `ENG`->`en-US`,
`002`->`zh-TW`, `VIE`->`vi`, `THA`->`th`).

## Integrations

**RMS — Shopee App Release platform** (`https://app-release.shopee.io/api/v1/native_material`)
Authenticated with the `RMS_AUTH` cookie. Endpoints used:
`get_material_list` (POST, dashboard view) and
`get_android_material_info_by_release_cycle` (GET, per cycle and region).
Only `play_store` material is used — RMS also holds iOS material whose
screenshots are the wrong size for Android stores.

**Huawei AppGallery Connect** (`https://connect-api.cloud.huawei.com`)
OAuth2 client-credentials token from `/api/oauth2/v1/token`, then
`/api/publish/v2/upload-url/for-obs` (pre-signed PUT + objectId),
`/api/publish/v2/app-file-info` (bind icon `fileType 0`, screenshots
`fileType 2`, APK `fileType 5`), `/api/publish/v2/app-language-info` (listing
text and app name), `/api/publish/v2/upload-url` (APK upload) and
`/api/publish/v2/app-submit`. Failures arrive as a `ret.code` inside an HTTP
200, so every call checks that field.

**Samsung Galaxy Store** (`https://devapi.samsungapps.com`, uploads via
`https://seller.samsungapps.com`)
RS256-signed JWT (`iss` = service account id, scope `publishing`, 10-minute
expiry) exchanged at `/auth/accessToken`, then `/seller/createUploadSessionId`,
`https://seller.samsungapps.com/galaxyapi/fileUpload`, `/seller/contentInfo`,
`/seller/contentUpdate`, `/seller/v2/content/binary` and `/seller/contentSubmit`.

Network timeouts are `(10, 60)` seconds for API calls and `(10, 900)` for
uploads, since both stores only answer once the whole file has been received.

## Dependencies

| Package | Role |
|---|---|
| `requests` | Every HTTP call to RMS, Huawei and Samsung, including the prepared-request upload path that drives the progress bars. |
| `PyJWT` | Signs the Samsung auth JWT (`RS256`). |
| `cryptography` | Not imported directly — PyJWT needs it to sign RS256. |
| `tqdm` | Progress bars in the Samsung uploader. |
| `Pillow` | 480x854 screenshot resizing in the retrieval script. Imported defensively; only required unless `--no-resize` is passed. |

## GitHub mirror

This repo's Git history contains credentials from before they were consolidated
into `credentials.py`, so `main` is never pushed to the public mirror. Instead
`sync_github.sh` builds a single history-free snapshot commit on the
`github-main` branch whose tree is exactly `main`'s tree, and pushes that. A
`post-commit` hook runs it on every commit to `main`.

Before pushing, the script scans the tree being pushed for credential-shaped
strings (private-key headers, client secrets, basic-auth blobs, JWTs, AWS keys)
and refuses to push on a hit. If `gitleaks` is installed it runs that too, over
an export of exactly that tree. A finding only skips the sync; the local commit
is untouched.

```bash
./sync_github.sh
```

## Known limitations

- **The two uploaders have no `if __name__ == "__main__"` guard.** They execute
  at module level, so importing either one runs a release. Only
  `retrieve_rms_materials.py` is import-safe.
- **OPPO and Transsion have no public API.** Submit manually in each store's web
  form, using the 480x854 screenshots in `Manual_480x854_<cycle>/`. That folder
  is deliberately named so the uploaders can never auto-discover it; originals
  are untouched.
- **The RMS cookie expires (~30 days).** A run stops with an "Unauthorized"
  message; refresh `RMS_AUTH_COOKIE` from a logged-in browser session.
- **`version_code()` is only monotonic while segment digit counts stay stable** —
  `"3.81.5"` yields `3815`, which is numerically below `38037`. This is
  acceptable only because it must mirror the APK manifest's own versionCode,
  which the build system derives the same way. If that scheme changes, this must
  change with it.
- **Samsung pushes only the app's `default_language` listing**, not every
  language present in `listing.txt`.
- **Huawei region coverage is split across two developer accounts**; a token
  failure on one account skips only that account's apps.
- `featureGraphic` files are downloaded from RMS but ignored by both uploaders,
  because their names contain neither `icon` nor `phoneScreenshots`.
- Region codes are matched against asset filenames by **substring**, so an asset
  naming scheme that embeds one language code inside another would mismatch.
