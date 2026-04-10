"""
Runway AI模块 — Mock实现

模拟视频生成，创建占位 MP4 文件。
"""
import time
import threading
from pathlib import Path


def generate(request: dict, status_callback: callable,
             cancel_flag: threading.Event = None) -> dict:
    asset_id = request["asset_id"]
    output_dir = Path(request["output_dir"])
    key_prefix = request["key_prefix"]
    gen_config = request.get("gen_config", {})
    batch_size = int(gen_config.get("batch_size", 1))
    duration = gen_config.get("duration", "4s")

    output_dir.mkdir(parents=True, exist_ok=True)

    model = request.get("model", "runway_gen3")
    status_callback("processing", f"[{model}] 开始视频生成，时长={duration}，batch_size={batch_size}...")

    for i in range(batch_size):
        if cancel_flag and cancel_flag.is_set():
            return {"success": False, "asset_paths": [], "extra_data": {},
                    "message": "任务已取消"}
        time.sleep(1.0)
        status_callback("processing", f"[{model}] 正在生成第 {i + 1}/{batch_size} 个视频...")

    asset_paths = []
    for i in range(1, batch_size + 1):
        filename = f"{key_prefix}_{i}.mp4"
        file_path = output_dir / filename
        _write_placeholder_mp4(file_path)
        rel_path = f"assets/{asset_id}/{filename}"
        asset_paths.append({f"{key_prefix}_{i}": rel_path})

    status_callback("processing", f"[{model}] 视频生成完成，共 {batch_size} 个")

    return {
        "success": True,
        "asset_paths": asset_paths,
        "extra_data": {},
        "message": f"Runway mock 生成完成，{batch_size} 个视频"
    }


def _write_placeholder_mp4(path: Path):
    """写入最小合法 MP4 占位文件（ftyp box）。"""
    # ftyp box: 空的 mp4 容器
    ftyp = (
        b'\x00\x00\x00\x18'   # box size = 24
        b'ftyp'               # box type
        b'isom'               # major brand
        b'\x00\x00\x02\x00'  # minor version
        b'isom'               # compatible brands
        b'iso2'
    )
    path.write_bytes(ftyp)
