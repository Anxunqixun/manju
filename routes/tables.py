"""
关系表通用路由：/table/<table_name>
"""
import json
from flask import (Blueprint, jsonify, redirect, render_template,
                   request, session, url_for)

from engine.config_loader import (load_project_config, load_workflow_config,
                                   get_slot_default)
from engine.db import (list_relation_records, get_relation_record,
                        create_relation_record, update_relation_record,
                        delete_relation_record, create_asset, get_asset,
                        list_assets, update_asset, delete_asset)
from ai_modules.scanner import get_models_by_asset_type

bp = Blueprint("tables", __name__)


@bp.route("/table/<table_display_name>")
def table_page(table_display_name: str):
    current = session.get("current_project", "")
    if not current:
        return redirect(url_for("projects.index"))

    project_config = load_project_config(current)
    workflow_name = project_config.get("workflow", "")
    workflow_config = load_workflow_config(workflow_name) if workflow_name else {}

    table_config = workflow_config.get("relation_tables", {}).get(table_display_name)
    if table_config is None:
        return f"关系表 {table_display_name} 不存在", 404

    table_name = f"rel_{table_display_name}"
    records = list_relation_records(current, table_name)

    # 为每条记录附加各槽位资产的状态
    enriched_records = []
    for rec in records:
        slots_status = {}
        ref_ids_data = rec.get("ref_ids") or []
        for slot_entry in ref_ids_data:
            for slot_name, ids in slot_entry.items():
                for aid in ids:
                    asset = get_asset(current, aid)
                    if asset:
                        slots_status[slot_name] = {
                            "asset_id": aid,
                            "status": asset["status"],
                            "last_output": asset.get("last_output", ""),
                            "asset_type": asset["asset_type"],
                            "asset_paths": asset.get("asset_paths") or [[]],
                            "active_index": asset.get("active_index") or [0, 0],
                        }
        rec["slots_status"] = slots_status
        enriched_records.append(rec)

    # 构建树结构：根节点 + 子节点链
    chains = _build_chains(enriched_records)

    return render_template("relation_table.html",
                           current_project=current,
                           table_display_name=table_display_name,
                           table_name=table_name,
                           table_config=table_config,
                           chains=chains,
                           all_records=enriched_records,
                           project_config=project_config,
                           workflow_config=workflow_config)


def _build_chains(records: list[dict]) -> list[list[dict]]:
    """将平铺的记录列表构建为横排链条列表。"""
    id_map = {r["id"]: r for r in records}
    root_nodes = [r for r in records if r.get("parent_id") is None]
    chains = []
    for root in root_nodes:
        chain = [root]
        # 收集所有 parent_id = root.id 或沿链的子节点
        # 链条模式：每个节点只有一个子
        current = root
        while True:
            children = [r for r in records
                        if r.get("parent_id") == current["id"]]
            if not children:
                break
            # strict 模式：第一个子节点
            child = children[0]
            chain.append(child)
            current = child
        chains.append(chain)
    return chains
