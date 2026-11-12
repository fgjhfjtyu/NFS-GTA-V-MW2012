"""Safe file installation, manifest and rollback helpers for NFS MW 2012."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable

APP_DIR = "_ModManager"
GAME_EXE = "NFS13.exe"
MAX_ARCHIVE_FILES = 100_000
MAX_ARCHIVE_BYTES = 12 * 1024 * 1024 * 1024
LOG = Callable[[str], None]

COMMON_SUBPATHS = [
    r"Program Files (x86)\Origin Games\Need for Speed(TM) Most Wanted",
    r"Program Files\Origin Games\Need for Speed(TM) Most Wanted",
    r"Program Files (x86)\EA Games\Need for Speed(TM) Most Wanted",
    r"Program Files (x86)\Steam\steamapps\common\Need for Speed Most Wanted",
    r"Program Files\EA Games\Need for Speed(TM) Most Wanted",
    r"Games\Need for Speed Most Wanted",
    r"Games\Need for Speed(TM) Most Wanted",
]


def detect_game_dir() -> str:
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        for sub in COMMON_SUBPATHS:
            candidate = Path(f"{letter}:\\{sub}")
            if (candidate / GAME_EXE).is_file() and (candidate / "VEHICLES").is_dir():
                return str(candidate)
    return ""


def is_game_dir(path: str | Path) -> bool:
    p = Path(path)
    return p.is_dir() and (p / GAME_EXE).is_file()


def _manifest_path(game_dir: str | Path) -> Path:
    return Path(game_dir) / APP_DIR / "manifest.json"


def load_manifest(game_dir: str | Path) -> dict:
    path = _manifest_path(game_dir)
    if not path.exists():
        return {"schema": 2, "mods": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"فهرست مودها خراب یا ناخواناست: {path}\n{exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("mods", []), list):
        raise ValueError("ساختار manifest.json معتبر نیست؛ برای جلوگیری از حذف سوابق، عملیات متوقف شد.")
    data.setdefault("schema", 2)
    data.setdefault("mods", [])
    return data


def save_manifest(game_dir: str | Path, data: dict) -> None:
    target = _manifest_path(game_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    fd, temp_name = tempfile.mkstemp(prefix="manifest-", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, target)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative_path(value: str) -> str | None:
    raw = str(value).replace("\\", "/")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or raw.startswith("//"):
        return None
    parts = path.parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        return None
    if ":" in parts[0] or parts[0].casefold() == APP_DIR.casefold():
        return None
    return "/".join(parts)


def _safe_target(game_dir: Path, rel: str) -> Path:
    safe = safe_relative_path(rel)
    if not safe:
        raise ValueError(f"مسیر فایل مود ناامن است: {rel}")
    root = game_dir.resolve()
    target = (root / Path(*safe.split("/"))).resolve()
    if root not in target.parents:
        raise ValueError(f"مسیر فایل از پوشه بازی خارج می‌شود: {rel}")
    return target


def _active_paths(data: dict) -> set[str]:
    active: set[str] = set()
    for mod in data.get("mods", []):
        for rel in list(mod.get("added", [])) + list(mod.get("replaced", [])):
            safe = safe_relative_path(rel)
            if safe:
                active.add(safe.casefold())
    return active


def _list_mod_files(mod_path: str | Path):
    path = Path(mod_path)
    entries: list[tuple[str, object]] = []
    archive = None
    if path.is_dir():
        for root, dirs, files in os.walk(path, followlinks=False):
            dirs[:] = [d for d in dirs if not (Path(root) / d).is_symlink()]
            for name in files:
                source = Path(root) / name
                if source.is_symlink() or not source.is_file():
                    continue
                rel = safe_relative_path(source.relative_to(path).as_posix())
                if rel:
                    entries.append((rel, source))
    elif path.is_file() and path.suffix.lower() == ".zip":
        archive = zipfile.ZipFile(path)
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_FILES:
            archive.close()
            raise ValueError("تعداد فایل‌های ZIP از حد ایمنی بیشتر است.")
        total = sum(info.file_size for info in infos if not info.is_dir())
        if total > MAX_ARCHIVE_BYTES:
            archive.close()
            raise ValueError("حجم استخراج ZIP از ۱۲ گیگابایت بیشتر است.")
        seen: set[str] = set()
        for info in infos:
            if info.is_dir():
                continue
            rel = safe_relative_path(info.filename)
            mode = (info.external_attr >> 16) & 0xFFFF
            is_symlink = mode and (mode & 0o170000) == 0o120000
            if rel and not is_symlink:
                if rel.casefold() in seen:
                    archive.close()
                    raise ValueError(f"مسیر تکراری در ZIP پیدا شد: {rel}")
                seen.add(rel.casefold())
                entries.append((rel, info))
    else:
        raise ValueError("ورودی مود باید پوشه یا فایل ZIP باشد.")
    if not entries:
        if archive:
            archive.close()
        raise ValueError("هیچ فایل قابل نصبی در مود پیدا نشد.")
    # Prevent duplicate paths in a malicious archive.
    seen: set[str] = set()
    for rel, _ in entries:
        key = rel.casefold()
        if key in seen:
            if archive:
                archive.close()
            raise ValueError(f"مسیر تکراری در مود پیدا شد: {rel}")
        seen.add(key)
    return entries, archive


def _detect_strip(rels: Iterable[str], game_dir: Path) -> str | None:
    rels = list(rels)
    tops = {item.split("/", 1)[0] for item in rels}
    if len(tops) == 1 and all("/" in item for item in rels):
        top = next(iter(tops))
        if not (game_dir / top).exists():
            return top
    return None


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    os.close(fd)
    try:
        shutil.copy2(source, temp_name)
        os.replace(temp_name, destination)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def install_mod(game_dir: str | Path, mod_path: str | Path, mode: str,
                log: LOG = print) -> str:
    """Install a regular NFS mod transactionally. GTA RAGE assets are rejected."""
    game = Path(game_dir).expanduser().resolve()
    if not game.is_dir():
        raise ValueError("پوشه بازی معتبر نیست.")
    if mode not in {"add", "replace"}:
        raise ValueError("حالت نصب نامعتبر است.")
    entries, archive = _list_mod_files(mod_path)
    rels = [rel for rel, _ in entries]
    raw_gta_exts = {".yft", ".ytd", ".ydr", ".ydd", ".ymt", ".ybn", ".ytyp", ".ycd", ".rpf"}
    raw_gta = [rel for rel in rels if Path(rel).suffix.lower() in raw_gta_exts]
    if raw_gta:
        if archive:
            archive.close()
        raise ValueError("فایل‌های خام GTA V مستقیماً قابل نصب در NFS نیستند؛ از گردش‌کار تبدیل خودرو استفاده کنید.")

    strip = _detect_strip(rels, game)
    if strip:
        log(f"پوشه ریشه {strip} تشخیص داده شد.")
    plan: list[tuple[str, object, Path, bool]] = []
    data = load_manifest(game)
    active = _active_paths(data)
    for rel, source in entries:
        target_rel = rel[len(strip) + 1:] if strip else rel
        # Convenience for single-file vehicle bundles.
        if target_rel.lower().endswith(".bndl") and "/" not in target_rel:
            target_rel = f"VEHICLES/{target_rel}"
        target = _safe_target(game, target_rel)
        exists = target.exists()
        if target_rel.casefold() in active:
            if archive:
                archive.close()
            raise ValueError(f"فایل {target_rel} قبلاً توسط مود دیگری مدیریت می‌شود؛ ابتدا آن مود را حذف کنید.")
        if exists and mode == "add":
            log(f"رد شد (فایل موجود است): {target_rel}")
            continue
        if exists and not target.is_file():
            if archive:
                archive.close()
            raise ValueError(f"مسیر مقصد فایل معمولی نیست: {target_rel}")
        plan.append((target_rel, source, target, exists))
    if archive is None and not plan:
        raise ValueError("فایل جدیدی برای نصب باقی نماند.")
    if not plan:
        if archive:
            archive.close()
        raise ValueError("هیچ فایلی نصب نشد؛ همه فایل‌ها از قبل وجود دارند.")

    name = Path(mod_path).stem if Path(mod_path).is_file() else Path(mod_path).name
    mod_id = f"{''.join(c if c.isalnum() or c in '._-' else '_' for c in name)[:48]}_{time.time_ns()}"
    backup_root = game / APP_DIR / "backups" / mod_id
    added: list[str] = []
    replaced: list[str] = []
    hashes: dict[str, str] = {}
    installed: list[tuple[Path, Path | None]] = []
    try:
        for target_rel, source, target, existed in plan:
            if existed:
                backup = backup_root / Path(*target_rel.split("/"))
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup)
                replaced.append(target_rel)
            else:
                backup = None
                added.append(target_rel)
            # Materialize zip members into a temp file before atomic replacement.
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, staging_name = tempfile.mkstemp(prefix=".nfsmod-", suffix=".tmp", dir=target.parent)
            os.close(fd)
            try:
                if archive:
                    with archive.open(source) as src, open(staging_name, "wb") as dst:
                        shutil.copyfileobj(src, dst, 1024 * 1024)
                else:
                    shutil.copy2(source, staging_name)
                os.replace(staging_name, target)
            finally:
                try:
                    os.unlink(staging_name)
                except FileNotFoundError:
                    pass
            hashes[target_rel] = sha256_file(target)
            installed.append((target, backup))
            log(("جایگزین شد: " if existed else "نصب شد: ") + target_rel)
    except Exception:
        for target, backup in reversed(installed):
            if backup and backup.exists():
                _atomic_copy(backup, target)
            else:
                target.unlink(missing_ok=True)
        shutil.rmtree(backup_root, ignore_errors=True)
        raise
    finally:
        if archive:
            archive.close()

    data["mods"].append({"id": mod_id, "name": name, "added": added,
                         "replaced": replaced, "installed_hashes": hashes,
                         "mode": mode, "time": time.strftime("%Y-%m-%d %H:%M")})
    try:
        save_manifest(game, data)
    except Exception:
        for target, backup in reversed(installed):
            if backup and backup.exists():
                _atomic_copy(backup, target)
            else:
                target.unlink(missing_ok=True)
        shutil.rmtree(backup_root, ignore_errors=True)
        raise
    log(f"پایان نصب: {len(added)} فایل جدید و {len(replaced)} جایگزینی.")
    return mod_id


def uninstall_mod(game_dir: str | Path, mod_id: str, log: LOG = print) -> None:
    game = Path(game_dir).expanduser().resolve()
    data = load_manifest(game)
    mod = next((item for item in data["mods"] if item.get("id") == mod_id), None)
    if not mod:
        raise ValueError("این مود در فهرست نصب‌شده‌ها نیست.")
    mod_id_text = str(mod_id)
    if (not mod_id_text or mod_id_text in {".", ".."} or "/" in mod_id_text
            or "\\" in mod_id_text or ":" in mod_id_text):
        raise ValueError("شناسه مود نامعتبر است؛ مسیر پشتیبان قابل اعتماد نیست.")
    backup_root = game / APP_DIR / "backups" / mod_id_text
    # Preflight all restore paths before modifying anything.
    restore: list[tuple[str, Path, Path]] = []
    for rel in mod.get("replaced", []):
        safe = safe_relative_path(rel)
        if not safe:
            raise ValueError(f"فهرست مود دارای مسیر ناامن است: {rel}")
        backup = backup_root / Path(*safe.split("/"))
        target = _safe_target(game, safe)
        if not backup.is_file():
            raise FileNotFoundError(f"نسخه پشتیبان پیدا نشد؛ برای جلوگیری از خراب‌شدن بازی عملیات لغو شد: {backup}")
        restore.append((safe, backup, target))

    for rel, backup, target in restore:
        _atomic_copy(backup, target)
        log(f"نسخه اصلی بازگردانده شد: {rel}")

    installed_hashes = mod.get("installed_hashes", {})
    for rel in mod.get("added", []):
        safe = safe_relative_path(rel)
        if not safe:
            log(f"مسیر ناامن حذف نشد: {rel}")
            continue
        target = _safe_target(game, safe)
        if not target.is_file():
            continue
        expected = installed_hashes.get(rel)
        if expected and sha256_file(target) != expected:
            log(f"حذف نشد چون فایل پس از نصب تغییر کرده است: {rel}")
            continue
        target.unlink()
        log(f"فایل افزوده‌شده حذف شد: {rel}")
        parent = target.parent
        while parent != game:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
    data["mods"] = [item for item in data["mods"] if item.get("id") != mod_id]
    save_manifest(game, data)
    shutil.rmtree(backup_root, ignore_errors=True)
    log("عملیات حذف مود پایان یافت.")
