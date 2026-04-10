"""
Fish Audio AI模块 — Mock实现

模拟 TTS 音频生成和音色克隆，创建占位 MP3/音色卡文件。
"""
import time
import threading
from pathlib import Path


def generate(request: dict, status_callback: callable,
             cancel_flag: threading.Event = None) -> dict:
    asset_id = request["asset_id"]
    output_dir = Path(request["output_dir"])
    key_prefix = request["key_prefix"]
    asset_type = request.get("asset_type", "音频")
    gen_config = request.get("gen_config", {})
    model = request.get("model", "fish_audio_tts")

    output_dir.mkdir(parents=True, exist_ok=True)

    if asset_type == "音色卡":
        return _generate_voice_card(
            asset_id, output_dir, key_prefix, model,
            status_callback, cancel_flag
        )
    else:
        return _generate_tts(
            asset_id, output_dir, key_prefix, model, gen_config,
            status_callback, cancel_flag
        )


def _generate_tts(asset_id, output_dir, key_prefix, model, gen_config,
                  status_callback, cancel_flag):
    fmt = gen_config.get("format", "mp3")
    status_callback("processing", f"[{model}] 开始 TTS 合成...")

    if cancel_flag and cancel_flag.is_set():
        return {"success": False, "asset_paths": [], "extra_data": {}, "message": "任务已取消"}

    time.sleep(0.8)
    status_callback("processing", f"[{model}] 语音合成中...")

    filename = f"{key_prefix}_1.{fmt}"
    file_path = output_dir / filename
    _write_placeholder_mp3(file_path)
    rel_path = f"assets/{asset_id}/{filename}"

    status_callback("processing", f"[{model}] TTS 合成完成")
    return {
        "success": True,
        "asset_paths": [{f"{key_prefix}_1": rel_path}],
        "extra_data": {},
        "message": f"Fish Audio TTS mock 完成"
    }


def _generate_voice_card(asset_id, output_dir, key_prefix, model,
                         status_callback, cancel_flag):
    status_callback("processing", f"[{model}] 开始音色克隆...")

    if cancel_flag and cancel_flag.is_set():
        return {"success": False, "asset_paths": [], "extra_data": {}, "message": "任务已取消"}

    time.sleep(1.2)
    status_callback("processing", f"[{model}] 音色特征提取中...")

    # 生成试听音频
    filename = f"{key_prefix}_preview.mp3"
    file_path = output_dir / filename
    _write_placeholder_mp3(file_path)
    rel_path = f"assets/{asset_id}/{filename}"

    # mock voice_id
    mock_voice_id = f"mock_voice_{asset_id}"
    status_callback("processing", f"[{model}] 音色克隆完成，voice_id={mock_voice_id}")

    return {
        "success": True,
        "asset_paths": [{f"{key_prefix}_preview": rel_path}],
        "extra_data": {"voice_id": mock_voice_id},
        "message": f"Fish Audio 音色克隆 mock 完成"
    }


def _write_placeholder_mp3(path: Path):
    """写入最小合法 MP3 占位文件（ID3 header + 空帧）。"""
    # ID3v2 最小 header（10 字节）
    id3 = b'ID3\x03\x00\x00\x00\x00\x00\x00'
    # 空 MP3 帧
    frame = b'\xff\xfb\x90\x00' + b'\x00' * 413
    path.write_bytes(id3 + frame)
