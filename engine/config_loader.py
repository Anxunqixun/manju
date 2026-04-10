"""
配置加载层：工作流配置 + 项目配置，带 mtime 缓存。
"""
import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent

# 模块级缓存 {name: (mtime, config_dict)}
_workflow_cache: dict[str, tuple[float, dict]] = {}
_project_cache: dict[str, tuple[float, dict]] = {}


def _workflows_dir() -> Path:
    return BASE_DIR / "config" / "workflows"


def _project_config_path(project_name: str) -> Path:
    return BASE_DIR / "projects" / project_name / "project_config.json"


def list_workflows() -> list[str]:
    """返回所有可用工作流名称（去掉 .json 后缀）。"""
    d = _workflows_dir()
    if not d.exists():
        return []
    return [p.stem for p in d.glob("*.json")]


def load_workflow_config(workflow_name: str) -> dict:
    """加载工作流配置，使用 mtime 缓存。"""
    path = _workflows_dir() / f"{workflow_name}.json"
    if not path.exists():
        raise FileNotFoundError(f"工作流配置不存在: {path}")
    mtime = path.stat().st_mtime
    cached = _workflow_cache.get(workflow_name)
    if cached is None or cached[0] != mtime:
        with open(path, encoding="utf-8") as f:
            config = json.load(f)
        _workflow_cache[workflow_name] = (mtime, config)
    return _workflow_cache[workflow_name][1]


def load_project_config(project_name: str) -> dict:
    """加载项目配置，使用 mtime 缓存。"""
    path = _project_config_path(project_name)
    if not path.exists():
        return {}
    mtime = path.stat().st_mtime
    cached = _project_cache.get(project_name)
    if cached is None or cached[0] != mtime:
        with open(path, encoding="utf-8") as f:
            config = json.load(f)
        _project_cache[project_name] = (mtime, config)
    return _project_cache[project_name][1]


def save_project_config(project_name: str, config: dict):
    """保存项目配置并更新缓存。"""
    path = _project_config_path(project_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    # 失效缓存
    _project_cache.pop(project_name, None)


def list_projects() -> list[dict]:
    """返回所有项目基本信息列表。"""
    projects_dir = BASE_DIR / "projects"
    if not projects_dir.exists():
        return []
    result = []
    for p in sorted(projects_dir.iterdir()):
        if p.is_dir():
            cfg = load_project_config(p.name)
            result.append({
                "name": p.name,
                "workflow": cfg.get("workflow", ""),
                "project_name": cfg.get("project_name", p.name),
            })
    return result


def get_slot_default(project_config: dict, table_display_name: str,
                     slot_name: str) -> dict:
    """从项目配置中取指定槽位的默认值，不存在时返回空dict。"""
    return (
        project_config
        .get("defaults", {})
        .get(table_display_name, {})
        .get(slot_name, {})
    )


def get_global_thread_limit(project_config: dict) -> int:
    return project_config.get("global_thread_limit", 3)
