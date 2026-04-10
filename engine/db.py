"""
数据库层：sqlite3 连接工厂 + schema 初始化
每个项目使用独立的 SQLite 文件，每个线程持有独立连接（threading.local）。
"""
import sqlite3
import json
import threading
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent / "projects"

_local = threading.local()


def _db_path(project_name: str) -> Path:
    return BASE_DIR / project_name / "database.sqlite"


def get_db(project_name: str) -> sqlite3.Connection:
    """返回当前线程对应的数据库连接（如不存在则新建）。"""
    key = f"conn_{project_name}"
    conn = getattr(_local, key, None)
    if conn is None:
        db_path = _db_path(project_name)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        setattr(_local, key, conn)
    return conn


def close_db(project_name: str):
    """关闭当前线程的数据库连接。"""
    key = f"conn_{project_name}"
    conn = getattr(_local, key, None)
    if conn is not None:
        conn.close()
        setattr(_local, key, None)


ASSETS_DDL = """
CREATE TABLE IF NOT EXISTS assets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_type  TEXT    NOT NULL,
    sub_category TEXT,
    model_name  TEXT,
    prompt      TEXT    DEFAULT '[]',
    dependencies TEXT   DEFAULT '[]',
    gen_config  TEXT    DEFAULT '{}',
    status      TEXT    DEFAULT 'waiting',
    last_output TEXT    DEFAULT '',
    ck_record   TEXT    DEFAULT '',
    asset_paths TEXT    DEFAULT '[[]]',
    active_index TEXT   DEFAULT '[0, 0]',
    extra_data  TEXT    DEFAULT '{}',
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

RELATION_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS {table_name} (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category    TEXT,
    ref_ids     TEXT    DEFAULT '[]',
    name        TEXT,
    node_name   TEXT,
    parent_id   INTEGER,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""


def init_db(project_name: str, workflow_config: dict):
    """初始化项目数据库：建 assets 表 + 工作流定义的所有关系表。"""
    conn = get_db(project_name)
    conn.execute(ASSETS_DDL)
    for table_display_name in workflow_config.get("relation_tables", {}).keys():
        table_name = f"rel_{table_display_name}"
        conn.execute(RELATION_TABLE_DDL.format(table_name=table_name))
    conn.commit()


def create_relation_table(project_name: str, table_name: str):
    """动态创建单张关系表（table_name 已含 rel_ 前缀）。"""
    conn = get_db(project_name)
    conn.execute(RELATION_TABLE_DDL.format(table_name=table_name))
    conn.commit()


def list_relation_tables(project_name: str) -> list[str]:
    """列出数据库中所有 rel_ 开头的表名。"""
    conn = get_db(project_name)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'rel_%'"
    ).fetchall()
    return [row["name"] for row in rows]


# ---------- 资产 CRUD ----------

def create_asset(project_name: str, asset_type: str, sub_category: str,
                 model_name: str = None, prompt: list = None,
                 dependencies: list = None, gen_config: dict = None,
                 extra_data: dict = None) -> int:
    conn = get_db(project_name)
    cur = conn.execute(
        """INSERT INTO assets (asset_type, sub_category, model_name, prompt,
           dependencies, gen_config, extra_data)
           VALUES (?,?,?,?,?,?,?)""",
        (
            asset_type,
            sub_category,
            model_name,
            json.dumps(prompt or [], ensure_ascii=False),
            json.dumps(dependencies or [], ensure_ascii=False),
            json.dumps(gen_config or {}, ensure_ascii=False),
            json.dumps(extra_data or {}, ensure_ascii=False),
        )
    )
    conn.commit()
    # 确保 assets 目录存在
    asset_dir = BASE_DIR / project_name / "assets" / str(cur.lastrowid)
    asset_dir.mkdir(parents=True, exist_ok=True)
    return cur.lastrowid


def get_asset(project_name: str, asset_id: int) -> dict | None:
    conn = get_db(project_name)
    row = conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
    if row is None:
        return None
    return _asset_row_to_dict(row)


def update_asset(project_name: str, asset_id: int, **fields):
    """更新资产任意字段，JSON字段自动序列化。"""
    json_fields = {"prompt", "dependencies", "gen_config", "asset_paths",
                   "active_index", "extra_data"}
    set_clauses = []
    values = []
    for k, v in fields.items():
        set_clauses.append(f"{k}=?")
        if k in json_fields and not isinstance(v, str):
            values.append(json.dumps(v, ensure_ascii=False))
        else:
            values.append(v)
    set_clauses.append("updated_at=CURRENT_TIMESTAMP")
    values.append(asset_id)
    sql = f"UPDATE assets SET {', '.join(set_clauses)} WHERE id=?"
    conn = get_db(project_name)
    conn.execute(sql, values)
    conn.commit()


def list_assets(project_name: str, sub_category: str = None) -> list[dict]:
    conn = get_db(project_name)
    if sub_category:
        rows = conn.execute(
            "SELECT * FROM assets WHERE sub_category=? ORDER BY id",
            (sub_category,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM assets ORDER BY id").fetchall()
    return [_asset_row_to_dict(r) for r in rows]


def delete_asset(project_name: str, asset_id: int):
    conn = get_db(project_name)
    conn.execute("DELETE FROM assets WHERE id=?", (asset_id,))
    conn.commit()


def _asset_row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for field in ("prompt", "dependencies", "gen_config",
                  "asset_paths", "active_index", "extra_data"):
        try:
            d[field] = json.loads(d[field]) if d[field] else None
        except (json.JSONDecodeError, TypeError):
            pass
    return d


# ---------- 关系记录 CRUD ----------

def create_relation_record(project_name: str, table_name: str,
                            category: str, name: str, node_name: str,
                            parent_id: int = None, ref_ids: list = None) -> int:
    conn = get_db(project_name)
    cur = conn.execute(
        f"""INSERT INTO {table_name} (category, name, node_name, parent_id, ref_ids)
            VALUES (?,?,?,?,?)""",
        (
            category,
            name,
            node_name,
            parent_id,
            json.dumps(ref_ids or [], ensure_ascii=False),
        )
    )
    conn.commit()
    return cur.lastrowid


def get_relation_record(project_name: str, table_name: str, record_id: int) -> dict | None:
    conn = get_db(project_name)
    row = conn.execute(
        f"SELECT * FROM {table_name} WHERE id=?", (record_id,)
    ).fetchone()
    if row is None:
        return None
    return _rel_row_to_dict(row)


def list_relation_records(project_name: str, table_name: str) -> list[dict]:
    conn = get_db(project_name)
    rows = conn.execute(
        f"SELECT * FROM {table_name} ORDER BY id"
    ).fetchall()
    return [_rel_row_to_dict(r) for r in rows]


def update_relation_record(project_name: str, table_name: str,
                            record_id: int, **fields):
    set_clauses = []
    values = []
    for k, v in fields.items():
        set_clauses.append(f"{k}=?")
        if k == "ref_ids" and not isinstance(v, str):
            values.append(json.dumps(v, ensure_ascii=False))
        else:
            values.append(v)
    values.append(record_id)
    sql = f"UPDATE {table_name} SET {', '.join(set_clauses)} WHERE id=?"
    conn = get_db(project_name)
    conn.execute(sql, values)
    conn.commit()


def delete_relation_record(project_name: str, table_name: str, record_id: int):
    conn = get_db(project_name)
    conn.execute(f"DELETE FROM {table_name} WHERE id=?", (record_id,))
    conn.commit()


def _rel_row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    try:
        d["ref_ids"] = json.loads(d["ref_ids"]) if d["ref_ids"] else []
    except (json.JSONDecodeError, TypeError):
        d["ref_ids"] = []
    return d
