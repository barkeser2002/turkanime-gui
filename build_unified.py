#!/usr/bin/env python3
"""
Build script for TurkAnime GUI
Builds Next.js frontend and prepares for PyInstaller bundling
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path

def run_command(cmd, cwd=None, shell=True):
    """Run a command and handle errors"""
    print(f"Running: {cmd}")
    result = subprocess.run(cmd, shell=shell, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error: {result.stderr}")
        return False
    print(result.stdout)
    return True

def build_frontend():
    """Build Next.js frontend as static export"""
    print("\n=== Building Next.js Frontend ===")
    
    frontend_dir = Path(__file__).parent.parent / "frontend"
    
    if not frontend_dir.exists():
        print("Frontend directory not found, skipping frontend build")
        return True
    
    # Install dependencies
    print("Installing frontend dependencies...")
    if not run_command("npm install", cwd=str(frontend_dir)):
        print("Failed to install frontend dependencies")
        return False
    
    # Build frontend
    print("Building frontend...")
    if not run_command("npm run build", cwd=str(frontend_dir)):
        print("Failed to build frontend")
        return False
    
    # Copy build output
    build_output = frontend_dir / "out"
    if not build_output.exists():
        build_output = frontend_dir / ".next"
    
    target_dir = Path(__file__).parent.parent / "frontend_build"
    
    if target_dir.exists():
        shutil.rmtree(target_dir)
    
    if build_output.exists():
        print(f"Copying frontend build to {target_dir}")
        shutil.copytree(build_output, target_dir)
        print("Frontend build copied successfully")
    else:
        print("Warning: Frontend build output not found")
        return False
    
    return True

def clean_build():
    """Clean build artifacts"""
    print("\n=== Cleaning Build Artifacts ===")
    
    dirs_to_clean = [
        "build",
        "dist",
        "__pycache__",
        "frontend_build",
    ]
    
    for dir_name in dirs_to_clean:
        dir_path = Path(__file__).parent.parent / dir_name
        if dir_path.exists():
            print(f"Removing {dir_path}")
            shutil.rmtree(dir_path)
    
    print("Clean complete")

def build_pyinstaller():
    """Build executable with PyInstaller"""
    print("\n=== Building Executable with PyInstaller ===")
    
    spec_file = Path(__file__).parent.parent / "turkanime-gui-unified.spec"
    
    if not spec_file.exists():
        print(f"Error: Spec file not found: {spec_file}")
        return False
    
    if not run_command(f"pyinstaller {spec_file}"):
        print("Failed to build with PyInstaller")
        return False
    
    print("PyInstaller build complete")
    return True

def main():
    """Main build process"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Build TurkAnime GUI")
    parser.add_argument("--clean", action="store_true", help="Clean build artifacts before building")
    parser.add_argument("--skip-frontend", action="store_true", help="Skip frontend build")
    parser.add_argument("--skip-pyinstaller", action="store_true", help="Skip PyInstaller build")
    
    args = parser.parse_args()
    
    # Change to project root
    os.chdir(Path(__file__).parent.parent)
    
    if args.clean:
        clean_build()
    
    # Build frontend
    if not args.skip_frontend:
        if not build_frontend():
            print("\n❌ Frontend build failed")
            sys.exit(1)
    
    # Build with PyInstaller
    if not args.skip_pyinstaller:
        if not build_pyinstaller():
            print("\n❌ PyInstaller build failed")
            sys.exit(1)
    
    print("\n✅ Build completed successfully!")
    print(f"Executable available in: dist/")

if __name__ == "__main__":
    main()
