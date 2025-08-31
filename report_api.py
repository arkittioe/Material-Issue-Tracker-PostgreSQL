# file: report_api.py
import os
import logging
from flask import Flask, jsonify, request, make_response
from flask_cors import CORS
from data_manager import DataManager
from config_manager import DB_USER as CFG_DB_USER, DB_PASSWORD as CFG_DB_PASSWORD

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("report_api")

app = Flask(__name__)
CORS(app, supports_credentials=True)

# ---------- DataManager lazy factory ----------
# دقت: DataManager به گونه‌ای نوشته شده که اگر user/password را نگیرَد
# از ENV یا config_manager fallback استفاده می‌کند؛ با این helper می‌تونیم
# در هر زمان DataManager را بازسازی کنیم (مثلاً بعد از تغییر creds در ENV).
_dm_instance = None


def get_data_manager(force_reinit: bool = False):
    """
    برمی‌گرداند: نمونه DataManager (ساخته شده یا ساخته جدید)
    ترتیب گرفتن credentials:
      1) متغیر محیطی API_DB_USER / API_DB_PASSWORD
      2) متغیر محیطی APP_DB_USER / APP_DB_PASSWORD (پشتیبانی از پیشنهاد قبلی)
      3) مقادیر داخل config_manager (CFG_DB_USER / CFG_DB_PASSWORD)
      4) در نهایت DataManager بدون credentials (که خودش fallback دارد)
    در صورت خطای ساخت، None بازمی‌گرداند و لاگ می‌زند.
    """
    global _dm_instance
    if _dm_instance is not None and not force_reinit:
        return _dm_instance

    db_user = os.getenv("API_DB_USER") or os.getenv("APP_DB_USER") or (CFG_DB_USER or "")
    db_pass = os.getenv("API_DB_PASSWORD") or os.getenv("APP_DB_PASSWORD") or (CFG_DB_PASSWORD or "")

    try:
        # اگر رشته خالی باشد، DataManager خودش fallback را استفاده می‌کند
        _dm_instance = DataManager(db_user or None, db_pass or None)
        logger.info("DataManager initialized (user=%s)", db_user or "(from config)")
        return _dm_instance
    except Exception as e:
        logger.exception("Failed to initialize DataManager: %s", e)
        _dm_instance = None
        return None


# ---------- Utility helpers ----------
def bad_request(message: str, status_code: int = 400):
    return make_response(jsonify({"error": message}), status_code)


def internal_error(message: str = "Internal Server Error"):
    logger.error("Internal error: %s", message)
    return make_response(jsonify({"error": message}), 500)


# ---------- Health endpoint ----------
@app.route("/api/health")
def health_check():
    """
    وضعیت سرویس + تست اتصال به دیتابیس (اختیاری).
    اگر ENV API_DB_USER/API_DB_PASSWORD تنظیم شده باشد تست اتصال انجام می‌شود.
    """
    dm = get_data_manager()
    if not dm:
        return jsonify({"status": "error", "db": "unavailable", "message": "DataManager not initialized"}), 503

    # اگر DataManager متد تست اتصال داشته باشد (توصیه‌شده)، استفاده می‌کنیم
    try:
        # DataManager.test_connection ممکن است در نسخه‌ی شما موجود باشد
        if hasattr(DataManager, "test_connection"):
            # تلاش برای تست با اعتبارهایی که DataManager استفاده کرده
            ok, msg = DataManager.test_connection(os.getenv("API_DB_USER") or os.getenv("APP_DB_USER") or CFG_DB_USER or "",
                                                 os.getenv("API_DB_PASSWORD") or os.getenv("APP_DB_PASSWORD") or CFG_DB_PASSWORD or "")
            return jsonify({"status": "ok" if ok else "db-error", "db_message": msg})
    except Exception as e:
        logger.exception("Health check DB test failed: %s", e)
        return jsonify({"status": "error", "db": "test_failed", "message": str(e)}), 500

    return jsonify({"status": "ok"})


# ---------- Basic endpoints ----------
@app.route("/api/projects")
def get_projects():
    dm = get_data_manager()
    if not dm:
        return internal_error("Database not available")

    try:
        projects = dm.get_all_projects()
        projects_list = [{"id": p.id, "name": p.name} for p in projects]
        return jsonify(projects_list)
    except Exception as e:
        logger.exception("get_projects failed: %s", e)
        return internal_error(str(e))


@app.route("/api/lines")
def get_lines():
    dm = get_data_manager()
    if not dm:
        return internal_error("Database not available")

    project_id = request.args.get("project_id", type=int)
    if not project_id:
        return bad_request("project_id is required", 400)

    try:
        lines = dm.get_lines_for_project(project_id)
        return jsonify(lines)
    except Exception as e:
        logger.exception("get_lines failed for project_id=%s: %s", project_id, e)
        return internal_error(str(e))


# ---------- Reports endpoints ----------
@app.route("/api/reports/mto-summary")
def get_mto_summary_report():
    dm = get_data_manager()
    if not dm:
        return internal_error("Database not available")

    project_id = request.args.get("project_id", type=int)
    if not project_id:
        return bad_request("project_id is required", 400)

    # خواندن فیلترها
    filters = {
        'item_code': request.args.get('item_code', type=str),
        'description': request.args.get('description', type=str),
        'min_progress': request.args.get('min_progress', type=float),
        'max_progress': request.args.get('max_progress', type=float),
        'sort_by': request.args.get('sort_by', default=None, type=str),
        'sort_order': request.args.get('sort_order', default='asc', type=str)
    }
    # حذف Noneها
    active_filters = {k: v for k, v in filters.items() if v is not None}

    try:
        data = dm.get_project_mto_summary(project_id, **active_filters)
        # Expected structure: {"summary": {...}, "data":[...]}
        return jsonify(data)
    except Exception as e:
        logger.exception("get_mto_summary_report failed for project_id=%s: %s", project_id, e)
        return internal_error(str(e))


@app.route("/api/reports/line-status")
def get_line_status_report():
    dm = get_data_manager()
    if not dm:
        return internal_error("Database not available")

    project_id = request.args.get("project_id", type=int)
    if not project_id:
        return bad_request("project_id is required", 400)

    try:
        data = dm.get_project_line_status_list(project_id)
        return jsonify(data)
    except Exception as e:
        logger.exception("get_line_status_report failed for project_id=%s: %s", project_id, e)
        return internal_error(str(e))


@app.route("/api/reports/detailed-line")
def get_detailed_line_report():
    dm = get_data_manager()
    if not dm:
        return internal_error("Database not available")

    project_id = request.args.get("project_id", type=int)
    line_no = request.args.get("line_no", type=str)
    if not project_id or not line_no:
        return bad_request("project_id and line_no are required", 400)

    try:
        data = dm.get_detailed_line_report(project_id, line_no)
        # Expected: {"bill_of_materials": [...], "miv_history": [...]}
        return jsonify(data)
    except Exception as e:
        logger.exception("get_detailed_line_report failed for project_id=%s line_no=%s: %s", project_id, line_no, e)
        return internal_error(str(e))


@app.route("/api/reports/shortage")
def get_shortage_report():
    dm = get_data_manager()
    if not dm:
        return internal_error("Database not available")

    project_id = request.args.get("project_id", type=int)
    if not project_id:
        return bad_request("project_id is required", 400)

    line_no = request.args.get("line_no", default=None, type=str)

    try:
        data = dm.get_shortage_report(project_id, line_no)
        return jsonify(data)
    except Exception as e:
        logger.exception("get_shortage_report failed for project_id=%s line_no=%s: %s", project_id, line_no, e)
        return internal_error(str(e))


@app.route("/api/reports/spool-inventory")
def get_spool_inventory_report():
    dm = get_data_manager()
    if not dm:
        return internal_error("Database not available")

    try:
        filters = {
            'spool_id': request.args.get('spool_id', type=str),
            'location': request.args.get('location', type=str),
            'component_type': request.args.get('component_type', type=str),
            'material': request.args.get('material', type=str),
            'sort_by': request.args.get('sort_by', default='spool_id', type=str),
            'sort_order': request.args.get('sort_order', default='asc', type=str),
            'page': request.args.get('page', default=1, type=int),
            'per_page': request.args.get('per_page', default=20, type=int),
        }
        active_filters = {k: v for k, v in filters.items() if v is not None}
        data = dm.get_spool_inventory_report(**active_filters)
        return jsonify(data)
    except Exception as e:
        logger.exception("get_spool_inventory_report failed: %s", e)
        return internal_error(str(e))


@app.route("/api/reports/analytics/<report_name>")
def get_analytics_report(report_name):
    dm = get_data_manager()
    if not dm:
        return internal_error("Database not available")

    project_id = request.args.get("project_id", type=int)
    # می‌توانید پارامترهای زمان/فیلتر را از querystring بگیرید و به dm پاس دهید:
    params = {}
    try:
        data = dm.get_report_analytics(project_id, report_name, **params)
        if isinstance(data, tuple) and data and data[0].get("error"):
            # اگر dm خطا داده (با ساختار ({"error":...}, status_code))
            payload, status = data
            return make_response(jsonify(payload), status)
        return jsonify(data)
    except Exception as e:
        logger.exception("get_analytics_report failed for %s: %s", report_name, e)
        return internal_error(str(e))


@app.route("/api/reports/spool-consumption")
def get_spool_consumption_history():
    dm = get_data_manager()
    if not dm:
        return internal_error("Database not available")

    try:
        data = dm.get_spool_consumption_history()
        return jsonify(data)
    except Exception as e:
        logger.exception("get_spool_consumption_history failed: %s", e)
        return internal_error(str(e))


@app.route("/api/activity-logs")
def get_activity_logs():
    dm = get_data_manager()
    if not dm:
        return internal_error("Database not available")

    limit = request.args.get("limit", default=100, type=int)
    try:
        logs = dm.get_activity_logs(limit)
        logs_list = [
            {
                "timestamp": log.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                "user": log.user,
                "action": log.action,
                "details": log.details
            } for log in logs
        ]
        return jsonify(logs_list)
    except Exception as e:
        logger.exception("get_activity_logs failed: %s", e)
        return internal_error(str(e))


# Optional admin endpoint to force reinitialization (useful when you change ENV creds)
@app.route("/api/admin/reload-db", methods=["POST"])
def admin_reload_db():
    # NOTE: Add authentication in production or protect this endpoint
    global _dm_instance
    _dm_instance = None
    dm = get_data_manager(force_reinit=True)
    if not dm:
        return internal_error("Failed to reinitialize DataManager")
    return jsonify({"status": "ok", "message": "DataManager reinitialized"})


if __name__ == "__main__":
    # در حالت توسعه: debug=True مناسب است. برای production از gunicorn یا uwsgi استفاده کنید.
    app.run(debug=True, port=int(os.environ.get("REPORT_API_PORT", 5000)), host="0.0.0.0")
