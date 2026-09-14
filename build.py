import hashlib
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BUILD_DIR = ROOT / "build"
DIST_DIR = ROOT / "dist"
SPEC_FILE = ROOT / "CheckMaker.spec"
EXE_PATH = DIST_DIR / "CheckMaker.exe"


def run(command: list[str]) -> None:
    print(">", " ".join(command))
    subprocess.run(command, cwd=ROOT, check=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def main() -> None:
    print("--- CheckMaker Build System ---")

    print("[1/5] Cleaning build artifacts...")
    for path in (BUILD_DIR, DIST_DIR):
        if path.exists():
            shutil.rmtree(path)

    if not SPEC_FILE.is_file():
        raise FileNotFoundError(f"Missing PyInstaller spec: {SPEC_FILE}")

    print("[2/5] Running tests...")
    run([sys.executable, "-m", "pytest"])

    print("[3/5] Building executable...")
    run([
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        str(SPEC_FILE),
    ])

    print("[4/5] Verifying artifact...")
    if not EXE_PATH.is_file():
        raise FileNotFoundError(f"Expected executable not found: {EXE_PATH}")

    size = EXE_PATH.stat().st_size
    if size <= 0:
        raise RuntimeError("Generated executable is empty.")

    print("[5/5] Artifact integrity...")
    digest = sha256(EXE_PATH)

    print()
    print("BUILD SUCCESS")
    print(f"Artifact : {EXE_PATH}")
    print(f"Size     : {size:,} bytes")
    print(f"SHA-256  : {digest}")


if __name__ == "__main__":
    main()
