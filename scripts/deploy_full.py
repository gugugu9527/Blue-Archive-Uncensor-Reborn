from __future__ import annotations

import hashlib
import json
import random
import shutil
import sys
import time
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


ROOT_DIR = Path(__file__).resolve().parent.parent

SCRIPTS_DIR = ROOT_DIR / "scripts"
MODIFIED_DIR = ROOT_DIR / "modified"
OUT_DIR = ROOT_DIR / "out"
EXCLUSIONS_DIR = ROOT_DIR / "assetexclusions"

OFFICIAL_BUNDLE_INFO_BASE = (
    "https://static.bluearchive-cn.com"
    "/prodm39/AssetBundles/Catalog"
)

OFFICIAL_BUNDLE_BASE = (
    "https://static.bluearchive-cn.com"
    "/prodm39/AssetBundles/Android"
)

OFFICIAL_TABLE_MANIFEST_BASE = (
    "https://static.bluearchive-cn.com"
    "/prodm39/Manifest/TableBundles"
)

OFFICIAL_TABLE_BUNDLE_BASE = (
    "https://static.bluearchive-cn.com"
    "/prodm39/pool/TableBundles"
)


def create_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


SESSION = create_session()


def download_bytes(url: str, output: Path, max_attempts: int = 6) -> None:
    print(f"[DOWNLOAD] {url}")
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_name(output.name + ".part")
    if tmp.exists():
        tmp.unlink()

    last_exc = None
    for attempt in range(1, max_attempts + 1):
        resume_from = tmp.stat().st_size if tmp.exists() else 0
        headers = {"Accept-Encoding": "identity"}
        if resume_from > 0:
            headers["Range"] = f"bytes={resume_from}-"

        try:
            with SESSION.get(url, headers=headers, stream=True, timeout=(15, 120)) as response:
                if response.status_code == 416:
                    tmp.unlink(missing_ok=True)
                    raise RuntimeError("416 Range Not Satisfiable")
                response.raise_for_status()

                if response.status_code == 206:
                    mode = "ab"
                else:
                    mode = "wb"
                    resume_from = 0

                content_length = response.headers.get("Content-Length")
                expected = resume_from + int(content_length) if content_length else None

                with tmp.open(mode) as f:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)

            actual = tmp.stat().st_size
            if expected and actual != expected:
                raise RuntimeError(f"文件大小不一致: expected={expected}, actual={actual}")

            tmp.replace(output)
            return

        except Exception as exc:
            last_exc = exc
            wait = min(2 ** attempt, 30)
            print(f"  [RETRY {attempt}/{max_attempts}] {type(exc).__name__}: {exc}；{wait}s 后重试")
            time.sleep(wait)

    tmp.unlink(missing_ok=True)
    raise RuntimeError(f"下载失败（已重试 {max_attempts} 次）: {url}") from last_exc


def download_json(url: str, output: Path) -> dict:
    download_bytes(url, output)
    with output.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def calculate_md5(path: Path) -> str:
    md5 = hashlib.md5()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            md5.update(chunk)
    return md5.hexdigest()


def random_digits(length: int) -> str:
    return str(random.SystemRandom().randint(10 ** (length - 1), 10 ** length - 1))


def main() -> int:
    print("=" * 72)
    print("全量部署模式")
    print("=" * 72)

    # 获取官方版本
    from getCurrentVersion import get_current_versions
    versions = get_current_versions()
    resource_version = versions["ResourceVersion"]
    table_version = versions["TableVersion"]

    print(f"ResourceVersion: {resource_version}")
    print(f"TableVersion: {table_version}")

    # 清理
    if MODIFIED_DIR.exists():
        shutil.rmtree(MODIFIED_DIR)
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)

    # 下载 catalog
    catalog_dir = MODIFIED_DIR / "AssetBundles" / "Catalog" / resource_version / "Android"
    catalog_dir.mkdir(parents=True, exist_ok=True)
    catalog_path = catalog_dir / "bundleDownloadInfo.json"

    catalog_url = f"{OFFICIAL_BUNDLE_INFO_BASE}/{resource_version}/Android/bundleDownloadInfo.json"
    catalog = download_json(catalog_url, catalog_path)

    bundles = catalog.get("BundleFiles", [])
    print(f"\n共 {len(bundles)} 个 Bundle 需要下载")

    # 排除项
    exclusions = {}
    if EXCLUSIONS_DIR.is_dir():
        for path in EXCLUSIONS_DIR.rglob("*.bundle"):
            if path.is_file():
                exclusions[path.name] = path
    print(f"排除项 Bundle: {len(exclusions)}")

    # 下载所有 Bundle
    downloaded = 0
    skipped = 0

    for entry in bundles:
        name = entry.get("Name")
        if not name or not name.lower().endswith(".bundle"):
            continue

        if name in exclusions:
            print(f"[SKIP-EXCLUSION] {name}")
            skipped += 1
            continue

        output = ROOT_DIR / name
        if output.exists():
            print(f"[SKIP-EXISTS] {name}")
            skipped += 1
            continue

        url = f"{OFFICIAL_BUNDLE_BASE}/{name}"
        try:
            download_bytes(url, output)
            downloaded += 1
        except Exception as e:
            print(f"[ERROR] {name}: {e}")

    print(f"\n下载完成: {downloaded} 个，跳过: {skipped} 个")

    # 运行贴图替换
    print("\n" + "=" * 72)
    print("运行贴图替换...")
    print("=" * 72)
    import subprocess
    subprocess.run([sys.executable, str(SCRIPTS_DIR / "replaceTexture2D.py")], cwd=ROOT_DIR, check=True)

    # 运行 MD5 重算
    print("\n" + "=" * 72)
    print("重算 MD5...")
    print("=" * 72)
    subprocess.run([sys.executable, str(SCRIPTS_DIR / "calculateMD5.py")], cwd=ROOT_DIR, check=True)

    # 复制修改后的 Bundle 到 modified
    bundle_dest = MODIFIED_DIR / "AssetBundles" / "Android"
    bundle_dest.mkdir(parents=True, exist_ok=True)

    copied = 0
    for path in OUT_DIR.rglob("*.bundle"):
        if path.is_file():
            shutil.copy2(path, bundle_dest / path.name)
            copied += 1

    # 复制排除项
    for name, path in exclusions.items():
        shutil.copy2(path, bundle_dest / name)
        copied += 1

    print(f"\n复制 Bundle: {copied} 个")

    # bundleDownloadInfo.hash
    bundle_hash_path = catalog_dir / "bundleDownloadInfo.hash"
    bundle_hash_path.write_text(random_digits(9), encoding="utf-8")

    # 下载 TableManifest
    table_manifest_dir = MODIFIED_DIR / "Manifest" / "TableBundles" / table_version
    table_manifest_dir.mkdir(parents=True, exist_ok=True)
    table_manifest_path = table_manifest_dir / "TableManifest"

    table_manifest_url = f"{OFFICIAL_TABLE_MANIFEST_BASE}/{table_version}/TableManifest"
    table_manifest = download_json(table_manifest_url, table_manifest_path)

    # 下载 ExcelDB
    excel_entry = table_manifest["Table"]["ExcelDB.db"]
    excel_crc = str(excel_entry["Crc"]).lower()

    excel_path = ROOT_DIR / "ExcelDB_new.db"
    official_excel_url = f"{OFFICIAL_TABLE_BUNDLE_BASE}/{excel_crc[:2]}/{excel_crc}"
    download_bytes(official_excel_url, excel_path)

    # 运行 ExcelDB 补丁
    print("\n" + "=" * 72)
    print("运行 ExcelDB 补丁...")
    print("=" * 72)

    # 下载旧 ExcelDB
    old_excel_url = "https://mx.infastra.de5.net/prodm39/pool/TableBundles/51/517bb1fb1aa4d980ab87644ec10ecb2a"
    old_excel_path = ROOT_DIR / "ExcelDB_old.db"
    download_bytes(old_excel_url, old_excel_path)

    subprocess.run([sys.executable, str(SCRIPTS_DIR / "updateExcelDB.py")], cwd=ROOT_DIR, check=True)

    # 计算 ExcelDB MD5
    excel_md5 = calculate_md5(excel_path)
    excel_size = excel_path.stat().st_size
    print(f"\nExcelDB MD5: {excel_md5}")
    print(f"ExcelDB Size: {excel_size}")

    # 移动 ExcelDB
    excel_target = MODIFIED_DIR / "pool" / "TableBundles" / excel_md5[:2] / excel_md5
    excel_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(excel_path), str(excel_target))

    # 更新 TableManifest
    excel_entry["Crc"] = excel_md5
    excel_entry["Size"] = excel_size
    save_json(table_manifest_path, table_manifest)

    # TableManifestHash
    table_manifest_hash_path = table_manifest_dir / "TableManifestHash"
    table_manifest_hash_path.write_text(random_digits(10), encoding="utf-8")

    # 清理临时文件
    for path in ROOT_DIR.glob("*.bundle"):
        if path.is_file():
            path.unlink()
    for filename in ("ExcelDB_old.db", "ExcelDB_new_backup.db"):
        path = ROOT_DIR / filename
        if path.exists():
            path.unlink()

    print("\n" + "=" * 72)
    print("全量部署准备完成！")
    print("=" * 72)
    print(f"ResourceVersion: {resource_version}")
    print(f"TableVersion: {table_version}")
    print(f"Bundle 总数: {len(bundles)}")
    print(f"输出目录: {MODIFIED_DIR}")
    print("=" * 72)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
