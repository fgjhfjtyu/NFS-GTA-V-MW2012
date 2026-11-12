"""GTA V-to-NFS MW 2012 staging, validation and safe BNDL installation."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable

import core

APP_DIR = core.APP_DIR
GTA_EXTENSIONS = {".yft", ".ytd", ".ydr", ".ydd", ".ymt", ".ybn", ".ytyp", ".ycd", ".rpf"}
CODEWALKER_XML_EXTENSIONS = {".xml"}
MODEL_EXTENSIONS = {".obj", ".mtl", ".dds", ".fbx", ".gltf", ".glb", ".png", ".tga", ".jpg", ".jpeg"}
NFS_MATERIALS = {
    "body", "bodypaint", "bodypaintlivery", "carbon", "glass", "colouriseglass",
    "glassdoublesided", "light", "grille", "interior", "refraction",
}
TEXTURE_TYPES = {"c", "d", "dp", "e", "en", "in", "l", "n", "s"}
MAX_FILES = 100_000
MAX_UNPACKED_BYTES = 12 * 1024 * 1024 * 1024
LOG = Callable[[str], None]


def _safe_archive_name(name: str) -> str | None:
    raw = name.replace("\\", "/")
    p = PurePosixPath(raw)
    if not raw or p.is_absolute() or raw.startswith("//"):
        return None
    if any(part in ("", ".", "..") for part in p.parts):
        return None
    if p.parts and ":" in p.parts[0]:
        return None
    return "/".join(p.parts)


def _extract_zip_safely(archive: Path, destination: Path, log: LOG) -> int:
    total = 0
    count = 0
    dest = destination.resolve()
    with zipfile.ZipFile(archive) as zf:
        infos = zf.infolist()
        if len(infos) > MAX_FILES:
            raise ValueError(f"آرشیو بیش از {MAX_FILES:,} فایل دارد.")
        seen: set[str] = set()
        for info in infos:
            if info.is_dir():
                continue
            rel = _safe_archive_name(info.filename)
            if not rel:
                log(f"مسیر ناامن رد شد: {info.filename}")
                continue
            if rel.casefold() in seen:
                raise ValueError(f"آرشیو دارای مسیر تکراری است: {rel}")
            seen.add(rel.casefold())
            unix_mode = (info.external_attr >> 16) & 0xFFFF
            if unix_mode and (unix_mode & 0o170000) == 0o120000:
                log(f"لینک نمادین رد شد: {info.filename}")
                continue
            total += info.file_size
            if total > MAX_UNPACKED_BYTES:
                raise ValueError("حجم فایل‌های استخراجی از ۱۲ گیگابایت بیشتر است.")
            target = (dest / Path(*rel.split("/"))).resolve()
            if dest not in target.parents:
                log(f"مسیر خارج از فضای کاری رد شد: {info.filename}")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
            count += 1
    log(f"استخراج ZIP تکمیل شد: {count} فایل، {total / (1024 ** 2):.1f} مگابایت.")
    return count


def _find_7zip() -> str | None:
    found = shutil.which("7z") or shutil.which("7zz") or shutil.which("7za")
    if found:
        return found
    for candidate in (
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "7-Zip" / "7z.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "7-Zip" / "7z.exe",
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def _extract_7z_safely(source: Path, destination: Path, log: LOG) -> int:
    seven_zip = _find_7zip()
    if not seven_zip:
        raise ValueError("برای RAR/7Z باید 7-Zip نصب باشد؛ یا آرشیو را به ZIP تبدیل کنید.")
    listing = subprocess.run([seven_zip, "l", "-slt", str(source)], capture_output=True,
                             text=True, errors="replace", timeout=180)
    if listing.returncode not in (0, 1):
        raise ValueError("7-Zip نتوانست فهرست آرشیو را بخواند.")
    records = []
    for block in listing.stdout.split("\n\n"):
        record = {}
        for line in block.splitlines():
            if " = " in line:
                k, v = line.split(" = ", 1)
                record[k.strip()] = v.strip()
        name = record.get("Path")
        if not name or name == str(source) or "Type" in record:
            continue
        safe = _safe_archive_name(name)
        if not safe:
            raise ValueError(f"آرشیو دارای مسیر ناامن است: {name}")
        attrs = record.get("Attributes", "")
        if attrs.startswith(("L", "H")):
            raise ValueError(f"آرشیو دارای لینک نمادین/سخت است: {name}")
        if any(existing.casefold() == safe.casefold() for existing, _ in records):
            raise ValueError(f"آرشیو دارای مسیر تکراری است: {safe}")
        records.append((safe, int(record.get("Size", 0) or 0)))
    if len(records) > MAX_FILES or sum(size for _, size in records) > MAX_UNPACKED_BYTES:
        raise ValueError("آرشیو از محدودیت تعداد فایل یا حجم ایمن بیشتر است.")
    destination.mkdir(parents=True, exist_ok=True)
    # Extract into a new empty directory only after validating every listed name.
    result = subprocess.run([seven_zip, "x", str(source), f"-o{destination}", "-y", "-bd"],
                            capture_output=True, text=True, errors="replace", timeout=1800)
    if result.returncode != 0:
        raise ValueError("استخراج با 7-Zip ناموفق بود: " + (result.stderr or result.stdout)[-1200:])
    count = 0
    root = destination.resolve()
    for path in destination.rglob("*"):
        if path.is_symlink():
            path.unlink(missing_ok=True)
            continue
        if path.is_file():
            resolved = path.resolve()
            if root not in resolved.parents:
                path.unlink(missing_ok=True)
                continue
            count += 1
    log(f"استخراج آرشیو با 7-Zip تکمیل شد: {count} فایل.")
    return count


def scan_source(root: str | Path) -> dict:
    root = Path(root)
    found = {"gta": [], "xml": [], "models": [], "archives": [], "other": []}
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        ext = path.suffix.lower()
        rel = path.relative_to(root).as_posix()
        if ext in GTA_EXTENSIONS:
            found["gta"].append(rel)
        elif ext == ".xml":
            found["xml"].append(rel)
        elif ext in MODEL_EXTENSIONS:
            found["models"].append(rel)
        elif ext in {".zip", ".rar", ".7z"}:
            found["archives"].append(rel)
        else:
            found["other"].append(rel)
    for values in found.values():
        values.sort(key=str.casefold)
    return found


def create_conversion_workspace(source: str | Path, game_dir: str | Path,
                                donor_name: str | None = None,
                                log: LOG = print) -> dict:
    source = Path(source).expanduser().resolve()
    game = Path(game_dir).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError("فایل یا پوشه مود GTA V پیدا نشد.")
    if not game.is_dir():
        raise ValueError("پوشه بازی NFS معتبر نیست.")
    if source.is_dir() and source.is_symlink():
        raise ValueError("مسیر مود نباید لینک نمادین باشد.")

    base = Path.home() / "Documents" / "NFS-GTA-Converter" / "Workspaces"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "NFS-GTA-Converter" / "Workspaces"
        base.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    slug_source = source.stem if source.is_file() else source.name
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", slug_source).strip("._") or "GTAV_Mod"
    workspace = base / f"{slug[:42]}_{stamp}"
    dirs = {
        "source": workspace / "01_GTA_V_Source",
        "codewalker": workspace / "02_CodeWalker_XML_Export",
        "exchange": workspace / "03_Blender_Model_Exchange",
        "donor": workspace / "04_NFS_Donor_Unpacked",
        "final": workspace / "05_NFS_Export_and_BNDL",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)

    if source.is_dir():
        for root, subdirs, files in os.walk(source, followlinks=False):
            rel_dir = Path(root).relative_to(source)
            subdirs[:] = [d for d in subdirs if not (Path(root) / d).is_symlink()]
            for name in files:
                src = Path(root) / name
                if src.is_symlink() or not src.is_file():
                    continue
                rel = src.relative_to(source)
                dst = dirs["source"] / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        log("پوشه مود به فضای کاری کپی شد.")
    elif source.suffix.lower() == ".zip":
        _extract_zip_safely(source, dirs["source"], log)
    elif source.suffix.lower() in {".rar", ".7z"}:
        _extract_7z_safely(source, dirs["source"], log)
    elif source.suffix.lower() == ".rpf":
        shutil.copy2(source, dirs["source"] / source.name)
        log("فایل RPF کپی شد؛ برای استخراج آن باید از CodeWalker استفاده شود.")
    elif source.suffix.lower() in GTA_EXTENSIONS:
        shutil.copy2(source, dirs["source"] / source.name)
        log(f"فایل منبع GTA V کپی شد: {source.name}")
    elif source.suffix.lower() in {".xml", ".obj", ".mtl", ".dds", ".fbx", ".gltf", ".glb"}:
        shutil.copy2(source, dirs["source"] / source.name)
        log(f"فایل مدل/تکسچر واسط کپی شد: {source.name}")
    else:
        raise ValueError("ورودی باید پوشه، ZIP، RAR، 7Z، RPF یا فایل شناخته‌شده GTA/مدل باشد.")

    scan = scan_source(dirs["source"])
    donor_copy = ""
    if donor_name:
        safe_donor = Path(donor_name).name
        if safe_donor != donor_name or Path(safe_donor).suffix.lower() != ".bndl":
            raise ValueError("نام خودروی مرجع نامعتبر است.")
        donor = game / "VEHICLES" / safe_donor
        if not donor.is_file():
            raise FileNotFoundError(f"خودروی مرجع در پوشه VEHICLES پیدا نشد: {safe_donor}")
        donor_dest = dirs["donor"] / donor.name
        shutil.copy2(donor, donor_dest)
        donor_copy = str(donor_dest)
        log(f"خودروی مرجع کپی شد: {donor.name}")

    info = {
        "schema": 2,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": str(source),
        "game_dir": str(game),
        "donor_vehicle": donor_name or "",
        "donor_copy": donor_copy,
        "workspace": str(workspace),
        "folders": {key: str(value) for key, value in dirs.items()},
        "scan": scan,
    }
    (workspace / "workspace.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    (workspace / "READ_ME_FA.txt").write_text(_conversion_guide(info), encoding="utf-8")
    return {"workspace": str(workspace), "source_dir": str(dirs["source"]),
            "codewalker_dir": str(dirs["codewalker"]), "exchange_dir": str(dirs["exchange"]),
            "donor_dir": str(dirs["donor"]), "final_dir": str(dirs["final"]),
            "scan": scan, "donor_copy": donor_copy, "guide": str(workspace / "READ_ME_FA.txt")}


def _conversion_guide(info: dict) -> str:
    return f"""راهنمای تبدیل خودروی GTA V به Need for Speed Most Wanted 2012
{'=' * 70}

فضای کاری: {info['workspace']}
خودروی جایگزین: {info.get('donor_vehicle') or 'انتخاب نشده'}

مسیرهای ساخته‌شده:
01_GTA_V_Source                 فایل مود اصلی
02_CodeWalker_XML_Export       فایل‌های XML خروجی CodeWalker
03_Blender_Model_Exchange      فایل‌های واسط OBJ/FBX و تکسچرها
04_NFS_Donor_Unpacked          خودروی مرجع بازی
05_NFS_Export_and_BNDL         خروجی NFS و بسته BNDL

مراحل واقعی تبدیل:
1) اگر ورودی RPF یا فایل خام YFT/YDR/YDD/YTD است، آن را با CodeWalker باز کنید.
   مدل خودرو و تکسچرهای مرتبط را به XML و تصاویر قابل استفاده صادر کنید.
2) در Sollumz، XML صادرشده از CodeWalker را وارد Blender کنید. طبق مستندات Sollumz،
   افزونه فایل XML صادرشده از CodeWalker را می‌پذیرد؛ فایل باینری خام YFT را مستقیماً
   به آن ندهید. راهنمای رسمی: https://docs.sollumz.org/support/faq
3) مدل را به‌عنوان هندسه عمومی FBX/OBJ و تکسچرها را جداگانه در پوشه 03 صادر کنید.
   مدل GTA را باید با ساختار و مقیاس مناسب خودروی NFS تطبیق دهید؛ خودروهای NFS به
   اجزایی مثل بدنه، شیشه، چراغ، چرخ‌ها و نقاط اتصال نیاز دارند.
4) برای آماده‌سازی خروجی NFS از افزونه DGIorio استفاده کنید. این افزونه Blender
   3.1 تا 3.6 می‌خواهد، در حالی که Sollumz فعلی Blender 4.0 یا جدیدتر می‌خواهد؛
   ممکن است لازم باشد دو نسخه Blender نصب داشته باشید.
   افزونه: https://github.com/DGIorio/nfsmw_exporter
   افزونه و ابزارهای قدیمی‌تر NFSMW12VMT: https://github.com/NIVSAYZ/NFSMW12VMT
5) خودروی مرجع {info.get('donor_vehicle') or 'انتخاب‌شده'} را با Noesis و افزونه
   NFS MW 2012 unpacker باز کنید تا ساختار/منابع آن در پوشه 04 آماده باشد.
6) از Blender exporter و Bundle Packer/Unpacker خروجی BNDL سازگار با MW2012 بسازید.
   مستندات بسته‌بندی: https://github.com/DGIorio/bundle_packer_unpacker
7) فایل BNDL خروجی را در برنامه انتخاب و نصب کنید. پیش از جایگزینی، نسخه اصلی
   پشتیبان‌گیری می‌شود. از بخش مدیریت مودها می‌توانید آن را بازگردانید.

قواعد مهم مدل در مسیر قدیمی NFSMW12VMT:
- هر Mesh فقط یک Material داشته باشد.
- نام Mesh به صورت MeshName_MaterialType باشد.
- نوع‌های شناخته‌شده: {', '.join(sorted(NFS_MATERIALS))}
- تکسچرها DDS و در کنار OBJ/MTL باشند؛ نام‌گذاری MeshName_TextureType.
- مجموع رأس‌ها را زیر ۶۰٬۰۰۰ نگه دارید؛ بیشتر از آن ممکن است باعث crash شود.
- افزونه NBMC و blender_export_obj_NFSMW12VMT را هم‌زمان فعال نکنید.

یادآوری: این فرایند تبدیل هندسه را نمی‌توان با تغییر پسوند یا کپی فایل خودکار کرد.
مدل باید از نظر UV، متریال، مقیاس، چرخ‌ها و ساختار خودرو متناسب‌سازی شود.
این برنامه استخراج امن، آماده‌سازی فضای کار، اعتبارسنجی و نصب/بازگردانی BNDL را مدیریت می‌کند؛
تبدیل هندسی کامل و بدون دخالت Blender/CodeWalker در این نسخه پیاده‌سازی نشده است.
"""


def _parse_mtl_texture(line: str) -> str | None:
    # Handles MTL options such as -s/-bm; the texture path is the final token.
    import shlex
    try:
        tokens = shlex.split(line, posix=False)
    except ValueError:
        tokens = line.split()
    if len(tokens) < 2:
        return None
    return tokens[-1].strip('"\'')


def validate_nfs_model_folder(folder: str | Path) -> dict:
    """Structural validation for the legacy NFSMW12VMT OBJ/MTL/DDS pipeline."""
    root = Path(folder).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("پوشه مدل معتبر نیست.")
    objs = [p for p in root.iterdir() if p.is_file() and p.suffix.lower() == ".obj"]
    if len(objs) != 1:
        raise ValueError("برای اعتبارسنجی NFSMW12VMT باید دقیقاً یک OBJ اصلی در پوشه باشد.")
    obj = objs[0]
    errors: list[str] = []
    warnings: list[str] = []
    vertices = 0
    faces = 0
    object_names: list[str] = []
    object_mats: dict[str, set[str]] = {}
    current_obj = "(default)"
    mtl_names: list[str] = []
    with obj.open("r", encoding="utf-8", errors="replace") as stream:
        for raw in stream:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("v "):
                vertices += 1
            elif line.startswith("f "):
                faces += 1
            elif line.startswith("mtllib "):
                mtl_names.extend(line.split(maxsplit=1)[1].split())
            elif line.startswith(("o ", "g ")):
                current_obj = line[2:].strip() or "(unnamed)"
                if current_obj not in object_names:
                    object_names.append(current_obj)
                object_mats.setdefault(current_obj, set())
            elif line.startswith("usemtl "):
                object_mats.setdefault(current_obj, set()).add(line.split(maxsplit=1)[1].strip())
    if not vertices:
        errors.append("OBJ هیچ رأسی ندارد.")
    if not faces:
        errors.append("OBJ هیچ سطح/Face ندارد.")
    if vertices > 60000:
        errors.append(f"تعداد رأس‌ها {vertices:,} است؛ از سقف ۶۰٬۰۰۰ این مسیر بیشتر است.")
    if not mtl_names:
        errors.append("OBJ فایل MTL را معرفی نکرده است.")

    mtl_files: list[Path] = []
    for name in mtl_names:
        rel = _safe_archive_name(name)
        path = (root / Path(*rel.split("/"))).resolve() if rel else root / "__invalid__"
        if not rel or root not in path.parents or not path.is_file():
            errors.append(f"فایل MTL معتبر/موجود نیست: {name}")
        else:
            mtl_files.append(path)

    defined_materials: set[str] = set()
    textures: list[str] = []
    for mtl in mtl_files:
        with mtl.open("r", encoding="utf-8", errors="replace") as stream:
            for raw in stream:
                line = raw.strip()
                if line.startswith("newmtl "):
                    defined_materials.add(line.split(maxsplit=1)[1].strip())
                elif line.startswith(("map_Kd ", "map_Ka ", "map_Bump ", "bump ", "map_Ks ", "map_d ")):
                    ref = _parse_mtl_texture(line)
                    if ref:
                        textures.append(ref)

    if not object_names:
        warnings.append("OBJ فاقد نام Mesh است؛ نام‌گذاری Meshها را در Blender بررسی کنید.")
    for name in object_names:
        suffix = name.rsplit("_", 1)[-1].lower() if "_" in name else ""
        if suffix not in NFS_MATERIALS:
            warnings.append(f"نوع متریال انتهای نام Mesh شناخته‌شده نیست: {name}")
        assigned = object_mats.get(name, set())
        if len(assigned) > 1:
            errors.append(f"Mesh {name} بیش از یک Material دارد.")
        elif assigned and not assigned.issubset(defined_materials):
            errors.append(f"Mesh {name} به Material تعریف‌نشده ارجاع می‌دهد.")
        elif not assigned:
            warnings.append(f"Mesh {name} متریال تخصیص‌یافته ندارد.")

    for ref in textures:
        safe = _safe_archive_name(ref)
        path = (root / Path(*safe.split("/"))).resolve() if safe else root / "__invalid__"
        if not safe or root not in path.parents or not path.is_file():
            errors.append(f"تکسچر مفقود یا مسیر آن ناامن است: {ref}")
        elif path.suffix.lower() != ".dds":
            warnings.append(f"تکسچر باید DDS باشد: {ref}")
        else:
            with path.open("rb") as texture_stream:
                if texture_stream.read(4) != b"DDS ":
                    errors.append(f"هدر فایل DDS معتبر نیست: {ref}")

    dds_files = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() == ".dds"]
    if not dds_files:
        warnings.append("هیچ تکسچر DDS در پوشه مدل نیست؛ برای این مسیر معمولاً لازم است.")
    bad_dds = []
    for texture in dds_files:
        if texture.stat().st_size < 128:
            bad_dds.append(texture.name)
            continue
        with texture.open("rb") as stream:
            if stream.read(4) != b"DDS ":
                bad_dds.append(texture.name)
    if bad_dds:
        errors.append("فایل DDS با هدر نامعتبر: " + ", ".join(bad_dds[:10]))

    return {"obj": str(obj), "vertices": vertices, "faces": faces,
            "mesh_count": len(object_names), "materials": len(defined_materials),
            "dds_count": len(dds_files), "errors": errors, "warnings": warnings,
            "ok": not errors}


def list_vehicle_bundles(game_dir: str | Path) -> list[str]:
    vehicles = Path(game_dir) / "VEHICLES"
    if not vehicles.is_dir():
        return []
    return sorted((p.name for p in vehicles.iterdir()
                   if p.is_file() and p.suffix.lower() == ".bndl"), key=str.casefold)


def install_vehicle_bundle(game_dir: str | Path, source_bndl: str | Path,
                           target_name: str, log: LOG = print) -> str:
    """Atomically replace an existing car BNDL, preserving a unique restorable backup."""
    game = Path(game_dir).expanduser().resolve()
    source = Path(source_bndl).expanduser().resolve()
    if not source.is_file() or source.suffix.lower() != ".bndl":
        raise ValueError("خروجی باید فایل BNDL باشد.")
    if source.stat().st_size < 1024:
        raise ValueError("فایل BNDL خیلی کوچک است و احتمالاً ناقص است.")
    safe_name = Path(target_name).name
    if safe_name != target_name or Path(safe_name).suffix.lower() != ".bndl":
        raise ValueError("نام خودروی مقصد نامعتبر است.")
    vehicles = game / "VEHICLES"
    target = vehicles / safe_name
    if not vehicles.is_dir() or not target.is_file():
        raise FileNotFoundError(f"خودروی مقصد در VEHICLES پیدا نشد: {safe_name}")
    if source == target.resolve():
        raise ValueError("فایل منبع و مقصد یکسان هستند.")
    data = core.load_manifest(game)
    rel = f"VEHICLES/{safe_name}"
    if rel.casefold() in core._active_paths(data):
        raise ValueError("این خودرو قبلاً توسط یک مود جایگزین شده است؛ ابتدا مود قبلی را حذف کنید.")

    mod_id = f"GTA_V_{target.stem}_{time.time_ns()}"
    backup_root = game / APP_DIR / "backups" / mod_id
    backup = backup_root / "VEHICLES" / safe_name
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, backup)
    temp_target = vehicles / f".{safe_name}.{mod_id}.tmp"
    try:
        shutil.copy2(source, temp_target)
        if temp_target.stat().st_size < 1024:
            raise ValueError("فایل موقت خروجی ناقص است.")
        os.replace(temp_target, target)
        installed_hash = core.sha256_file(target)
        data["mods"].append({
            "id": mod_id, "name": f"GTA V → {safe_name}",
            "added": [], "replaced": [rel], "installed_hashes": {rel: installed_hash},
            "mode": "gta-conversion", "time": time.strftime("%Y-%m-%d %H:%M"),
        })
        core.save_manifest(game, data)
    except Exception:
        try:
            shutil.copy2(backup, temp_target)
            os.replace(temp_target, target)
        finally:
            temp_target.unlink(missing_ok=True)
            shutil.rmtree(backup_root, ignore_errors=True)
        raise
    log(f"خودروی {safe_name} جایگزین شد.")
    log(f"نسخه اصلی در این مسیر پشتیبان‌گیری شد: {backup}")
    return mod_id
