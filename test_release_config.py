"""Unit tests for release_config.py, plus sniff_ext from retrieve_rms_materials.

Run with:  python3 -m pytest

Pure-function tests only — no network and no credentials.py needed (a stub
credentials module is injected when the real one is absent, so these run on
a fresh public clone too).
"""
import argparse
import json
import os
import sys
import types

import pytest

import release_config

# retrieve_rms_materials imports credentials at module level; stub it out
# when running without a real credentials.py.
try:
    import credentials  # noqa: F401
except ImportError:
    stub = types.ModuleType("credentials")
    stub.RMS_AUTH_COOKIE = "test-cookie"
    sys.modules["credentials"] = stub
from retrieve_rms_materials import sniff_ext, resize_screenshot


# ── helpers ──────────────────────────────────────────────────────────

@pytest.fixture
def release_root(tmp_path, monkeypatch):
    """A fake release folder wired into release_config's globals."""
    monkeypatch.setattr(release_config, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(release_config, "CYCLE", "9.99")
    monkeypatch.setattr(release_config, "MATERIALS_DIR", None)
    root = tmp_path / "9.99"
    root.mkdir()
    return root


def write_package(pkg_dir, lang="id", icons=1, screenshots=2, listing_langs=None):
    """A materials package folder as retrieve_rms_materials.py would write it."""
    pkg_dir.mkdir(parents=True, exist_ok=True)
    listing_langs = listing_langs if listing_langs is not None else [lang]
    (pkg_dir / "listing.txt").write_text(json.dumps(
        {"listings": [{"language": l, "title": "T"} for l in listing_langs]}))
    for n in range(1, icons + 1):
        (pkg_dir / f"{lang}_icon_{n}.png").write_bytes(b"x")
    for n in range(1, screenshots + 1):
        (pkg_dir / f"{lang}_phoneScreenshots_{n}.jpeg").write_bytes(b"x")
    return pkg_dir


# ── root ─────────────────────────────────────────────────────────────

def test_root_joins_base_dir_and_cycle(release_root):
    assert release_config.root() == str(release_root)


# ── bundle_path ──────────────────────────────────────────────────────

def test_bundle_path_exact_name_at_release_root(release_root):
    apk = release_root / "shopee-id-release-9.99.1.apk"
    apk.write_bytes(b"x")
    assert release_config.bundle_path("HUAWEI", apk.name, str(release_root)) == str(apk)


def test_bundle_path_exact_name_in_store_dir(release_root):
    apk = release_root / "HUAWEI" / "shopee-id-release-9.99.1.apk"
    apk.parent.mkdir()
    apk.write_bytes(b"x")
    assert release_config.bundle_path("HUAWEI", apk.name, str(release_root)) == str(apk)


def test_bundle_path_highest_build_number_wins(release_root):
    store = release_root / "SAMSUNG"
    store.mkdir()
    name = "shopee-id-release-9.99.1.apk"
    for prefix in ("26557", "9999", "26600"):  # 9999 < 26557 numerically, not lexically
        (store / f"{prefix}-{name}").write_bytes(b"x")
    chosen = release_config.bundle_path("SAMSUNG", name, str(release_root))
    assert os.path.basename(chosen) == f"26600-{name}"


def test_bundle_path_missing_raises(release_root):
    with pytest.raises(FileNotFoundError):
        release_config.bundle_path("HUAWEI", "nope.apk", str(release_root))


# ── find_materials ───────────────────────────────────────────────────

def test_find_materials_none_exits(release_root):
    with pytest.raises(SystemExit):
        release_config.find_materials(str(release_root))


def test_find_materials_accepts_both_naming_patterns(release_root):
    for name in ("RMS_Materials_9.99", "GPS_Content_Retrieval_2026-01-01"):
        folder = release_root / name
        folder.mkdir()
        assert release_config.find_materials(str(release_root)) == str(folder)
        folder.rmdir()


def test_find_materials_newest_of_several_wins(release_root):
    old = release_root / "RMS_Materials_old"
    new = release_root / "GPS_Content_Retrieval_new"
    old.mkdir()
    new.mkdir()
    os.utime(old, (1, 1))  # force old mtime
    assert release_config.find_materials(str(release_root)) == str(new)


def test_find_materials_ignores_plain_files(release_root):
    (release_root / "RMS_Materials_file").write_text("not a folder")
    with pytest.raises(SystemExit):
        release_config.find_materials(str(release_root))


def test_find_materials_explicit_override(release_root, monkeypatch):
    chosen = release_root / "MyMaterials"
    chosen.mkdir()
    monkeypatch.setattr(release_config, "MATERIALS_DIR", "MyMaterials")
    assert release_config.find_materials(str(release_root)) == str(chosen)
    monkeypatch.setattr(release_config, "MATERIALS_DIR", "DoesNotExist")
    with pytest.raises(SystemExit):
        release_config.find_materials(str(release_root))


# ── resolve_regions ──────────────────────────────────────────────────

def test_resolve_regions_default_is_all_supported_sorted(monkeypatch):
    monkeypatch.setattr(release_config, "REGIONS", None)
    assert release_config.resolve_regions({"th", "id", "my"}, "Test") == ["id", "my", "th"]


def test_resolve_regions_keeps_requested_order_and_skips_uncarried(monkeypatch):
    monkeypatch.setattr(release_config, "REGIONS", ["th", "mx", "id"])
    # mx is a real Shopee region this store does not carry: skipped, not fatal
    assert release_config.resolve_regions({"id", "th"}, "Test") == ["th", "id"]


def test_resolve_regions_typo_aborts(monkeypatch):
    monkeypatch.setattr(release_config, "REGIONS", ["id", "tv"])  # tv: typo for tw
    with pytest.raises(SystemExit):
        release_config.resolve_regions({"id", "tw"}, "Test")


def test_resolve_regions_nothing_selected_aborts(monkeypatch):
    monkeypatch.setattr(release_config, "REGIONS", ["mx"])
    with pytest.raises(SystemExit):
        release_config.resolve_regions({"id", "th"}, "Test")


# ── apply_args ───────────────────────────────────────────────────────

def _args(**kw):
    defaults = dict(cycle=None, version=None, regions=None, materials=None, dry_run=False)
    defaults.update(kw)
    return argparse.Namespace(**defaults)


@pytest.fixture
def config_globals(monkeypatch):
    """Snapshot the globals apply_args mutates, so tests can't leak state."""
    for name in ("CYCLE", "VERSION", "REGIONS", "MATERIALS_DIR", "DRY_RUN"):
        monkeypatch.setattr(release_config, name, getattr(release_config, name))


def test_apply_args_overrides(config_globals):
    release_config.apply_args(_args(cycle="3.81", version="3.81.20",
                                    regions=" ID, th ,", materials="M", dry_run=True))
    assert release_config.CYCLE == "3.81"
    assert release_config.VERSION == "3.81.20"
    assert release_config.REGIONS == ["id", "th"]
    assert release_config.MATERIALS_DIR == "M"
    assert release_config.DRY_RUN is True


def test_apply_args_defaults_untouched(config_globals):
    before = (release_config.CYCLE, release_config.VERSION, release_config.REGIONS)
    release_config.apply_args(_args())
    assert (release_config.CYCLE, release_config.VERSION, release_config.REGIONS) == before
    assert release_config.DRY_RUN is False


# ── check_package ────────────────────────────────────────────────────

def test_check_package_ready(tmp_path):
    pkg = write_package(tmp_path / "com.shopee.id")
    assert release_config.check_package(str(pkg), ["id"]) == []


def test_check_package_missing_listing(tmp_path):
    pkg = tmp_path / "com.shopee.id"
    pkg.mkdir()
    problems = release_config.check_package(str(pkg), ["id"])
    assert problems and "listing.txt" in problems[0]


def test_check_package_unreadable_listing(tmp_path):
    pkg = tmp_path / "com.shopee.id"
    pkg.mkdir()
    (pkg / "listing.txt").write_text("{not json")
    problems = release_config.check_package(str(pkg), ["id"])
    assert problems and "unreadable" in problems[0]


def test_check_package_language_not_in_listing(tmp_path):
    pkg = write_package(tmp_path / "com.shopee.th", lang="th", listing_langs=["en-US"])
    problems = release_config.check_package(str(pkg), ["th"])
    assert any("not in listing.txt" in p for p in problems)


def test_check_package_missing_icon_and_screenshots(tmp_path):
    pkg = write_package(tmp_path / "com.shopee.id", icons=0, screenshots=0)
    problems = release_config.check_package(str(pkg), ["id"])
    assert any("no icon" in p for p in problems)
    assert any("screenshot" in p for p in problems)


def test_check_package_screenshot_bounds(tmp_path):
    pkg = write_package(tmp_path / "com.shopee.id", screenshots=2)
    assert release_config.check_package(str(pkg), ["id"], min_screenshots=3)
    assert release_config.check_package(str(pkg), ["id"], max_screenshots=1)
    assert release_config.check_package(str(pkg), ["id"],
                                        min_screenshots=1, max_screenshots=8) == []


def test_check_package_only_demanded_languages_checked(tmp_path):
    # en-US listed but has no files; asking only for th must not flag en-US
    pkg = write_package(tmp_path / "com.shopee.th", lang="th",
                        listing_langs=["th", "en-US"])
    assert release_config.check_package(str(pkg), ["th"]) == []
    assert release_config.check_package(str(pkg), ["th", "en-US"])


# ── sniff_ext (retrieve_rms_materials) ───────────────────────────────

@pytest.mark.parametrize("data,expected", [
    (b"\x89PNG\r\n\x1a\n" + b"0" * 8, ".png"),
    (b"\xff\xd8\xff" + b"0" * 8, ".jpeg"),
    (b"GIF87a" + b"0" * 8, ".gif"),
    (b"GIF89a" + b"0" * 8, ".gif"),
    (b"RIFF\x00\x00\x00\x00WEBP" + b"0" * 8, ".webp"),
    (b"totally unknown bytes", ".fallback"),
])
def test_sniff_ext(data, expected):
    assert sniff_ext(data, ".fallback") == expected


# ── resize_screenshot (retrieve_rms_materials) ───────────────────────

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


def _image_bytes(size, fmt, mode="RGB"):
    import io
    buf = io.BytesIO()
    Image.new(mode, size, (200, 100, 50) if mode == "RGB" else None).save(buf, fmt)
    return buf.getvalue()


@pytest.mark.parametrize("src_size", [(1080, 1920), (480, 854), (900, 1600), (2000, 1000)])
def test_resize_screenshot_hits_exact_target(src_size):
    import io
    out, ext = resize_screenshot(_image_bytes(src_size, "JPEG"))
    assert ext == ".jpeg"
    assert Image.open(io.BytesIO(out)).size == (480, 854)


def test_resize_screenshot_png_stays_png():
    import io
    out, ext = resize_screenshot(_image_bytes((1080, 1920), "PNG"))
    assert ext == ".png"
    assert Image.open(io.BytesIO(out)).format == "PNG"


def test_resize_screenshot_rgba_source_converts_for_jpeg():
    import io
    buf = io.BytesIO()
    Image.new("RGBA", (1080, 1920)).save(buf, "WEBP")  # webp keeps alpha
    out, ext = resize_screenshot(buf.getvalue())
    assert ext == ".jpeg"
    img = Image.open(io.BytesIO(out))
    assert img.format == "JPEG" and img.size == (480, 854)
