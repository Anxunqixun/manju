"""
结果导出路由：/export
"""
import io
import zipfile
from pathlib import Path

from flask import (Blueprint, jsonify, redirect, render_template,
                   request, session, url_for, send_file)

from engine.config_loader import load_project_config, load_workflow_config
from engine.db import list_relation_records, get_asset

bp = Blueprint("export", __name__)
PROJECTS_DIR = Path(__file__).parent.parent / "projects"


@bp.route("/export")
def export_page():
    current = session.get("current_project", "")
    if not current:
        return redirect(url_for("projects.index"))

    project_config = load_project_config(current)
    workflow_name = project_config.get("workflow", "")
    workflow_config = load_workflow_config(workflow_name) if workflow_name else {}

    return render_template("export.html",
                           current_project=current,
                           project_config=project_config,
                           workflow_config=workflow_config)


@bp.route("/export/download", methods=["POST"])
def export_download():
    """
    POST body: {
        "tables": ["角色", "场景"],
        "slots": {"角色": ["角色主视图"], "场景": ["场景全景图"]},
        "record_ids": {"角色": [1, 2], "场景": [3]}
    }
    """
    current = session.get("current_project", "")
    if not current:
        return jsonify({"error": "未选择项目"}), 400

    data = request.get_json(force=True)
    selected_tables = data.get("tables") or []
    selected_slots = data.get("slots") or {}
    selected_records = data.get("record_ids") or {}

    project_config = load_project_config(current)
    workflow_name = project_config.get("workflow", "")
    workflow_config = load_workflow_config(workflow_name) if workflow_name else {}

    project_dir = PROJECTS_DIR / current
    buf = io.BytesIO()

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for table_display_name in selected_tables:
            table_name = f"rel_{table_display_name}"
            records = list_relation_records(current, table_name)
            slots_for_table = selected_slots.get(table_display_name) or []
            record_ids_filter = selected_records.get(table_display_name)

            for rec in records:
                if record_ids_filter and rec["id"] not in record_ids_filter:
                    continue

                root_name = rec.get("name") or f"record_{rec['id']}"
                node_name = rec.get("node_name") or f"node_{rec['id']}"

                ref_ids_data = rec.get("ref_ids") or []
                for slot_entry in ref_ids_data:
                    for slot_name, ids in slot_entry.items():
                        if slots_for_table and slot_name not in slots_for_table:
                            continue
                        for aid in ids:
                            asset = get_asset(current, aid)
                            if not asset or asset["status"] != "success":
                                continue
                            # 取激活素材
                            asset_paths = asset.get("asset_paths") or [[]]
                            active = asset.get("active_index") or [0, 0]
                            try:
                                active_file_dict = asset_paths[active[0]][active[1]]
                            except (IndexError, TypeError):
                                continue

                            for key, rel_path in active_file_dict.items():
                                src = project_dir / rel_path
                                if not src.exists():
                                    continue
                                suffix = src.suffix
                                archive_path = (
                                    f"{table_display_name}/"
                                    f"{root_name}/"
                                    f"{slot_name}/"
                                    f"{node_name}{suffix}"
                                )
                                zf.write(src, archive_path)

    buf.seek(0)
    filename = f"{current}_export.zip"
    return send_file(buf, download_name=filename,
                     as_attachment=True, mimetype="application/zip")
