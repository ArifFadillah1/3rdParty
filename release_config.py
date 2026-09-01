"""Single source of truth for the release currently being shipped.

Edit CYCLE and VERSION below; every script derives the rest. Nothing else needs
to be edited per release — the release folder comes from CYCLE, the marketing
materials folder is discovered by looking inside it, and each store contributes
its own region set.

Any of it can be overridden for one run without touching this file:

    python3 huawei_package_upload.py --dry-run
    python3 samsung_package_upload.py --regions id,th
    python3 retrieve_rms_materials.py --cycle 3.81

CYCLE and VERSION are deliberately separate. The cycle is a planning bucket and
does not always share a prefix with the version it carries — cycle 3.82 shipped
version 3.81.29 — so deriving one from the other silently breaks exactly when it
matters.
"""

import glob
import json
import os
import sys

# ── Edit these two per release ─────────────────────────────────────────
CYCLE = "3.80"        # release cycle: names the release folder, drives the RMS query
VERSION = "3.80.37"   # build version: what the stores actually see

# Regions to ship.
#   None  → every region the store being run carries
#   List  → only these, in the order given
# A region Shopee ships but this store does not carry (mx on Samsung, say) is
# reported and skipped. A region that is not a Shopee region at all stops the
# run, so a typo can never look like a successful upload.
REGIONS = None

# Release folders live at <BASE_DIR>/<CYCLE>/
BASE_DIR = os.path.expanduser("~/Documents/Python/3rdPartyApp")

# Marketing materials folder.
#   None  → newest RMS_Materials_* or GPS_Content_Retrieval_* in the release folder
#   str   → a folder name inside the release folder, or an absolute path
MATERIALS_DIR = None

DRY_RUN = False

# Every region Shopee ships to. This exists only to tell a typo apart from a
# region a particular store genuinely does not carry.
ALL_REGIONS = ["ar", "br", "cl", "co", "id", "mx", "my", "ph", "sg", "th", "tw", "vn"]

_MATERIALS_PATTERNS = ("RMS_Materials_*", "GPS_Content_Retrieval_*")


# ── Paths ──────────────────────────────────────────────────────────────
def root():
    """The release folder for the current cycle."""
    return os.path.join(BASE_DIR, CYCLE)


def find_materials(release_root=None):
    """Absolute path to this release's marketing materials folder.

    A release folder normally holds exactly one. When it holds several the
    newest by modification time wins and the others are named, because the
    difference between two timestamped folder names is not something anyone
    can check by eye.
    """
    release_root = release_root or root()

    if MATERIALS_DIR:
        path = (MATERIALS_DIR if os.path.isabs(MATERIALS_DIR)
                else os.path.join(release_root, MATERIALS_DIR))
        if not os.path.isdir(path):
            sys.exit(f"Materials folder not found: {path}")
        return path

    candidates = []
    for pattern in _MATERIALS_PATTERNS:
        candidates.extend(p for p in glob.glob(os.path.join(release_root, pattern))
                          if os.path.isdir(p))
    if not candidates:
        sys.exit(f"No materials folder under {release_root}\n"
                 f"  Expected {' or '.join(_MATERIALS_PATTERNS)}. Run "
                 f"retrieve_rms_materials.py first, or set MATERIALS_DIR.")

    candidates.sort(key=os.path.getmtime)
    chosen = candidates[-1]
    if len(candidates) > 1:
        others = ", ".join(os.path.basename(p) for p in candidates[:-1])
        print(f"  NOTE  {len(candidates)} materials folders present; using "
              f"{os.path.basename(chosen)} (ignoring {others})")
    return chosen


def bundle_path(store_dir, bundle_name, release_root=None):
    """Locate a build on disk. Jenkins artifacts land in the store's subfolder
    prefixed with the build number, e.g.
    26557-shopee-indonesia-release-huawei-3.80.37.apk. If a version was rebuilt,
    several prefixes match and the highest build number wins."""
    release_root = release_root or root()

    for candidate in (os.path.join(release_root, bundle_name),
                      os.path.join(release_root, store_dir, bundle_name)):
        if os.path.isfile(candidate):
            return candidate

    def build_no(path):
        head = os.path.basename(path).split("-", 1)[0]
        return int(head) if head.isdigit() else -1

    prefixed = sorted(glob.glob(os.path.join(release_root, store_dir, "*-" + bundle_name)),
                      key=build_no)
    if prefixed:
        if len(prefixed) > 1:
            others = ", ".join(os.path.basename(p) for p in prefixed[:-1])
            print(f"  NOTE  {len(prefixed)} builds match {bundle_name}; "
                  f"using {os.path.basename(prefixed[-1])} (ignoring {others})")
        return prefixed[-1]
    raise FileNotFoundError(f"No build found for {bundle_name} under {release_root}")


# ── Regions ────────────────────────────────────────────────────────────
def resolve_regions(supported, store):
    """Decide which regions this store should process.

    Three outcomes: a region the store carries is processed; a Shopee region the
    store does not carry is reported and skipped; anything else is a typo and
    stops the run before a single byte is uploaded. Both uploaders used to drop
    all three cases silently, so `--regions id,tv` reported success while Taiwan
    never shipped.
    """
    supported = set(supported)

    if REGIONS is None:
        selected = sorted(supported)
        print(f"  Regions   {', '.join(selected)}  (all {store} regions)")
        return selected

    unknown = [r for r in REGIONS if r not in ALL_REGIONS]
    if unknown:
        sys.exit(f"Unknown region(s): {', '.join(unknown)}\n"
                 f"  Shopee regions are: {', '.join(ALL_REGIONS)}")

    selected = [r for r in REGIONS if r in supported]
    skipped = [r for r in REGIONS if r not in supported]

    if not selected:
        sys.exit(f"None of the requested regions ({', '.join(REGIONS)}) are on {store}\n"
                 f"  {store} carries: {', '.join(sorted(supported))}")

    line = f"  Regions   {', '.join(selected)}"
    if skipped:
        line += f"   (skipped, not on {store}: {', '.join(skipped)})"
    print(line)
    return selected


# ── CLI ────────────────────────────────────────────────────────────────
def add_release_args(parser):
    """Add the overrides every script accepts. Call apply_args() with the result."""
    parser.add_argument("--cycle", help=f"release cycle (default: {CYCLE})")
    parser.add_argument("--version", help=f"build version (default: {VERSION})")
    parser.add_argument("--regions",
                        help="comma-separated region codes (default: every region the store carries)")
    parser.add_argument("--materials",
                        help="materials folder name or path (default: newest in the release folder)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the resolved paths and region plan, then exit without uploading")
    return parser


def apply_args(args):
    global CYCLE, VERSION, REGIONS, MATERIALS_DIR, DRY_RUN
    if getattr(args, "cycle", None):
        CYCLE = args.cycle
    if getattr(args, "version", None):
        VERSION = args.version
    if getattr(args, "regions", None):
        REGIONS = [r.strip().lower() for r in args.regions.split(",") if r.strip()]
    if getattr(args, "materials", None):
        MATERIALS_DIR = args.materials
    DRY_RUN = bool(getattr(args, "dry_run", False))


# ── Reporting ──────────────────────────────────────────────────────────
def print_header(store, materials_dir, flags=""):
    print(f"\n{'─' * 68}")
    print(f"{store}   cycle {CYCLE}   version {VERSION}")
    print(f"{'─' * 68}")
    print(f"  Release   {root()}")
    print(f"  Materials {materials_dir}")
    if flags:
        print(f"  Flags     {flags}")


# ── Preflight ──────────────────────────────────────────────────────────
def check_package(package_dir, languages, min_screenshots=1, max_screenshots=None):
    """Validate one package's marketing materials. Returns a list of problems,
    empty when the package is ready.

    Asset lookup deliberately mirrors what the uploaders do — substring match on
    the language code and on "icon"/"phoneScreenshots" — so a package that passes
    here cannot then have the uploader silently find nothing.
    """
    package = os.path.basename(package_dir)
    listing_path = os.path.join(package_dir, "listing.txt")
    if not os.path.isfile(listing_path):
        return [f"no {package}/listing.txt"]
    try:
        with open(listing_path, encoding="utf-8") as f:
            listings = json.load(f, strict=False).get("listings", [])
    except (ValueError, OSError) as exc:
        return [f"{package}/listing.txt unreadable: {exc}"]

    available = {e.get("language") for e in listings if e.get("language")}
    files = [f for f in os.listdir(package_dir)
             if os.path.isfile(os.path.join(package_dir, f))]

    problems = []
    for lang in languages:
        if lang not in available:
            problems.append(f"{lang}: not in listing.txt "
                            f"(has: {', '.join(sorted(available)) or 'nothing'})")
            continue
        if not [f for f in files if lang in f and "icon" in f]:
            problems.append(f"{lang}: no icon file")
        shots = [f for f in files if lang in f and "phoneScreenshots" in f]
        if len(shots) < min_screenshots:
            problems.append(f"{lang}: {len(shots)} screenshot(s), needs at least {min_screenshots}")
        elif max_screenshots and len(shots) > max_screenshots:
            problems.append(f"{lang}: {len(shots)} screenshot(s), store accepts at most {max_screenshots}")
    return problems


def report_preflight(findings, store, verbose=False):
    """findings: (region, package, apk_name_or_None, [problems]).

    Returns True when every selected region is ready. Called before the first
    upload on every run, so a release can no longer die half-finished on a
    missing file — which is how the 3.80 run broke.
    """
    blocked = [f for f in findings if f[3]]

    if verbose:
        print(f"\n  Dry run — nothing will be uploaded.\n")
        width = max((len(f[1]) for f in findings), default=14)
        for region, package, apk, problems in findings:
            status = "ready" if not problems else "NOT READY"
            print(f"  {region:<3} {package:<{width}}  {apk or '(no apk this run)'}   {status}")
        print()

    if not blocked:
        print(f"  Preflight  {len(findings)} package(s) ready")
        return True

    print(f"\n  Preflight failed — nothing has been uploaded:\n")
    width = max(len(f[1]) for f in blocked)
    for region, package, _apk, problems in blocked:
        print(f"  {region:<3} {package:<{width}}")
        for problem in problems:
            print(f"  {'':<3} {'':<{width}}  - {problem}")
    print(f"\n  Fix these, or narrow the run with --regions "
          f"({', '.join(f[0] for f in findings if not f[3]) or 'nothing else is ready'}).")
    return False

