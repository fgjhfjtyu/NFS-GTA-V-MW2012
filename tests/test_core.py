from pathlib import Path
import zipfile

import pytest

import core
import gta_pipeline


def make_game(tmp_path: Path) -> Path:
    game = tmp_path / "NFS"
    (game / "VEHICLES").mkdir(parents=True)
    (game / "NFS13.exe").write_bytes(b"exe")
    (game / "VEHICLES" / "CAR_A.BNDL").write_bytes(b"ORIGINAL" * 256)
    return game


def test_vehicle_install_and_restore(tmp_path):
    game = make_game(tmp_path)
    mod = tmp_path / "converted.BNDL"
    mod.write_bytes(b"CONVERTED" * 300)
    mod_id = gta_pipeline.install_vehicle_bundle(game, mod, "CAR_A.BNDL", lambda _: None)
    assert (game / "VEHICLES" / "CAR_A.BNDL").read_bytes() == mod.read_bytes()
    manifest = core.load_manifest(game)
    assert manifest["mods"][0]["id"] == mod_id
    core.uninstall_mod(game, mod_id, lambda _: None)
    assert (game / "VEHICLES" / "CAR_A.BNDL").read_bytes() == b"ORIGINAL" * 256
    assert core.load_manifest(game)["mods"] == []


def test_vehicle_install_blocks_second_active_replacement(tmp_path):
    game = make_game(tmp_path)
    mod = tmp_path / "converted.BNDL"
    mod.write_bytes(b"CONVERTED" * 300)
    gta_pipeline.install_vehicle_bundle(game, mod, "CAR_A.BNDL", lambda _: None)
    with pytest.raises(ValueError, match="قبلاً"):
        gta_pipeline.install_vehicle_bundle(game, mod, "CAR_A.BNDL", lambda _: None)


def test_regular_zip_install_and_uninstall(tmp_path):
    game = make_game(tmp_path)
    package = tmp_path / "mod.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("readme.txt", "new file")
        archive.writestr("VEHICLES/CAR_A.BNDL", b"REPLACED" * 300)
    mod_id = core.install_mod(game, package, "replace", lambda _: None)
    assert (game / "readme.txt").read_text() == "new file"
    assert (game / "VEHICLES" / "CAR_A.BNDL").read_bytes().startswith(b"REPLACED")
    core.uninstall_mod(game, mod_id, lambda _: None)
    assert not (game / "readme.txt").exists()
    assert (game / "VEHICLES" / "CAR_A.BNDL").read_bytes() == b"ORIGINAL" * 256


def test_generic_installer_rejects_raw_gta_assets(tmp_path):
    game = make_game(tmp_path)
    source = tmp_path / "raw"
    source.mkdir()
    (source / "adder.yft").write_bytes(b"not for NFS")
    with pytest.raises(ValueError, match="خام GTA V"):
        core.install_mod(game, source, "replace", lambda _: None)


def test_zip_slip_is_not_extracted(tmp_path):
    game = make_game(tmp_path)
    package = tmp_path / "bad.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("../../outside.txt", "bad")
    with pytest.raises(ValueError):
        core.install_mod(game, package, "replace", lambda _: None)
    assert not (tmp_path / "outside.txt").exists()


def test_uninstall_does_not_delete_user_modified_new_file(tmp_path):
    game = make_game(tmp_path)
    package = tmp_path / "mod.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("new.txt", "original mod file")
    mod_id = core.install_mod(game, package, "add", lambda _: None)
    (game / "new.txt").write_text("user changed this")
    logs = []
    core.uninstall_mod(game, mod_id, logs.append)
    assert (game / "new.txt").read_text() == "user changed this"
    assert any("تغییر کرده" in line for line in logs)


def test_nfs_model_validation_accepts_basic_valid_package(tmp_path):
    folder = tmp_path / "model"
    folder.mkdir()
    (folder / "car.obj").write_text(
        "mtllib car.mtl\no Body_body\nv 0 0 0\nv 1 0 0\nv 0 1 0\n"
        "usemtl Paint\nf 1 2 3\n", encoding="utf-8")
    (folder / "car.mtl").write_text("newmtl Paint\nmap_Kd Body_d.dds\n", encoding="utf-8")
    (folder / "Body_d.dds").write_bytes(b"DDS " + bytes(124))
    result = gta_pipeline.validate_nfs_model_folder(folder)
    assert result["ok"] is True
    assert result["vertices"] == 3
    assert result["faces"] == 1


def test_nfs_model_validation_detects_material_per_mesh_limit(tmp_path):
    folder = tmp_path / "model"
    folder.mkdir()
    (folder / "car.obj").write_text(
        "mtllib car.mtl\no Body_body\nv 0 0 0\nv 1 0 0\nv 0 1 0\n"
        "usemtl Paint\nusemtl Glass\nf 1 2 3\n", encoding="utf-8")
    (folder / "car.mtl").write_text("newmtl Paint\nnewmtl Glass\n", encoding="utf-8")
    result = gta_pipeline.validate_nfs_model_folder(folder)
    assert result["ok"] is False
    assert any("بیش از یک Material" in item for item in result["errors"])


def test_workspace_accepts_single_yft_file(tmp_path, monkeypatch):
    game = make_game(tmp_path)
    yft = tmp_path / "adder.yft"
    yft.write_bytes(b"YFTDATA")
    monkeypatch.setattr(gta_pipeline.Path, "home", lambda: tmp_path)
    result = gta_pipeline.create_conversion_workspace(yft, game, "CAR_A.BNDL", lambda _: None)
    staged = Path(result["source_dir"]) / "adder.yft"
    assert staged.is_file()
    assert "adder.yft" in result["scan"]["gta"]
    assert Path(result["donor_copy"]).is_file()
    assert Path(result["guide"]).is_file()


def test_workspace_extracts_zip_without_traversal(tmp_path, monkeypatch):
    game = make_game(tmp_path)
    package = tmp_path / "car.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("mod/adder.yft", b"YFTDATA")
        archive.writestr("../escape.txt", b"NO")
    monkeypatch.setattr(gta_pipeline.Path, "home", lambda: tmp_path)
    result = gta_pipeline.create_conversion_workspace(package, game, "CAR_A.BNDL", lambda _: None)
    assert (Path(result["source_dir"]) / "mod" / "adder.yft").is_file()
    assert not (tmp_path / "escape.txt").exists()


def test_toolchain_config_requires_nfs_library_folder(tmp_path, monkeypatch):
    import toolchain
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    library = tmp_path / "game-library"
    (library / "NFSMW_Library_PC").mkdir(parents=True)
    toolchain.configure_tool("nfs_library", str(library))
    data = toolchain.load_config()
    assert data["nfs_library"] == str(library.resolve())
    assert toolchain.detect_tools()["nfs_library"]["found"] is True
