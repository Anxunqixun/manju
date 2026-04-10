"""
项目管理路由：/ 项目列表、新建/切换/删除、导入/导出
"""
import io
import json
import shutil
import zipfile
from pathlib import Path

from flask import (Blueprint, jsonify, redirect, render_template,
                   request, session, url_for, send_file, current_app)

from engine.config_loader import (list_projects, list_workflows,
                                   load_workflow_config, save_project_config)
from engine.db import init_db

bp = Blueprint("projects", __name__)
PROJECTS_DIR = Path(__file__).parent.parent / "projects"


@bp.route("/")
def index():
    projects = list_projects()
    current = session.get("current_project", "")
    return render_template("index.html", projects=projects,
                           current_project=current)


@bp.route("/switch/<project_name>")
def switch_project(project_name: str):
    projects_names = [p["name"] for p in list_projects()]
    if project_name in projects_names:
        session["current_project"] = project_name
    return redirect(url_for("projects.index"))


@bp.route("/projects/create", methods=["POST"])
def create_project():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    workflow = (data.get("workflow") or "").strip()
    if not name or not workflow:
        return jsonify({"error": "项目名称和工作流不能为空"}), 400

    project_dir = PROJECTS_DIR / name
    if project_dir.exists():
        return jsonify({"error": "项目已存在"}), 409

    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "assets").mkdir(exist_ok=True)
    (project_dir / "exports").mkdir(exist_ok=True)

    config = {
        "project_name": name,
        "workflow": workflow,
        "defaults": {},
        "global_thread_limit": 3,
    }
    save_project_config(name, config)

    workflow_config = load_workflow_config(workflow)
    init_db(name, workflow_config)

    session["current_project"] = name
    return jsonify({"ok": True, "name": name})


@bp.route("/projects/delete/<project_name>", methods=["DELETE"])
def delete_project(project_name: str):
    project_dir = PROJECTS_DIR / project_name
    if not project_dir.exists():
        return jsonify({"error": "项目不存在"}), 404
    shutil.rmtree(project_dir)
    if session.get("current_project") == project_name:
        session.pop("current_project", None)
    return jsonify({"ok": True})


@bp.route("/projects/export/<project_name>")
def export_project(project_name: str):
    """打包整个项目文件夹为 zip 下载。"""
    project_dir = PROJECTS_DIR / project_name
    if not project_dir.exists():
        return jsonify({"error": "项目不存在"}), 404

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in project_dir.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(PROJECTS_DIR))
    buf.seek(0)
    return send_file(buf, download_name=f"{project_name}.zip",
                     as_attachment=True, mimetype="application/zip")


@bp.route("/projects/import", methods=["POST"])
def import_project():
    """上传 zip 解压到 projects 目录。"""
    if "file" not in request.files:
        return jsonify({"error": "请上传 zip 文件"}), 400
    f = request.files["file"]
    if not f.filename.endswith(".zip"):
        return jsonify({"error": "只支持 .zip 文件"}), 400

    buf = io.BytesIO(f.read())
    with zipfile.ZipFile(buf, "r") as zf:
        # 安全检查：防止路径穿越
        for name in zf.namelist():
            if ".." in name or name.startswith("/"):
                return jsonify({"error": "zip 文件包含非法路径"}), 400
        zf.extractall(PROJECTS_DIR)

    return jsonify({"ok": True})
