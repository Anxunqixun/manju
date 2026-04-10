"""
组装层：从数据库取出资产记录，构建传给网关层的标准请求结构。

职责：
1. 取出 prompt、dependencies、gen_config、extra_data
2. 将 prompt 各分段按 assembly_rules.prompt_join_separator 拼接
3. 取出每个依赖资产的完整信息（asset_paths、active_index、extra_data、last_output）
4. 打包为统一请求结构
5. 不做任何变量替换
"""
import json
from pathlib import Path

from engine.db import get_asset, list_assets
from engine.config_loader import load_workflow_config, load_project_config

BASE_DIR = Path(__file__).parent.parent / "projects"


class DependencyNotMetError(Exception):
    """依赖资产尚未完成，不能组装请求。"""
    def __init__(self, unfinished_ids: list[int]):
        self.unfinished_ids = unfinished_ids
        super().__init__(f"依赖资产未完成: {unfinished_ids}")


def assemble_request(asset_id: int, project_name: str,
                     name: str = "", node_name: str = "",
                     slot_name: str = "") -> dict:
    """
    组装单个资产的生成请求。

    参数:
        asset_id     - 目标资产ID
        project_name - 项目名称
        name         - 关系记录的根节点名称（用于 key_prefix 命名）
        node_name    - 关系记录的节点名称
        slot_name    - 槽位名称

    返回: 完整的 request dict，可直接传给 Gateway.dispatch()
    """
    project_config = load_project_config(project_name)
    workflow_name = project_config.get("workflow", "")
    workflow_config = load_workflow_config(workflow_name) if workflow_name else {}
    separator = workflow_config.get("assembly_rules", {}).get("prompt_join_separator", "\n")

    asset = get_asset(project_name, asset_id)
    if asset is None:
        raise ValueError(f"资产不存在: asset_id={asset_id}")

    # 拼接 prompt 分段
    prompt_segments = asset.get("prompt") or []
    prompt_text = separator.join(
        v for seg in prompt_segments
        for v in seg.values()
        if v
    )

    # 检查并获取依赖信息
    dep_ids = asset.get("dependencies") or []
    unfinished = []
    dep_info_list = []
    for dep_id in dep_ids:
        dep_asset = get_asset(project_name, dep_id)
        if dep_asset is None:
            continue
        if dep_asset["status"] != "success":
            unfinished.append(dep_id)
        dep_info_list.append({
            "asset_id": dep_id,
            "asset_type": dep_asset["asset_type"],
            "asset_paths": dep_asset.get("asset_paths") or [[]],
            "active_index": dep_asset.get("active_index") or [0, 0],
            "extra_data": dep_asset.get("extra_data") or {},
            "last_output": dep_asset.get("last_output") or "",
        })

    if unfinished:
        raise DependencyNotMetError(unfinished)

    # 确定 output_dir 和 key_prefix
    output_dir = str(BASE_DIR / project_name / "assets" / str(asset_id))
    # key_prefix: {name}_{node_name}_{slot_name}，空则降级处理
    parts = [p for p in [name, node_name, slot_name] if p]
    key_prefix = "_".join(parts) if parts else f"asset_{asset_id}"

    return {
        "asset_id": asset_id,
        "model": asset.get("model_name") or "",
        "asset_type": asset.get("asset_type") or "",
        "prompt": prompt_text,
        "prompt_segments": prompt_segments,
        "dependencies": dep_info_list,
        "gen_config": asset.get("gen_config") or {},
        "extra_data": asset.get("extra_data") or {},
        "output_dir": output_dir,
        "key_prefix": key_prefix,
    }


def collect_dependency_tree(asset_ids: list[int], project_name: str) -> list[int]:
    """
    递归收集所有尚未完成的依赖资产ID（含传入列表本身）。
    返回按拓扑顺序排列的完整需要生成的资产ID列表。
    """
    visited = set()
    ordered = []

    def _visit(aid: int):
        if aid in visited:
            return
        visited.add(aid)
        asset = get_asset(project_name, aid)
        if asset is None:
            return
        for dep_id in (asset.get("dependencies") or []):
            dep = get_asset(project_name, dep_id)
            if dep and dep["status"] != "success":
                _visit(dep_id)
        if asset["status"] != "success":
            ordered.append(aid)

    for aid in asset_ids:
        _visit(aid)

    return ordered
