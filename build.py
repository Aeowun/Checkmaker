import os
import shutil
import subprocess
import sys
from pathlib import Path

def build():
    print("--- CheckMaker Build System ---")
    
    # 1. Clean previous builds
    print("[1/5] Cleaning build artifacts...")
    for folder in ['build', 'dist']:
        if os.path.exists(folder):
            shutil.rmtree(folder)
    
    # 2. Verify dependencies
    print("[2/5] Verifying dependencies...")
    try:
        import webview
        import PyInstaller
    except ImportError:
        print("Error: Missing dependencies. Run 'pip install -r requirements-dev.txt'")
        return

    # 3. Run tests
    print("[3/5] Running tests...")
    test_result = subprocess.run([sys.executable, "-m", "pytest", "tests/test_checkmaker.py"], capture_output=True, text=True)
    if test_result.returncode != 0:
        print("Error: Tests failed!")
        print(test_result.stdout)
        print(test_result.stderr)
        return
    print("✓ Tests passed.")

    # 4. Build executable
    print("[4/5] Building executable with PyInstaller...")
    # --add-data "assets;assets" (Windows syntax)
    # --onefile
    # --noconsole
    cmd = [
        "pyinstaller",
        "--noconsole",
        "--onefile",
        "--add-data", "assets;assets",
        "--name", "CheckMaker",
        "checkmaker.py"
    ]
    
    build_result = subprocess.run(cmd)
    if build_result.returncode != 0:
        print("Error: PyInstaller build failed!")
        return

    # 5. Verification
    exe_path = Path("dist/CheckMaker.exe")
    if exe_path.exists():
        print(f"\n✓ Build successful!")
        print(f"Location: {exe_path.absolute()}")
    else:
        print("Error: Executable not found in dist folder.")

if __name__ == "__main__":
    build()
