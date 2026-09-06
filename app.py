import os

from flask import Flask, render_template

from db import SessionLocal
from errors import NodeNotFoundError
from queries import count_due_today, count_inbox, count_weekly_pomodoros


def create_app():
    app = Flask(__name__)

    from views.agenda import bp as agenda_bp
    from views.calendar import bp as calendar_bp
    from views.dashboard import bp as dashboard_bp
    from views.inbox import bp as inbox_bp
    from views.nodes import bp as nodes_bp
    from views.notes import bp as notes_bp
    from views.pomodoro import bp as pomodoro_bp
    from views.projects import bp as projects_bp
    from views.review import bp as review_bp

    app.register_blueprint(inbox_bp)
    app.register_blueprint(agenda_bp)
    app.register_blueprint(nodes_bp)
    app.register_blueprint(projects_bp)
    app.register_blueprint(calendar_bp)
    app.register_blueprint(pomodoro_bp)
    app.register_blueprint(notes_bp)
    app.register_blueprint(review_bp)
    app.register_blueprint(dashboard_bp)

    @app.context_processor
    def inject_badges():
        with SessionLocal() as session:
            inbox_count = count_inbox(session)
            due_today_count = count_due_today(session)
            weekly_pomodoro_count = count_weekly_pomodoros(session)
        return {
            "inbox_count": inbox_count,
            "due_today_count": due_today_count,
            "weekly_pomodoro_count": weekly_pomodoro_count,
        }

    @app.errorhandler(NodeNotFoundError)
    def handle_node_not_found(e):
        return render_template("error.html", error=e), 404

    @app.errorhandler(404)
    def not_found(e):
        return render_template("404.html"), 404

    return app


if __name__ == "__main__":
    # debug 模式僅供本機開發使用，請勿對外開放
    #
    # ORGTD_NO_RELOAD=1：由「啟動 orgtd.app」設定，關掉 Werkzeug reloader。
    # reloader 預設會多 fork 一個子行程，導致 lsof -i :5001 出現兩個
    # process，只殺父行程殺不乾淨、殘留子行程繼續佔用 port。雙擊啟動不需要
    # autoreload，關掉後只有單一 process，可被準確追蹤與關閉。
    # 手動 Terminal 啟動（不設這個環境變數）行為完全不變。
    use_reloader = os.environ.get("ORGTD_NO_RELOAD") != "1"
    create_app().run(debug=True, use_reloader=use_reloader, port=5001)
