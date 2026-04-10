"""
Flask 入口：create_app() + SSE 端点
"""
import json
import queue
import os
from pathlib import Path

from flask import Flask, Response, session


def create_app(config=None):
    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-prod")
    app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB 上传限制

    # ── 初始化 AI 模块扫描 ──────────────────────────────────────────
    from ai_modules.scanner import scan_modules
    ai_modules = scan_modules()
    app.extensions["ai_modules"] = ai_modules

    # ── 初始化 Gateway / Scheduler ─────────────────────────────────
    from engine.gateway import Gateway
    from engine.scheduler import Scheduler
    gateway = Gateway(ai_modules)
    scheduler = Scheduler(gateway)
    app.extensions["gateway"] = gateway
    app.extensions["scheduler"] = scheduler

    # ── 注册 Blueprints ────────────────────────────────────────────
    from routes.projects import bp as projects_bp
    from routes.config_routes import bp as config_bp
    from routes.dashboard import bp as dashboard_bp
    from routes.tables import bp as tables_bp
    from routes.export import bp as export_bp
    from routes.api import bp as api_bp

    app.register_blueprint(projects_bp)
    app.register_blueprint(config_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(tables_bp)
    app.register_blueprint(export_bp)
    app.register_blueprint(api_bp)

    # ── SSE 端点 ────────────────────────────────────────────────────
    from engine.gateway import register_sse_client, unregister_sse_client

    @app.route("/sse/<project_name>")
    def sse_stream(project_name: str):
        q = register_sse_client(project_name)

        def generate():
            try:
                while True:
                    try:
                        payload = q.get(timeout=25)
                        yield f"data: {payload}\n\n"
                    except queue.Empty:
                        # 心跳，保持连接
                        yield f"data: {json.dumps({'event': 'heartbeat'})}\n\n"
            except GeneratorExit:
                pass
            finally:
                unregister_sse_client(project_name, q)

        return Response(
            generate(),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    # ── 资产文件服务 ─────────────────────────────────────────────────
    from flask import send_from_directory
    projects_dir = Path(__file__).parent / "projects"

    @app.route("/project_files/<project_name>/<path:filepath>")
    def serve_project_file(project_name: str, filepath: str):
        """提供项目内文件的静态访问（资产预览用）。"""
        project_dir = projects_dir / project_name
        return send_from_directory(str(project_dir), filepath)

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, threaded=True, host="0.0.0.0", port=5000)
