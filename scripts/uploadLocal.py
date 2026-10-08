from __future__ import annotations

import os
import shutil
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
MODIFIED_DIR = ROOT_DIR / "modified"


def main() -> None:

    target_dir = os.environ.get("LOCAL_RESOURCE_DIR", "").strip()

    if not target_dir:
        raise RuntimeError(
            "缺少环境变量: LOCAL_RESOURCE_DIR\n"
            "请设置为 Nginx 静态资源根目录，"
            "例如 /www/wwwroot/ba-resources"
        )

    target = Path(target_dir)

    if not target.is_dir():
        raise RuntimeError(
            f"目标目录不存在: {target}"
        )

    if not MODIFIED_DIR.is_dir():
        raise RuntimeError(
            "modified/ 不存在"
        )

    files = sorted(
        path
        for path in MODIFIED_DIR.rglob("*")
        if path.is_file()
    )

    print(f"准备部署 {len(files)} 个文件到 {target}")

    copied = 0

    for local_path in files:

        relative = local_path.relative_to(MODIFIED_DIR)
        dest = target / relative

        dest.parent.mkdir(parents=True, exist_ok=True)

        shutil.copy2(local_path, dest)

        print(f"[DEPLOYED] {relative.as_posix()}")

        copied += 1

    print()
    print(f"本地部署完成，共 {copied} 个文件 -> {target}")


if __name__ == "__main__":
    main()
