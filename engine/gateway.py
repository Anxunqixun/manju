"""
网关层：线程包装器 + SSE推送中心

职责：
1. 管理 SSE 客户端队列（每个项目独立队列列表）
2. 为每个 AI 任务创建线程，包装 stdout 捕获、状态管理、SSE推送
3. 支持协作式取消（threading.Event）
4. 写回 asset_paths / extra_data / last_output 到数据库
"""
import io
import json
import queue
import sys
import threading
import time
from pathlib import Path

from engine import db

# ─── SSE 客户端注册表 ────────────────────────────────────────────────────────

# {project_name: [queue.Queue, ...]}
_sse_clients: dict[str, list[queue.Queue]] = {}
_sse_lock = threading.Lock()


def register_sse_client(project_name: str) -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=200)
    with _sse_lock:
        _sse_clients.setdefault(project_name, []).append(q)
    return q


def unregister_sse_client(project_name: str, q: queue.Queue):
    with _sse_lock:
        clients = _sse_clients.get(project_name, [])
        if q in clients:
            clients.remove(q)


def push_event(project_name: str, event_type: str, data: dict):
    """向该项目所有 SSE 连接推送事件。"""
    payload = json.dumps({"event": event_type, **data}, ensure_ascii=False)
    with _sse_lock:
        clients = list(_sse_clients.get(project_name, []))
    for q in clients:
        try:
            q.put_nowait(payload)
        except queue.Full:
            pass  # 丢弃慢客户端的事件


# ─── Gateway ─────────────────────────────────────────────────────────────────

class Gateway:
    def __init__(self, ai_modules: dict):
        self.ai_modules = ai_modules
        # {asset_id: threading.Thread}
        self._running_threads: dict[int, threading.Thread] = {}
        # {asset_id: threading.Event}
        self._cancel_flags: dict[int, threading.Event] = {}
        # {asset_id: float}  开始时间戳
        self._start_times: dict[int, float] = {}
        self._lock = threading.Lock()

    def dispatch(self, request: dict, project_name: str,
                 name: str = "", node_name: str = "", slot_name: str = ""):
        """派发生成任务到后台线程。"""
        asset_id = request["asset_id"]
        cancel_flag = threading.Event()

        def _run():
            old_stdout = sys.stdout
            buffer = io.StringIO()
            sys.stdout = buffer

            def status_callback(status: str, message: str):
                # 临时恢复 stdout 以便读取缓冲
                nonlocal buffer
                sys.stdout = old_stdout
                captured = buffer.getvalue()
                full_output = (captured + "\n" + message).strip()

                db.update_asset(project_name, asset_id,
                                status=status, last_output=full_output)
                push_event(project_name, "output_update",
                           {"asset_id": asset_id, "last_output": full_output})
                push_event(project_name, "status_change",
                           {"asset_id": asset_id, "new_status": status})

                sys.stdout = buffer = io.StringIO()  # 重置缓冲

            try:
                # 更新为 processing
                db.update_asset(project_name, asset_id, status="processing", last_output="")
                push_event(project_name, "status_change",
                           {"asset_id": asset_id, "new_status": "processing"})

                module = self.ai_modules.get(request.get("model", ""))
                if module is None:
                    raise ValueError(f"未知模型: {request.get('model')}")

                # 确保 output_dir 存在
                Path(request["output_dir"]).mkdir(parents=True, exist_ok=True)

                result = module.generate(request, status_callback, cancel_flag)

                sys.stdout = old_stdout

                if result.get("success"):
                    # 追加新轮次到 asset_paths
                    current = db.get_asset(project_name, asset_id)
                    existing_paths = current.get("asset_paths") or [[]]
                    new_round = result.get("asset_paths") or []
                    existing_paths.append(new_round)
                    new_active = [len(existing_paths) - 1, 0]

                    extra = result.get("extra_data") or {}
                    # 合并 extra_data
                    current_extra = current.get("extra_data") or {}
                    current_extra.update(extra)

                    db.update_asset(project_name, asset_id,
                                    status="success",
                                    asset_paths=existing_paths,
                                    active_index=new_active,
                                    extra_data=current_extra,
                                    last_output=result.get("message", ""))
                    push_event(project_name, "status_change",
                               {"asset_id": asset_id, "new_status": "success"})
                    push_event(project_name, "task_complete",
                               {"asset_id": asset_id, "success": True,
                                "message": result.get("message", "")})
                else:
                    msg = result.get("message", "生成失败")
                    db.update_asset(project_name, asset_id,
                                    status="failed", last_output=msg)
                    push_event(project_name, "status_change",
                               {"asset_id": asset_id, "new_status": "failed"})
                    push_event(project_name, "task_complete",
                               {"asset_id": asset_id, "success": False, "message": msg})

            except Exception as e:
                sys.stdout = old_stdout
                import traceback
                err_msg = traceback.format_exc()
                db.update_asset(project_name, asset_id,
                                status="failed", last_output=err_msg)
                push_event(project_name, "status_change",
                           {"asset_id": asset_id, "new_status": "failed"})
                push_event(project_name, "task_complete",
                           {"asset_id": asset_id, "success": False, "message": str(e)})

            finally:
                sys.stdout = old_stdout
                elapsed = time.time() - self._start_times.get(asset_id, time.time())
                push_event(project_name, "task_complete",
                           {"asset_id": asset_id,
                            "elapsed": round(elapsed, 1),
                            "label": f"{name} · {node_name} · {slot_name}"})
                with self._lock:
                    self._running_threads.pop(asset_id, None)
                    self._cancel_flags.pop(asset_id, None)
                    self._start_times.pop(asset_id, None)

        t = threading.Thread(target=_run, daemon=True,
                             name=f"gen-{asset_id}-{slot_name}")
        with self._lock:
            self._running_threads[asset_id] = t
            self._cancel_flags[asset_id] = cancel_flag
            self._start_times[asset_id] = time.time()

        db.update_asset(project_name, asset_id, status="queuing")
        push_event(project_name, "status_change",
                   {"asset_id": asset_id, "new_status": "queuing"})
        t.start()

    def cancel(self, asset_id: int, project_name: str,
               downstream_ids: list[int] = None):
        """取消指定资产的生成任务，并连锁取消下游 queuing 状态的资产。"""
        with self._lock:
            flag = self._cancel_flags.get(asset_id)
            if flag:
                flag.set()
            self._running_threads.pop(asset_id, None)
            self._cancel_flags.pop(asset_id, None)
            self._start_times.pop(asset_id, None)

        db.update_asset(project_name, asset_id, status="cancelled")

        cancelled_downstream = []
        for did in (downstream_ids or []):
            asset = db.get_asset(project_name, did)
            if asset and asset["status"] == "queuing":
                db.update_asset(project_name, did, status="cancelled")
                cancelled_downstream.append(did)

        push_event(project_name, "task_cancelled",
                   {"asset_id": asset_id,
                    "downstream_ids": cancelled_downstream})

    def get_running(self) -> dict[int, dict]:
        """返回当前所有运行中任务的信息 {asset_id: {elapsed, ...}}。"""
        result = {}
        now = time.time()
        with self._lock:
            for aid, t in self._running_threads.items():
                started = self._start_times.get(aid, now)
                result[aid] = {
                    "thread_name": t.name,
                    "elapsed": round(now - started, 1),
                    "alive": t.is_alive(),
                }
        return result

    def is_running(self, asset_id: int) -> bool:
        with self._lock:
            t = self._running_threads.get(asset_id)
            return t is not None and t.is_alive()
