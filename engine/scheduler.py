"""
调度层：拓扑排序 + 并发调度

职责：
1. 接收待生成的资产ID列表
2. 构建依赖图，Kahn 算法拓扑排序
3. 使用 Semaphore 控制并发数（global_thread_limit）
4. 完成后触发下游任务
"""
import json
import threading
from collections import defaultdict, deque

from engine import db
from engine.assembler import assemble_request, DependencyNotMetError
from engine.gateway import Gateway
from engine.config_loader import load_project_config, get_global_thread_limit


class Scheduler:
    def __init__(self, gateway: Gateway):
        self.gateway = gateway
        # {project_name: threading.Semaphore}
        self._semaphores: dict[str, threading.Semaphore] = {}
        self._sem_lock = threading.Lock()

    def _get_semaphore(self, project_name: str, limit: int) -> threading.Semaphore:
        with self._sem_lock:
            if project_name not in self._semaphores:
                self._semaphores[project_name] = threading.Semaphore(limit)
        return self._semaphores[project_name]

    def run_batch(self, asset_ids: list[int], project_name: str,
                  slot_meta: dict[int, dict] = None):
        """
        批量调度生成任务。

        asset_ids   - 需要生成的资产ID列表
        project_name - 项目名称
        slot_meta    - {asset_id: {name, node_name, slot_name}} 用于 key_prefix
        """
        project_config = load_project_config(project_name)
        thread_limit = get_global_thread_limit(project_config)
        semaphore = self._get_semaphore(project_name, thread_limit)
        slot_meta = slot_meta or {}

        # ── 建立依赖图（只在 asset_ids 集合内部） ──────────────────────
        id_set = set(asset_ids)
        dep_graph: dict[int, set[int]] = {}   # aid -> {依赖的aid（在集合内的）}
        rev_graph: dict[int, list[int]] = defaultdict(list)  # aid -> [依赖它的aid]

        for aid in asset_ids:
            asset = db.get_asset(project_name, aid)
            if asset is None:
                dep_graph[aid] = set()
                continue
            deps = {d for d in (asset.get("dependencies") or []) if d in id_set}
            dep_graph[aid] = deps
            for d in deps:
                rev_graph[d].append(aid)

        # ── Kahn 拓扑排序，得到初始就绪集合 ──────────────────────────
        in_degree = {aid: len(deps) for aid, deps in dep_graph.items()}
        ready_queue: deque[int] = deque(
            aid for aid, deg in in_degree.items() if deg == 0
        )

        completed: set[int] = set()
        failed: set[int] = set()
        dispatched: set[int] = set()
        lock = threading.Lock()

        def _on_task_done(asset_id: int, success: bool):
            """任务完成时的回调，在网关线程中调用。"""
            with lock:
                if success:
                    completed.add(asset_id)
                    # 降低下游的入度
                    for downstream in rev_graph[asset_id]:
                        in_degree[downstream] -= 1
                        if in_degree[downstream] == 0:
                            ready_queue.append(downstream)
                else:
                    failed.add(asset_id)
                    # 递归取消所有下游
                    _cancel_downstream(asset_id)
                semaphore.release()
                _dispatch_ready()

        def _cancel_downstream(aid: int):
            """将 aid 下游所有资产标记为 cancelled。"""
            for downstream in rev_graph.get(aid, []):
                if downstream not in completed and downstream not in failed:
                    failed.add(downstream)
                    db.update_asset(project_name, downstream, status="cancelled")
                    _cancel_downstream(downstream)

        def _dispatch_ready():
            """从就绪队列中取出任务并派发（受 semaphore 控制）。"""
            while ready_queue:
                aid = ready_queue.popleft()
                if aid in dispatched or aid in failed:
                    continue
                dispatched.add(aid)

                # 在新线程中等待 semaphore 后派发
                threading.Thread(
                    target=_acquire_and_dispatch,
                    args=(aid,),
                    daemon=True,
                    name=f"sched-{aid}"
                ).start()

        def _acquire_and_dispatch(aid: int):
            semaphore.acquire()
            with lock:
                if aid in failed:
                    semaphore.release()
                    return

            meta = slot_meta.get(aid, {})
            try:
                request = assemble_request(
                    aid, project_name,
                    name=meta.get("name", ""),
                    node_name=meta.get("node_name", ""),
                    slot_name=meta.get("slot_name", ""),
                )
            except DependencyNotMetError as e:
                # 依赖未满足（不在本批次内的依赖失败了）
                db.update_asset(project_name, aid, status="failed",
                                last_output=str(e))
                with lock:
                    failed.add(aid)
                    _cancel_downstream(aid)
                semaphore.release()
                return
            except Exception as e:
                db.update_asset(project_name, aid, status="failed",
                                last_output=str(e))
                with lock:
                    failed.add(aid)
                    _cancel_downstream(aid)
                semaphore.release()
                return

            # 包装 gateway dispatch，监听完成事件
            original_dispatch = self.gateway.dispatch

            def _wrapped_dispatch():
                # 直接使用 gateway，但我们需要在任务完成后回调
                # 通过订阅 SSE 不可靠，这里用线程包装器
                _dispatch_with_callback(request, meta, aid)

            _wrapped_dispatch()

        def _dispatch_with_callback(request: dict, meta: dict, aid: int):
            """派发任务，并在完成后调用 _on_task_done。"""
            name = meta.get("name", "")
            node_name = meta.get("node_name", "")
            slot_name = meta.get("slot_name", "")

            # 获取原始 dispatch，并包装完成回调
            cancel_flag = threading.Event()

            import io, sys
            import traceback
            from engine.gateway import push_event
            from pathlib import Path

            def _run():
                old_stdout = sys.stdout
                buffer = io.StringIO()
                sys.stdout = buffer

                def status_callback(status: str, message: str):
                    nonlocal buffer
                    sys.stdout = old_stdout
                    captured = buffer.getvalue()
                    full_output = (captured + "\n" + message).strip()
                    db.update_asset(project_name, aid,
                                    status=status, last_output=full_output)
                    push_event(project_name, "output_update",
                               {"asset_id": aid, "last_output": full_output})
                    push_event(project_name, "status_change",
                               {"asset_id": aid, "new_status": status})
                    sys.stdout = buffer = io.StringIO()

                success = False
                try:
                    db.update_asset(project_name, aid, status="processing", last_output="")
                    push_event(project_name, "status_change",
                               {"asset_id": aid, "new_status": "processing"})

                    module = self.gateway.ai_modules.get(request.get("model", ""))
                    if module is None:
                        raise ValueError(f"未知模型: {request.get('model')}")

                    Path(request["output_dir"]).mkdir(parents=True, exist_ok=True)
                    result = module.generate(request, status_callback, cancel_flag)
                    sys.stdout = old_stdout

                    if result.get("success"):
                        current = db.get_asset(project_name, aid)
                        existing_paths = current.get("asset_paths") or [[]]
                        new_round = result.get("asset_paths") or []
                        existing_paths.append(new_round)
                        new_active = [len(existing_paths) - 1, 0]
                        current_extra = current.get("extra_data") or {}
                        current_extra.update(result.get("extra_data") or {})
                        db.update_asset(project_name, aid,
                                        status="success",
                                        asset_paths=existing_paths,
                                        active_index=new_active,
                                        extra_data=current_extra,
                                        last_output=result.get("message", ""))
                        push_event(project_name, "status_change",
                                   {"asset_id": aid, "new_status": "success"})
                        push_event(project_name, "task_complete",
                                   {"asset_id": aid, "success": True,
                                    "message": result.get("message", ""),
                                    "label": f"{name} · {node_name} · {slot_name}"})
                        success = True
                    else:
                        msg = result.get("message", "生成失败")
                        db.update_asset(project_name, aid,
                                        status="failed", last_output=msg)
                        push_event(project_name, "status_change",
                                   {"asset_id": aid, "new_status": "failed"})
                        push_event(project_name, "task_complete",
                                   {"asset_id": aid, "success": False, "message": msg,
                                    "label": f"{name} · {node_name} · {slot_name}"})

                except Exception as e:
                    sys.stdout = old_stdout
                    err_msg = traceback.format_exc()
                    db.update_asset(project_name, aid,
                                    status="failed", last_output=err_msg)
                    push_event(project_name, "status_change",
                               {"asset_id": aid, "new_status": "failed"})
                    push_event(project_name, "task_complete",
                               {"asset_id": aid, "success": False, "message": str(e),
                                "label": f"{name} · {node_name} · {slot_name}"})
                finally:
                    sys.stdout = old_stdout
                    _on_task_done(aid, success)

            t = threading.Thread(target=_run, daemon=True,
                                 name=f"gen-{aid}-{slot_name}")
            with self.gateway._lock:
                self.gateway._running_threads[aid] = t
                self.gateway._cancel_flags[aid] = cancel_flag
                import time
                self.gateway._start_times[aid] = time.time()

            db.update_asset(project_name, aid, status="queuing")
            push_event(project_name, "status_change",
                       {"asset_id": aid, "new_status": "queuing"})
            t.start()

        # ── 启动初始就绪任务 ──────────────────────────────────────────
        _dispatch_ready()
