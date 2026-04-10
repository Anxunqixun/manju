"""
全局仪表盘路由：/dashboard
"""
import json
from flask import (Blueprint, jsonify, redirect, render_template,
                   request, session, url_for, current_app)

from engine.config_loader import load_project_config, load_workflow_config
from engine.db import list_relation_tables, list_assets, list_relation_records

bp = Blueprint("dashboard", __name__)


@bp.route("/dashboard")
def dashboard():
    current = session.get("current_project", "")
    if not current:
        return redirect(url_for("projects.index"))

    project_config = load_project_config(current)
    workflow_name = project_config.get("workflow", "")
    workflow_config = load_workflow_config(workflow_name) if workflow_name else {}

    # 统计每张关系表的进度
    table_stats = []
    for table_display_name in workflow_config.get("relation_tables", {}).keys():
        table_name = f"rel_{table_display_name}"
        records = list_relation_records(current, table_name)
        total_assets = 0
        done_assets = 0
        for rec in records:
            ref_ids_data = rec.get("ref_ids") or []
            for slot_entry in ref_ids_data:
                for slot_name, ids in slot_entry.items():
                    for aid in ids:
                        total_assets += 1
                        asset = next(
                            (a for a in list_assets(current, table_display_name)
                             if a["id"] == aid), None
                        )
                        if asset and asset["status"] == "success":
                            done_assets += 1
        table_stats.append({
            "display_name": table_display_name,
            "table_name": table_name,
            "record_count": len(records),
            "total_assets": total_assets,
            "done_assets": done_assets,
        })

    return render_template("dashboard.html",
                           current_project=current,
                           project_config=project_config,
                           workflow_config=workflow_config,
                           table_stats=table_stats)


@bp.route("/api/dashboard/running")
def running_tasks():
    """返回当前运行中的任务列表。"""
    current = session.get("current_project", "")
    gateway = current_app.extensions.get("gateway")
    if not gateway:
        return jsonify([])

    running = gateway.get_running()
    result = []
    for asset_id, info in running.items():
        if current:
            from engine.db import get_asset
            asset = get_asset(current, asset_id)
            if asset:
                info["asset_id"] = asset_id
                info["last_output"] = asset.get("last_output", "")
                info["status"] = asset.get("status", "")
                result.append(info)
    return jsonify(result)
