import os

from dotenv import load_dotenv
from flask import Flask, render_template
from flask_login import current_user
from flask_wtf.csrf import CSRFProtect

# .env 必須在匯入 config 之前載入，否則 config 讀到的環境變數是空的。
load_dotenv()

import config  # noqa: E402
from db import SessionLocal  # noqa: E402
from errors import NodeNotFoundError  # noqa: E402
from models import User  # noqa: E402
from queries import count_due_today, count_inbox, count_weekly_pomodoros  # noqa: E402
from security import login_manager  # noqa: E402

csrf = CSRFProtect()


def create_app():
    app = Flask(__name__)
    config.apply(app)

    # 所有 POST 一律驗 CSRF token。原本完全沒有防護，單人本機時風險有限，
    # 一旦有登入狀態就是可被外站觸發的寫入漏洞。
    csrf.init_app(app)
    login_manager.init_app(app)

    from views.auth import bp as auth_bp

    @login_manager.user_loader
    def load_user(user_id):
        with SessionLocal() as session:
            return session.get(User, int(user_id))

    from views.agenda import bp as agenda_bp
    from views.calendar import bp as calendar_bp
    from views.dashboard import bp as dashboard_bp
    from views.inbox import bp as inbox_bp
    from views.nodes import bp as nodes_bp
    from views.notes import bp as notes_bp
    from views.pomodoro import bp as pomodoro_bp
    from views.projects import bp as projects_bp
    from views.review import bp as review_bp
    from views.settings import bp as settings_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(inbox_bp)
    app.register_blueprint(agenda_bp)
    app.register_blueprint(nodes_bp)
    app.register_blueprint(projects_bp)
    app.register_blueprint(calendar_bp)
    app.register_blueprint(pomodoro_bp)
    app.register_blueprint(notes_bp)
    app.register_blueprint(review_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(settings_bp)

    @app.context_processor
    def inject_badges():
        # 未登入時（登入頁、註冊頁）不查資料庫，也沒有徽章可算。
        if not current_user.is_authenticated:
            return {"inbox_count": 0, "due_today_count": 0, "weekly_pomodoro_count": 0}
        with SessionLocal() as session:
            uid = current_user.id
            return {
                "inbox_count": count_inbox(session, uid),
                "due_today_count": count_due_today(session, uid),
                "weekly_pomodoro_count": count_weekly_pomodoros(session, uid),
            }

    @app.errorhandler(NodeNotFoundError)
    def handle_node_not_found(e):
        return render_template("error.html", error=e), 404

    @app.errorhandler(404)
    def not_found(e):
        return render_template("404.html"), 404

    return app


if __name__ == "__main__":
    # debug 由 ORGTD_DEBUG 環境變數控制，預設關閉。
    #
    # 原本寫死 debug=True：Werkzeug 的除錯器允許在瀏覽器裡執行任意
    # Python，只要對外開一個 port 就是完整的遠端執行漏洞。對外部署一律
    # 走 gunicorn（見 README），這條路徑只供本機開發。
    #
    # ORGTD_NO_RELOAD=1：由「啟動 orgtd.app」設定，關掉 Werkzeug reloader。
    # reloader 預設會多 fork 一個子行程，導致 lsof -i :5001 出現兩個
    # process，只殺父行程殺不乾淨、殘留子行程繼續佔用 port。
    use_reloader = config.DEBUG and os.environ.get("ORGTD_NO_RELOAD") != "1"
    create_app().run(debug=config.DEBUG, use_reloader=use_reloader, port=5001)
