# Shopee 3rd-Party Store Release Scripts

Tooling to ship Shopee Android builds to third-party app stores — Huawei
AppGallery and Samsung Galaxy Store — using marketing materials from the
internal App Release platform (RMS).

## Scripts

| Script | What it does |
|---|---|
| `release_config.py` | Shared config: cycle, version, paths, regions. Edit `CYCLE` and `VERSION` here once per release. Not run directly. |
| `retrieve_rms_materials.py` | Downloads icons, screenshots and listing text for a cycle from RMS into the release folder. |
| `huawei_package_upload.py` | Uploads APKs + materials to Huawei AppGallery (8 regions, 2 accounts). |
| `samsung_package_upload.py` | Uploads APKs + materials to Samsung Galaxy Store (7 regions). |

## One-time setup

1. Python 3 with: `pip3 install -r requirements.txt`
2. Create `credentials.py` next to the scripts (gitignored — never commit it):

   ```python
   RMS_AUTH_COOKIE = "..."   # RMS_AUTH cookie from browser DevTools on
                             # app-release.shopee.io; a JWT valid ~30 days

   HUAWEI_ACCOUNTS = [
       {"client_id": "...", "client_secret": "..."},  # main account (ID/MY/VN/TW/PH/SG/TH)
       {"client_id": "...", "client_secret": "..."},  # MX account
   ]

   SAMSUNG_SERVICE_ACCOUNT_ID = "..."
   SAMSUNG_PRIVATE_KEY = """-----BEGIN PRIVATE KEY-----
   ...
   -----END PRIVATE KEY-----"""
   ```

3. Release folders live at `~/Documents/Python/3rdPartyApp/<cycle>/`, with the
   Jenkins APKs in `HUAWEI/` and `SAMSUNG/` subfolders (build-number prefixes
   like `26557-shopee-indonesia-release-huawei-3.80.37.apk` are handled; the
   highest build number wins).

## Doing a release

```bash
# 1. Point the scripts at the release: edit CYCLE and VERSION in release_config.py

# 2. Pull marketing materials from RMS into the release folder
python3 retrieve_rms_materials.py --dry-run   # see what has publishable material
python3 retrieve_rms_materials.py

# 3. Preflight each store — checks APKs and materials, uploads nothing
python3 huawei_package_upload.py --dry-run
python3 samsung_package_upload.py --dry-run

# 4. Upload for real
python3 huawei_package_upload.py
python3 samsung_package_upload.py
```

Every script accepts the same overrides for a one-off run without editing
`release_config.py`:

```
--cycle 3.81          release cycle
--version 3.81.20     build version
--regions id,th       comma-separated region codes (default: all the store carries)
--materials <dir>     materials folder name or path
--dry-run             print the plan and preflight result, upload nothing
```

The uploaders additionally take `--submit` / `--no-submit` to override each
script's `AUTO_SUBMIT` default. Combined with `--regions` this gives
per-region submit control:

```bash
python3 samsung_package_upload.py --no-submit               # upload all, submit nothing
python3 samsung_package_upload.py --regions id,th --submit  # then submit just id+th
```

An unknown region code aborts the run; a region the store simply doesn't carry
is reported and skipped. Preflight runs on every real run too — if any selected
region is missing an APK, icon, screenshots or listing text, nothing at all is
uploaded.

## Per-store notes

- **Huawei**: `AUTO_SUBMIT` is **off** by default (top of the script) — uploads
  land as drafts to submit manually. Store titles are pinned in the script
  (`USE_LISTING_TITLE = False`) because listing titles carry campaign names.
- **Samsung**: `AUTO_SUBMIT` is **on** by default — a real run submits for
  review. Only each app's `default_language` listing is pushed.
- **RMS cookie expired?** A run stops with "Unauthorized". Log in to
  app-release.shopee.io, copy the fresh `RMS_AUTH` cookie from DevTools into
  `credentials.py`.

## GitHub mirror

This repo's history contains old credentials and is never pushed to GitHub.
Instead `sync_github.sh` pushes a clean, history-free snapshot of `main` to the
public mirror; a `post-commit` hook runs it automatically on every commit.
