"""
剧本配置路由：/config
"""
from flask import (Blueprint, jsonify, redirect, render_template,
                   request, session, url_for)

from engine.config_loader import (list_workflows, load_workflow_config,
                                   load_project_config, save_project_config)
from ai_modules.scanner import get_models_by_asset_type

bp = Blueprint("config_routes", __name__)


@bp.route("/config")
def config_page():
    current = session.get("current_project", "")
    if not current:
        return redirect(url_for("projects.index"))

    project_config = load_project_config(current)
    workflow_name = project_config.get("workflow", "")
    workflow_config = load_workflow_config(workflow_name) if workflow_name else {}
    workflows = list_workflows()

    return render_template("config.html",
                           current_project=current,
                           project_config=project_config,
                           workflow_config=workflow_config,
                           workflows=workflows)


@bp.route("/config/save", methods=["POST"])
def save_config():
    current = session.get("current_project", "")
    if not current:
        return jsonify({"error": "未选择项目"}), 400

    data = request.get_json(force=True)
    existing = load_project_config(current)
    existing.update(data)
    save_project_config(current, existing)
    return jsonify({"ok": True})
