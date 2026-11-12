"""Locate external tools used by the GTA V -> NFS MW 2012 conversion workflow."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

APP_NAME = "NFS-GTA-Converter"


def config_path() -> Path:
    base = Path(os.environ.get("APPDATA", Path.home() / ".config"))
    return base / APP_NAME / "tools.json"


def load_config() -> dict:
    path = config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(data: dict) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _candidate_paths() -> dict[str, list[Path]]:
    pf = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    pfx = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return {
        "nfs_library": [],
        "blender_36": [pf / "Blender Foundation" / "Blender 3.6" / "blender.exe",
                       pfx / "Blender Foundation" / "Blender 3.6" / "blender.exe"],
        "blender_34": [pf / "Blender Foundation" / "Blender 3.4" / "blender.exe",
                       pfx / "Blender Foundation" / "Blender 3.4" / "blender.exe"],
        "blender_4": [pf / "Blender Foundation" / "Blender 4.0" / "blender.exe",
                      pf / "Blender Foundation" / "Blender 4.1" / "blender.exe",
                      pf / "Blender Foundation" / "Blender 4.2" / "blender.exe",
                      pf / "Blender Foundation" / "Blender 4.3" / "blender.exe",
                      pf / "Blender Foundation" / "Blender 4.4" / "blender.exe",
                      pf / "Blender Foundation" / "Blender 4.5" / "blender.exe",
                      pf / "Blender Foundation" / "Blender 5.0" / "blender.exe",
                      local / "Programs" / "Blender Foundation" / "Blender 4.0" / "blender.exe"],
        "codewalker": [pf / "CodeWalker" / "CodeWalker.exe", pfx / "CodeWalker" / "CodeWalker.exe",
                       Path.home() / "Desktop" / "CodeWalker" / "CodeWalker.exe"],
        "noesis": [pf / "Noesis" / "Noesis.exe", pfx / "Noesis" / "Noesis.exe",
                   Path.home() / "Desktop" / "Noesis" / "Noesis.exe"],
        "seven_zip": [pf / "7-Zip" / "7z.exe", pfx / "7-Zip" / "7z.exe"],
        "nvcompress": [pf / "NVIDIA Corporation" / "NVIDIA Texture Tools" / "nvcompress.exe",
                       pfx / "NVIDIA Corporation" / "NVIDIA Texture Tools" / "nvcompress.exe"],
    }


def _first_existing(paths: list[Path]) -> str:
    for path in paths:
        if path.is_file():
            return str(path)
    return ""


def _version(executable: str, timeout: int = 5) -> str:
    if not executable or not Path(executable).is_file():
        return ""
    try:
        result = subprocess.run([executable, "--version"], capture_output=True, text=True,
                                 errors="replace", timeout=timeout)
        output = (result.stdout + "\n" + result.stderr).strip()
        return output.splitlines()[0][:180] if output else "نسخه قابل تشخیص نیست"
    except Exception as exc:
        return f"خطا در بررسی نسخه: {exc}"


def detect_tools() -> dict:
    configured = load_config()
    result = {}
    for key, paths in _candidate_paths().items():
        configured_path = configured.get(key, "")
        path = configured_path if configured_path and Path(configured_path).is_file() else _first_existing(paths)
        if key == "nfs_library":
            path = configured_path if configured_path and Path(configured_path).is_dir() else ""
            result[key] = {"path": path, "version": "پوشه کتابخانه معتبر" if path else "", "found": bool(path)}
            continue
        if key == "seven_zip" and not path:
            path = shutil.which("7z") or ""
        if not path and key == "blender_4":
            # Blender portable folders often install outside the standard paths.
            candidates = list((Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Blender Foundation").glob("Blender 4*/blender.exe"))
            path = str(candidates[-1]) if candidates else ""
        version = _version(path) if key.startswith("blender") else ("موجود" if path else "")
        result[key] = {"path": path, "version": version, "found": bool(path and Path(path).is_file())}
    return result


def configure_tool(key: str, path: str) -> None:
    allowed = set(_candidate_paths())
    if key not in allowed:
        raise ValueError("نام ابزار ناشناخته است.")
    candidate = Path(path).expanduser().resolve()
    if key == "nfs_library":
        if not candidate.is_dir():
            raise FileNotFoundError("پوشه کتابخانه NFS پیدا نشد.")
        if not (candidate / "NFSMW_Library_PC").is_dir() and candidate.name != "NFSMW_Library_PC":
            raise ValueError("پوشه انتخاب‌شده باید شامل پوشه NFSMW_Library_PC باشد یا خود آن پوشه باشد.")
    elif not candidate.is_file():
        raise FileNotFoundError("فایل انتخاب‌شده پیدا نشد.")
    config = load_config()
    config[key] = str(candidate)
    save_config(config)


def blender_role_status(tools: dict) -> dict:
    def version_num(info):
        text = info.get("version", "")
        match = re.search(r"Blender\s+(\d+)\.(\d+)", text, re.I)
        return tuple(map(int, match.groups())) if match else None
    v36 = version_num(tools.get("blender_36", {})) or version_num(tools.get("blender_34", {}))
    v4 = version_num(tools.get("blender_4", {}))
    return {
        "nfs_export": bool(v36 and (3, 1) <= v36 <= (3, 6)),
        "gta_import": bool(v4 and v4 >= (4, 0)),
    }
