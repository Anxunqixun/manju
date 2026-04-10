"""
AI模块自动扫描注册。

启动时扫描 ai_modules/ 下所有子目录，读取 manifest.json + 动态导入 module.py。
提供两个全局查询接口：
  - get_registry() -> {model_name: module}
  - get_manifest_list() -> [{platform, models:[...]}]
"""
import importlib.util
import json
from pathlib import Path

_MODULES_DIR = Path(__file__).parent

# {model_name: module_object}
_registry: dict = {}
# [{platform, models:[manifest model entries]}]
_manifests: list = []


def scan_modules() -> dict:
    """扫描并注册所有AI模块，返回 {model_name: module} 注册表。"""
    global _registry, _manifests
    _registry = {}
    _manifests = []

    for manifest_path in sorted(_MODULES_DIR.glob("*/manifest.json")):
        module_dir = manifest_path.parent
        module_py = module_dir / "module.py"
        if not module_py.exists():
            continue

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[scanner] 读取 {manifest_path} 失败: {e}")
            continue

        # 动态导入 module.py
        spec = importlib.util.spec_from_file_location(
            f"ai_modules.{module_dir.name}.module",
            module_py
        )
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as e:
            print(f"[scanner] 加载 {module_py} 失败: {e}")
            continue

        # 注册每个 model
        for model_def in manifest.get("models", []):
            model_name = model_def.get("model_name")
            if model_name:
                _registry[model_name] = mod

        _manifests.append(manifest)

    print(f"[scanner] 已注册模型: {list(_registry.keys())}")
    return _registry


def get_registry() -> dict:
    return _registry


def get_manifest_list() -> list:
    """返回所有平台的 manifest 列表（含完整 models 数组）。"""
    return _manifests


def get_models_by_asset_type(asset_type: str) -> list[dict]:
    """按 asset_type 过滤，返回可用模型定义列表。"""
    result = []
    for manifest in _manifests:
        for model_def in manifest.get("models", []):
            if model_def.get("asset_type") == asset_type:
                result.append({
                    "model_name": model_def["model_name"],
                    "platform": manifest.get("platform", ""),
                    "description": model_def.get("description", ""),
                    "params_schema": model_def.get("params_schema", {}),
                })
    return result
