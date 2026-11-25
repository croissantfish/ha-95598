from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
BUNDLE_DIR = ROOT_DIR / "addon" / "ha-95598" / "rootfs" / "app"


def source_files(directory: Path) -> dict[str, Path]:
    return {
        str(path.relative_to(directory)): path
        for path in directory.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }


def test_addon_source_bundle_matches_application_sources():
    for name in ("scripts", "captcha_solver", "config"):
        expected = source_files(ROOT_DIR / name)
        bundled = source_files(BUNDLE_DIR / name)
        assert bundled.keys() == expected.keys(), f"Bundled {name} file list differs; refresh rootfs/app."
        mismatched = [
            relative_path
            for relative_path in expected
            if bundled[relative_path].read_bytes() != expected[relative_path].read_bytes()
        ]
        assert not mismatched, f"Bundled files differ; refresh rootfs/app: {mismatched}"

    assert (BUNDLE_DIR / "requirements.txt").read_bytes() == (ROOT_DIR / "requirements.txt").read_bytes()
