"""
JSON API 路由：/api/*
涵盖资产CRUD、关系记录CRUD、生成触发、取消、文件上传
"""
import json
from pathlib import Path

from flask import (Blueprint, jsonify, request, session,
                   current_app, send_from_directory)
from werkzeug.utils import secure_filename

from engine.config_loader import (list_workflows, load_workflow_config,
                                   load_project_config, get_slot_default,
                                   list_projects)
from engine.db import (get_asset, update_asset, list_assets, create_asset,
                        delete_asset, list_relation_records,
                        get_relation_record, create_relation_record,
                        update_relation_record, delete_relation_record)
from engine.assembler import collect_dependency_tree, assemble_request, DependencyNotMetError
from ai_modules.scanner import (get_manifest_list, get_models_by_asset_type)

bp = Blueprint("api", __name__, url_prefix="/api")
PROJECTS_DIR = Path(__file__).parent.parent / "projects"


def _current_project() -> str:
    return session.get("current_project", "")


# ─── 项目 ────────────────────────────────────────────────────────────────────

@bp.route("/projects")
def get_projects():
    return jsonify(list_projects())


@bp.route("/workflows")
def get_workflows():
    return jsonify(list_workflows())


# ─── AI 模块 ─────────────────────────────────────────────────────────────────

@bp.route("/ai_modules")
def get_ai_modules():
    return jsonify(get_manifest_list())


@bp.route("/ai_modules/by_type/<asset_type>")
def get_models_for_type(asset_type: str):
    return jsonify(get_models_by_asset_type(asset_type))


# ─── 资产 ────────────────────────────────────────────────────────────────────

@bp.route("/<project_name>/assets")
def list_project_assets(project_name: str):
    sub = request.args.get("sub_category")
    return jsonify(list_assets(project_name, sub))


@bp.route("/<project_name>/assets/<int:asset_id>")
def get_project_asset(project_name: str, asset_id: int):
    asset = get_asset(project_name, asset_id)
    if asset is None:
        return jsonify({"error": "资产不存在"}), 404
    return jsonify(asset)


@bp.route("/<project_name>/assets/<int:asset_id>", methods=["PUT"])
def update_project_asset(project_name: str, asset_id: int):
    data = request.get_json(force=True)
    allowed = {"model_name", "prompt", "gen_config", "dependencies",
                "extra_data", "active_index", "status"}
    fields = {k: v for k, v in data.items() if k in allowed}
    if not fields:
        return jsonify({"error": "无有效字段"}), 400
    update_asset(project_name, asset_id, **fields)
    return jsonify({"ok": True})


@bp.route("/<project_name>/assets/<int:asset_id>/upload", methods=["POST"])
def upload_asset_file(project_name: str, asset_id: int):
    """文件上传：等同一次新的生成轮次，追加到 asset_paths。"""
    if "file" not in request.files:
        return jsonify({"error": "未找到文件"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "文件名为空"}), 400

    asset = get_asset(project_name, asset_id)
    if asset is None:
        return jsonify({"error": "资产不存在"}), 404

    # 安全文件名，保留原始扩展名
    safe_name = secure_filename(f.filename)
    output_dir = PROJECTS_DIR / project_name / "assets" / str(asset_id)
    output_dir.mkdir(parents=True, exist_ok=True)

    save_path = output_dir / safe_name
    f.save(str(save_path))

    rel_path = f"assets/{asset_id}/{safe_name}"
    key = safe_name.rsplit(".", 1)[0]

    existing_paths = asset.get("asset_paths") or [[]]
    new_round = [{key: rel_path}]
    existing_paths.append(new_round)
    new_active = [len(existing_paths) - 1, 0]

    update_asset(project_name, asset_id,
                 asset_paths=existing_paths,
                 active_index=new_active,
                 status="success")
    return jsonify({"ok": True, "rel_path": rel_path})


@bp.route("/<project_name>/assets/<path:rel_path_suffix>")
def serve_asset_file(project_name: str, rel_path_suffix: str):
    """提供资产文件的静态访问。"""
    assets_dir = PROJECTS_DIR / project_name
    return send_from_directory(str(assets_dir), rel_path_suffix)


# ─── 关系表 ──────────────────────────────────────────────────────────────────

@bp.route("/<project_name>/assets_for_deps")
def get_assets_for_deps(project_name: str):
    """
    返回除 exclude 表之外所有关系表的记录和槽位资产，供新建弹窗选择外部依赖。
    返回格式: [{display_name, records: [{id, name, node_name, slots: [{slot_name, asset_id, asset_type, status}]}]}]
    """
    exclude = request.args.get("exclude", "")
    project_config = load_project_config(project_name)
    workflow_name = project_config.get("workflow", "")
    workflow_config = load_workflow_config(workflow_name) if workflow_name else {}

    result = []
    for tdn, _tc in workflow_config.get("relation_tables", {}).items():
        if tdn == exclude:
            continue
        table_name = f"rel_{tdn}"
        records = list_relation_records(project_name, table_name)
        table_records = []
        for rec in records:
            slots_list = []
            for slot_entry in (rec.get("ref_ids") or []):
                for slot_name, ids in slot_entry.items():
                    for aid in ids:
                        asset = get_asset(project_name, aid)
                        if asset:
                            # 计算当前激活文件路径（供缩略图使用）
                            active_path = ""
                            if asset.get("status") == "success":
                                try:
                                    import json as _json
                                    paths = asset.get("asset_paths") or [[]]
                                    if isinstance(paths, str):
                                        paths = _json.loads(paths)
                                    ai = asset.get("active_index") or [0, 0]
                                    if isinstance(ai, str):
                                        ai = _json.loads(ai)
                                    active_path = list(paths[ai[0]][ai[1]].values())[0]
                                except Exception:
                                    pass
                            slots_list.append({
                                "slot_name": slot_name,
                                "asset_id": aid,
                                "asset_type": asset["asset_type"],
                                "status": asset["status"],
                                "active_path": active_path,
                            })
            table_records.append({
                "id": rec["id"],
                "name": rec.get("name", ""),
                "node_name": rec.get("node_name", ""),
                "slots": slots_list,
            })
        result.append({"display_name": tdn, "records": table_records})
    return jsonify(result)


@bp.route("/<project_name>/tables/<table_name>")
def get_table_records(project_name: str, table_name: str):
    records = list_relation_records(project_name, table_name)
    return jsonify(records)


@bp.route("/<project_name>/tables/<table_name>", methods=["POST"])
def create_table_record(project_name: str, table_name: str):
    """
    新建关系记录，自动批量创建关联资产。
    body: {name, node_name, parent_id?, slots_config?}
    """
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    node_name = (data.get("node_name") or "").strip()
    parent_id = data.get("parent_id")

    # 子节点：name 继承自父节点，不要求前端传入
    if not name and parent_id is not None:
        parent_rec = get_relation_record(project_name, table_name, int(parent_id))
        if parent_rec:
            name = parent_rec.get("name") or ""

    if not name:
        return jsonify({"error": "name 不能为空"}), 400

    project_config = load_project_config(project_name)
    workflow_name = project_config.get("workflow", "")
    workflow_config = load_workflow_config(workflow_name) if workflow_name else {}

    # 从 table_name 推断 display_name（去掉 rel_ 前缀）
    table_display_name = table_name.removeprefix("rel_")
    table_config = workflow_config.get("relation_tables", {}).get(table_display_name, {})
    slots = table_config.get("slots", {})

    # 自动生成 node_name
    if not node_name:
        pattern = table_config.get("node_name_pattern", "{name}_节点{index}")
        # 计算当前记录数作为 index
        existing = list_relation_records(project_name, table_name)
        index = len(existing) + 1
        node_name = pattern.format(name=name, index=index)

    # 批量创建资产，构建 ref_ids
    ref_ids = []
    inner_deps: dict[str, int] = {}  # slot_name -> asset_id
    slots_override_map = data.get("slots") or {}

    for slot_name, slot_def in slots.items():
        asset_type = slot_def.get("asset_type", "图片")
        prompt_template = slot_def.get("prompt_template") or []
        inner_dep_slot = slot_def.get("inner_dep")

        # 从剧本配置获取默认值
        default = get_slot_default(project_config, table_display_name, slot_name)
        model_name = default.get("model_name")
        prompt_defaults = default.get("prompt_defaults") or {}
        gen_config = default.get("gen_config") or {}

        # 用默认值填充 prompt 模板
        prompt = []
        for seg in prompt_template:
            filled = {k: prompt_defaults.get(k, v) for k, v in seg.items()}
            prompt.append(filled)

        # 应用前端传入的 per-slot 覆盖值
        slot_override = slots_override_map.get(slot_name, {})
        if slot_override.get("model_name") is not None:
            model_name = slot_override["model_name"]
        if slot_override.get("prompt") is not None:
            prompt = slot_override["prompt"]
        if slot_override.get("gen_config") is not None:
            gen_config = {**gen_config, **slot_override["gen_config"]}

        # 构建依赖
        dependencies = []
        if inner_dep_slot and inner_dep_slot in inner_deps:
            dependencies.append(inner_deps[inner_dep_slot])
        for dep_id in (slot_override.get("extra_deps") or []):
            if dep_id not in dependencies:
                dependencies.append(dep_id)

        asset_id = create_asset(
            project_name,
            asset_type=asset_type,
            sub_category=table_display_name,
            model_name=model_name,
            prompt=prompt,
            dependencies=dependencies,
            gen_config=gen_config,
        )
        inner_deps[slot_name] = asset_id
        ref_ids.append({slot_name: [asset_id]})

    record_id = create_relation_record(
        project_name, table_name,
        category=table_display_name,
        name=name,
        node_name=node_name,
        parent_id=parent_id,
        ref_ids=ref_ids,
    )
    return jsonify({"ok": True, "id": record_id, "node_name": node_name})


@bp.route("/<project_name>/tables/<table_name>/<int:record_id>", methods=["PUT"])
def update_table_record(project_name: str, table_name: str, record_id: int):
    data = request.get_json(force=True)
    allowed = {"name", "node_name", "ref_ids"}
    fields = {k: v for k, v in data.items() if k in allowed}
    if not fields:
        return jsonify({"error": "无有效字段"}), 400
    update_relation_record(project_name, table_name, record_id, **fields)
    return jsonify({"ok": True})


@bp.route("/<project_name>/tables/<table_name>/<int:record_id>", methods=["DELETE"])
def delete_table_record(project_name: str, table_name: str, record_id: int):
    """删除关系记录（及其整条链上的所有子节点），清理绑定资产。"""
    import shutil

    # 递归收集以 record_id 为根的链上所有记录 id
    all_records = list_relation_records(project_name, table_name)

    def collect_chain_ids(rid: int) -> list[int]:
        result = [rid]
        for r in all_records:
            if r.get("parent_id") == rid:
                result.extend(collect_chain_ids(r["id"]))
        return result

    chain_record_ids = collect_chain_ids(record_id)

    # 收集链上所有记录绑定的 asset_id
    bound_ids: set[int] = set()
    for cid in chain_record_ids:
        rec = get_relation_record(project_name, table_name, cid)
        if rec is None:
            continue
        for slot_entry in (rec.get("ref_ids") or []):
            for slot_name, ids in slot_entry.items():
                bound_ids.update(ids)

    # 从其他资产的 dependencies 中移除这些 id
    all_assets = list_assets(project_name)
    for asset in all_assets:
        if asset["id"] in bound_ids:
            continue
        deps = asset.get("dependencies") or []
        new_deps = [d for d in deps if d not in bound_ids]
        if len(new_deps) != len(deps):
            update_asset(project_name, asset["id"], dependencies=new_deps)

    # 删除资产文件和记录
    for aid in bound_ids:
        asset_dir = PROJECTS_DIR / project_name / "assets" / str(aid)
        if asset_dir.exists():
            shutil.rmtree(asset_dir)
        delete_asset(project_name, aid)

    # 删除链上所有关系记录（先删子节点，再删父节点）
    for cid in reversed(chain_record_ids):
        delete_relation_record(project_name, table_name, cid)

    return jsonify({"ok": True})


# ─── 生成触发 ────────────────────────────────────────────────────────────────

@bp.route("/<project_name>/generate/preview", methods=["POST"])
def generate_preview(project_name: str):
    """
    返回生成依赖展开后的完整资产列表（用于前端弹窗确认）。
    body: {"asset_ids": [1, 2, 3]}
    """
    data = request.get_json(force=True)
    asset_ids = data.get("asset_ids") or []
    full_list = collect_dependency_tree(asset_ids, project_name)
    return jsonify({"asset_ids": full_list, "count": len(full_list)})


@bp.route("/<project_name>/generate", methods=["POST"])
def trigger_generate(project_name: str):
    """
    触发批量生成。
    body: {
        "asset_ids": [1, 2, 3],
        "slot_meta": {
            "1": {"name":"角色A","node_name":"角色A_节点1","slot_name":"角色主视图"},
            ...
        }
    }
    """
    data = request.get_json(force=True)
    asset_ids = data.get("asset_ids") or []
    slot_meta_raw = data.get("slot_meta") or {}
    # JSON key 都是字符串，转为 int
    slot_meta = {int(k): v for k, v in slot_meta_raw.items()}

    if not asset_ids:
        return jsonify({"error": "asset_ids 不能为空"}), 400

    scheduler = current_app.extensions.get("scheduler")
    if scheduler is None:
        return jsonify({"error": "调度器未初始化"}), 500

    project_config = load_project_config(project_name)
    # 在后台线程中启动，避免阻塞 HTTP 响应
    import threading
    t = threading.Thread(
        target=scheduler.run_batch,
        args=(asset_ids, project_name, slot_meta),
        daemon=True,
        name=f"batch-{project_name}"
    )
    t.start()

    return jsonify({"ok": True, "queued": len(asset_ids)})


@bp.route("/<project_name>/cancel/<int:asset_id>", methods=["POST"])
def cancel_generate(project_name: str, asset_id: int):
    data = request.get_json(force=True) or {}
    downstream = data.get("downstream_ids") or []

    gateway = current_app.extensions.get("gateway")
    if gateway:
        gateway.cancel(asset_id, project_name, downstream)

    return jsonify({"ok": True})
