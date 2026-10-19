"""中文用途：按编号顺序合并工作台 JS/CSS parts，并同步 Frappe 部署副本。"""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


import hashlib
import json
import re
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def _part_key(path: Path) -> tuple[float, str]:
    match = re.match(r"^(\d+)", path.name)
    number = int(match.group(1)) if match else 10**9
    # 35-workbench-view 历史上依赖 40-vouchers 中先声明的方法；保留既有运行顺序。
    if path.suffix == ".js" and number == 35:
        return (40.5, path.name)
    return (float(number), path.name)


def _normalized_part(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")


def _joined(parts: list[Path]) -> str:
    return "\n\n".join(_normalized_part(path) for path in parts) + "\n"


def _write_if_changed(path: Path, content: str, changed: list[str]) -> None:
    encoded = content.encode("utf-8")
    if path.exists() and path.read_bytes() == encoded:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded)
    changed.append(str(path))


def build_workbench_assets(package_root: str | Path = PACKAGE_ROOT) -> dict:
    root = Path(package_root).resolve()
    source_dir = root / "page" / "overseas_cost_workbench"
    parts_dir = source_dir / "parts"
    deployed_dir = root / "overseas_costing" / "page" / "overseas_cost_workbench"
    changed: list[str] = []
    digests = {}
    inputs = {}
    outputs = {}
    for extension in ("js", "css"):
        parts = sorted(parts_dir.glob(f"*.{extension}"), key=_part_key)
        if not parts:
            raise FileNotFoundError(_("未找到工作台 {0} parts：{1}").format(extension.upper(), parts_dir))
        content = _joined(parts)
        source_path = source_dir / f"overseas_cost_workbench.{extension}"
        deployed_path = deployed_dir / source_path.name
        _write_if_changed(source_path, content, changed)
        _write_if_changed(deployed_path, content, changed)
        inputs[extension] = [path.name for path in parts]
        outputs[extension] = [str(source_path), str(deployed_path)]
        digests[extension] = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return {
        "inputs": inputs,
        "outputs": outputs,
        "digests": digests,
        "changed": changed,
    }


if __name__ == "__main__":
    print(json.dumps(build_workbench_assets(), ensure_ascii=False, indent=2))
