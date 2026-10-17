"""Assemble installed, locked dependencies into a relocatable Windows ZIP. No tests."""
import argparse
from email.parser import Parser
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REVISION = "44399559930365213510b1ee2eb15ded83374f0e"
TIKA_JAR = "tika-app-3.3.2.jar"
MODEL_FILES = (
    "config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json",
    "1_Pooling/config.json", "onnx/model_quint8_avx2.onnx",
)
FORMULA_FILES = ("image_resizer.onnx", "encoder.onnx", "decoder.onnx", "tokenizer.json")
# Interpreter parts the server never imports: installers, Tk GUI and IDLE.
BASE_LIB_EXCLUDED = {"site-packages", "ensurepip", "idlelib", "tkinter", "turtledemo", "test"}
BASE_DLLS_EXCLUDED = {"_tkinter.pyd", "tcl86t.dll", "tk86t.dll"}
# Loaded through reflection, service files or charset lookup, so jdeps cannot see them.
JAVA_EXTRA_MODULES = ("jdk.charsets", "jdk.localedata", "jdk.zipfs", "jdk.crypto.ec")


def is_notice(name):
    return name.lower().startswith(("license", "licence", "copying", "notice"))


def copy_tree(source, target, *, python=False, node=False):
    def ignore(directory, names):
        excluded = {"__pycache__", ".pytest_cache", ".git", ".env", ".env.local"}
        if python:
            excluded.update({"_virtualenv.py", "_virtualenv.pth"})
        skipped = [name for name in names if name in excluded or name.endswith((".pyc", ".pyo"))]
        if python:
            # OpenCV's FFmpeg plugin only serves video I/O; image OCR never loads it.
            skipped += [name for name in names if name.startswith("opencv_videoio_ffmpeg") and name.endswith(".dll")]
        if node:
            # Types, source maps, TypeScript sources and docs are never loaded by Node at run time.
            skipped += [name for name in names if not is_notice(name) and name.endswith(
                (".d.ts", ".d.mts", ".d.cts", ".map", ".md", ".ts", ".mts", ".cts"))]
        return skipped
    shutil.copytree(source, target, ignore=ignore, dirs_exist_ok=True)


def copy_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def release_environment(path):
    # Install exactly the locked runtime packages: no dev group, and no Xet
    # downloader because the release ships its models and never downloads them.
    environment = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(path)}
    subprocess.run(["uv", "sync", "--python", "3.11", "--frozen", "--no-dev", "--no-install-package", "hf-xet"],
                   cwd=ROOT, env=environment, check=True)
    return path


def python_runtime(venv, target):
    # A venv's python.exe depends on absolute paths: bundle its base interpreter instead.
    config = dict(line.split(" = ", 1) for line in (venv / "pyvenv.cfg").read_text().splitlines() if " = " in line)
    base = Path(config["home"])
    for name in (base / "Lib").iterdir():
        if name.name in BASE_LIB_EXCLUDED:
            continue
        if name.is_dir():
            copy_tree(name, target / "Lib" / name.name, python=True)
        elif not name.name.endswith((".pyc", ".pyo")):
            copy_file(name, target / "Lib" / name.name)
    for source in (base / "DLLs").iterdir():
        if source.is_file() and source.name not in BASE_DLLS_EXCLUDED:
            copy_file(source, target / "DLLs" / source.name)
    for pattern in ("*.exe", "*.dll", "LICENSE*"):
        for source in base.glob(pattern):
            copy_file(source, target / source.name)
    copy_tree(venv / "Lib" / "site-packages", target / "Lib" / "site-packages", python=True)


def java_runtime(java_home, bundle):
    tools = java_home / "bin"
    tika = bundle / ".runtime" / "tika"
    classes = tika / "classes"
    # Precompile the DOC helper so the release needs no compiler (tika.py prefers these classes).
    subprocess.run([str(tools / "javac.exe"), "-encoding", "UTF-8", "-nowarn", "-cp", str(tika / TIKA_JAR),
                    "-d", str(classes), str(ROOT / "backend" / "app" / "LegacyWordScripts.java")], check=True)
    with tempfile.TemporaryDirectory(prefix="zhiye-jdeps-") as directory:
        # jdeps misreads the fat jar's manifest, so analyse its extracted classes instead,
        # including the multi-release variants a modern runtime loads.
        with zipfile.ZipFile(tika / TIKA_JAR) as archive:
            for entry in archive.namelist():
                if entry.endswith(".class") and not entry.endswith("module-info.class"):
                    archive.extract(entry, directory)
        found = subprocess.check_output([str(tools / "jdeps.exe"), "--print-module-deps", "--ignore-missing-deps",
                                         directory, str(classes)], text=True)
    available = subprocess.check_output([str(tools / "java.exe"), "--list-modules"], text=True)
    available = {line.split("@", 1)[0].strip() for line in available.splitlines()}
    modules = sorted(set(found.strip().splitlines()[-1].split(",")) | (set(JAVA_EXTRA_MODULES) & available))
    print("Java modules:", ",".join(modules), flush=True)
    feature = int(re.search(r'JAVA_VERSION="(\d+)', (java_home / "release").read_text()).group(1))
    subprocess.run([str(tools / "jlink.exe"), "--add-modules", ",".join(modules), "--strip-debug",
                    "--no-header-files", "--no-man-pages", "--compress", "zip-6" if feature >= 21 else "2",
                    "--output", str(bundle / ".runtime" / "java")], check=True)


def licenses(bundle):
    notices = bundle / "third-party"
    copy_tree(ROOT / ".cache" / "packaging" / "notices", notices)
    packages = []
    for info in sorted((bundle / ".runtime" / "python" / "Lib" / "site-packages").glob("*.dist-info")):
        metadata = Parser().parsestr((info / "METADATA").read_text(encoding="utf-8"))
        packages.append({"name": metadata["Name"], "version": metadata["Version"],
                         "license": metadata["License-Expression"] or metadata["License"]})
    (notices / "python-packages.json").write_text(json.dumps(packages, ensure_ascii=False, indent=2), encoding="utf-8")
    # Keep licenses of frontend libraries whose code is compiled into the static page.
    for name in ("vue", "@vue", "katex", "markdown-it", "@babel", "entities", "@jridgewell",
                 "linkify-it", "mdurl", "uc.micro", "punycode.js", "argparse", "source-map-js", "estree-walker"):
        source = ROOT / "frontend" / "node_modules" / name
        if not source.is_dir():
            continue
        for path in source.rglob("*"):
            if path.is_file() and path.name.lower().startswith(("license", "copying", "notice")):
                copy_file(path, notices / "frontend" / name / path.relative_to(source))
    # Tika is a JAR/ZIP containing the upstream notices for its bundled dependencies.
    jar = bundle / ".runtime" / "tika" / TIKA_JAR
    with zipfile.ZipFile(jar) as archive:
        for name in ("META-INF/LICENSE", "META-INF/NOTICE"):
            (notices / ("Tika-" + Path(name).name + ".txt")).write_bytes(archive.read(name))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--java-home", type=Path, required=True,
                        help="Redistributable Temurin JDK folder; only a jlink-reduced runtime is bundled")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9][0-9A-Za-z.-]*", args.version):
        raise ValueError("Use a version containing only letters, numbers, dots and hyphens")
    if not all((args.java_home / "bin" / tool).is_file() for tool in ("javac.exe", "jdeps.exe", "jlink.exe")):
        raise ValueError("A complete JDK (javac, jdeps and jlink) is required to build the Java runtime")
    name = f"Zhiye-{args.version}-windows-x64"
    output = ROOT / "dist"
    bundle = output / name
    output.mkdir(exist_ok=True)
    # Refuse to overwrite an existing release rather than delete someone else's files.
    bundle.mkdir()
    print("Compiling the release frontend (no tests or type-check command)...", flush=True)
    environment = {**os.environ, "VITE_API_BASE_URL": "http://127.0.0.1:8000"}
    subprocess.run(["node", "node_modules/vite/bin/vite.js", "build"], cwd=ROOT / "frontend", env=environment, check=True)
    print("Bundling backend, Python and model runtime...", flush=True)
    copy_tree(ROOT / "backend" / "app", bundle / "backend" / "app")
    copy_tree(ROOT / "frontend" / "dist", bundle / "frontend" / "dist")
    for name in ("portable_server.py", "portable-start.ps1"):
        copy_file(ROOT / "scripts" / name, bundle / "scripts" / name)
    copy_file(ROOT / ".env.example", bundle / ".env.example")
    for name in ("USAGE.md", "MODEL_CONFIG.md", "ACKNOWLEDGEMENTS.md"):
        copy_file(ROOT / "docs" / name, bundle / "docs" / name)
    staging = output / f".{bundle.name}-venv"
    try:
        python_runtime(release_environment(staging), bundle / ".runtime" / "python")
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    for name in ("bridge.mjs", "oauth-bridge.mjs", "credential-store.mjs", "package.json", "package-lock.json"):
        copy_file(ROOT / "model-runtime" / name, bundle / "model-runtime" / name)
    copy_tree(ROOT / "model-runtime" / "node_modules", bundle / "model-runtime" / "node_modules", node=True)
    node = Path(shutil.which("node"))
    copy_file(node, bundle / ".runtime" / "node" / "node.exe")
    print("Bundling Java, Tika and public OCR / embedding models...", flush=True)
    copy_file(ROOT / ".runtime" / "tika" / TIKA_JAR, bundle / ".runtime" / "tika" / TIKA_JAR)
    java_runtime(args.java_home, bundle)
    snapshot = Path("hub/models--ibm-granite--granite-embedding-311m-multilingual-r2/snapshots") / REVISION
    for name in MODEL_FILES:
        copy_file(ROOT / ".cache" / "huggingface" / snapshot / name, bundle / ".runtime" / "huggingface" / snapshot / name)
    for name in FORMULA_FILES:
        copy_file(ROOT / ".cache" / "packaging" / "formula" / name, bundle / ".runtime" / "formula-models" / name)
    licenses(bundle)
    (bundle / "启动知页.cmd").write_text(
        '@echo off\r\ncd /d "%~dp0"\r\npowershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\\portable-start.ps1"\r\nif errorlevel 1 pause\r\n', encoding="ascii")
    copy_file(ROOT / "docs" / "PORTABLE.md", bundle / "使用说明.md")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    manifest = {"version": args.version, "source_commit": revision, "platform": "windows-x64",
                "runtime_tested": False, "embedding_revision": REVISION, "user_data": "%LOCALAPPDATA%/Zhiye"}
    (bundle / "release.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("Writing the release ZIP...", flush=True)
    archive_path = output / f"{bundle.name}.zip"
    with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(output).as_posix())
    with archive_path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    (output / f"{bundle.name}.sha256").write_text(f"{digest}  {archive_path.name}\n", encoding="ascii")
    print(f"Created {archive_path} ({archive_path.stat().st_size / 1024**2:.1f} MiB)", flush=True)


if __name__ == "__main__":
    main()
