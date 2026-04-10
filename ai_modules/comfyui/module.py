"""
ComfyUI AI模块 — Mock实现

实现统一的 generate() 接口。
当前为演示用 stub，模拟图片生成并创建占位文件。
"""
import time
import threading
from pathlib import Path


def generate(request: dict, status_callback: callable,
             cancel_flag: threading.Event = None) -> dict:
    """
    统一生成接口。

    request 字段:
        asset_id, model, asset_type, prompt, dependencies,
        gen_config, extra_data, output_dir, key_prefix

    status_callback(status: str, message: str)

    返回: {success, asset_paths, extra_data, message}
    """
    asset_id = request["asset_id"]
    output_dir = Path(request["output_dir"])
    key_prefix = request["key_prefix"]
    gen_config = request.get("gen_config", {})
    batch_size = int(gen_config.get("batch_size", 1))

    output_dir.mkdir(parents=True, exist_ok=True)

    model = request.get("model", "comfyui_flux")
    status_callback("processing", f"[{model}] 初始化生成任务，batch_size={batch_size}...")

    # 模拟生成过程
    for i in range(batch_size):
        if cancel_flag and cancel_flag.is_set():
            return {"success": False, "asset_paths": [], "extra_data": {},
                    "message": "任务已取消"}
        time.sleep(0.5)
        status_callback("processing", f"[{model}] 正在生成第 {i + 1}/{batch_size} 张图片...")

    # 创建占位输出文件
    asset_paths = []
    for i in range(1, batch_size + 1):
        filename = f"{key_prefix}_{i}.png"
        file_path = output_dir / filename
        # 创建最小 PNG 占位文件（1x1 像素，白色）
        _write_placeholder_png(file_path)
        rel_path = f"assets/{asset_id}/{filename}"
        asset_paths.append({f"{key_prefix}_{i}": rel_path})

    status_callback("processing", f"[{model}] 生成完成，共 {batch_size} 张图片")

    return {
        "success": True,
        "asset_paths": asset_paths,
        "extra_data": {},
        "message": f"ComfyUI mock 生成完成，{batch_size} 张图片"
    }


def _write_placeholder_png(path: Path):
    """写入最小合法 PNG 文件（1x1 白色像素）。"""
    # 最小 PNG 二进制（1x1 像素，白色，无压缩）
    PNG_1X1_WHITE = bytes([
        0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,  # PNG signature
        0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,  # IHDR chunk length + type
        0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,  # width=1, height=1
        0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x77, 0x53,  # bit depth=8, color type=2
        0xDE, 0x00, 0x00, 0x00, 0x0C, 0x49, 0x44, 0x41,  # IDAT chunk
        0x54, 0x08, 0xD7, 0x63, 0xF8, 0xFF, 0xFF, 0x3F,
        0x00, 0x05, 0xFE, 0x02, 0xFE, 0xA7, 0x35, 0x81,
        0x84, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4E,  # IEND chunk
        0x44, 0xAE, 0x42, 0x60, 0x82
    ])
    path.write_bytes(PNG_1X1_WHITE)
