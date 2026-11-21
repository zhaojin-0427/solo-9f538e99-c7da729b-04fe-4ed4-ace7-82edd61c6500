"""Flask entry point for the inspection-record management site."""

from __future__ import annotations

import os
from pathlib import Path

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from werkzeug.exceptions import RequestEntityTooLarge

from . import db
from .csv_import import MAX_BYTES, CsvError, parse_csv
from .judge import CATEGORIES, CATEGORY_LABELS
from .version_def import describe_item, parse_definition

BASE_DIR = Path(__file__).resolve().parent.parent


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret-change-me"),
        DATABASE=os.environ.get(
            "DATABASE", str(BASE_DIR / "data" / "inspection.db")
        ),
        MAX_CONTENT_LENGTH=MAX_BYTES + 1024 * 1024,
    )
    if config:
        app.config.update(config)

    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)
    db.init_db(app.config["DATABASE"])

    app.teardown_appcontext(db.close_db)

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_error):
        flash("上传文件过大，CSV 最大 20 MB。", "error")
        return redirect(request.path.rsplit("/import", 1)[0] or url_for("index"))

    @app.template_filter("category_label")
    def category_label(value: str) -> str:
        return CATEGORY_LABELS.get(value, value)

    @app.get("/")
    def index():
        return render_template("index.html", versions=db.list_versions())

    def _render_new_version(**context):
        return render_template("new_version.html", **context)

    @app.get("/versions/new")
    def new_version():
        return render_template("new_version.html")

    @app.post("/versions")
    def create_version():
        name = (request.form.get("name") or "").strip()
        source = (request.form.get("source") or "").strip()
        definition_text = request.form.get("definition") or ""

        form = {"name": name, "source": source, "definition_text": definition_text}

        if not name:
            return _render_new_version(
                error="请填写表版名称。", **form
            ), 400
        if len(name) > 100:
            return _render_new_version(
                error="表版名称不能超过 100 个字符。", **form
            ), 400

        definition, errors = parse_definition(definition_text)
        if definition is None:
            return _render_new_version(errors=errors, **form), 400

        try:
            version_id = db.create_version(name, source, definition)
        except Exception:
            app.logger.exception("create_version failed")
            return _render_new_version(
                error="保存表版失败，请稍后重试。", **form
            ), 500
        flash("表版已创建，可以上传巡检记录。", "success")
        return redirect(url_for("version_detail", version_id=version_id))

    @app.get("/versions/<int:version_id>")
    def version_detail(version_id: int):
        version = db.get_version(version_id)
        if version is None:
            abort(404)
        definition = version["definition"]
        counts = db.get_counts(version_id, len(definition["items"]))
        items = []
        for index, item in enumerate(definition["items"]):
            items.append(
                {
                    "index": index,
                    "number": index + 1,
                    "name": item["name"],
                    "condition": describe_item(definition, index),
                    "counts": counts[index],
                    "total": sum(counts[index].values()),
                }
            )
        return render_template(
            "version.html",
            version=version,
            items=items,
            categories=CATEGORIES,
            labels=CATEGORY_LABELS,
        )

    @app.post("/versions/<int:version_id>/import")
    def import_csv(version_id: int):
        version = db.get_version(version_id)
        if version is None:
            abort(404)

        upload = request.files.get("csv")
        if upload is None or not upload.filename:
            flash("请选择 CSV 文件。", "error")
            return redirect(url_for("version_detail", version_id=version_id))

        try:
            rows = parse_csv(upload.read(), version["definition"])
            count = db.import_records(version_id, rows, version["definition"])
        except (CsvError, db.DataError) as exc:
            message = exc.message if isinstance(exc, CsvError) else str(exc)
            errors = exc.errors if isinstance(exc, CsvError) else []
            return render_template(
                "import_result.html",
                version=version,
                error=message,
                errors=errors,
            ), 400
        except Exception:
            app.logger.exception("import_records failed")
            return render_template(
                "import_result.html",
                version=version,
                error="导入失败，未写入任何记录，请稍后重试。",
                errors=[],
            ), 500

        flash(f"已导入 {count} 条记录。", "success")
        return redirect(url_for("version_detail", version_id=version_id))

    @app.get("/versions/<int:version_id>/records")
    def record_lookup(version_id: int):
        version = db.get_version(version_id)
        if version is None:
            abort(404)
        record_no = (request.args.get("record_no") or "").strip()
        if record_no:
            if db.get_record(version_id, record_no) is None:
                flash(f"没有找到记录号“{record_no}”。", "error")
            else:
                return redirect(
                    url_for(
                        "record_detail",
                        version_id=version_id,
                        record_no=record_no,
                    )
                )
        return render_template(
            "lookup.html", version=version, record_no=record_no
        )

    @app.get("/versions/<int:version_id>/records/<path:record_no>")
    def record_detail(version_id: int, record_no: str):
        version = db.get_version(version_id)
        if version is None:
            abort(404)
        record = db.get_record(version_id, record_no)
        if record is None:
            abort(404)

        definition = version["definition"]
        answers = record["answers"]
        if len(answers) < len(definition["items"]):
            answers = answers + [""] * (len(definition["items"]) - len(answers))

        rows = []
        for index, item in enumerate(definition["items"]):
            rows.append(
                {
                    "index": index,
                    "number": index + 1,
                    "name": item["name"],
                    "answer": answers[index],
                    "category": record["categories"][index],
                    "category_label": CATEGORY_LABELS[record["categories"][index]],
                    "condition": describe_item(definition, index),
                }
            )

        counts = db.get_counts(version_id, len(definition["items"]))
        count_rows = [
            {
                "number": index + 1,
                "name": definition["items"][index]["name"],
                "counts": counts[index],
                "total": sum(counts[index].values()),
            }
            for index in range(len(definition["items"]))
        ]

        return render_template(
            "record.html",
            version=version,
            record=record,
            rows=rows,
            count_rows=count_rows,
            categories=CATEGORIES,
            labels=CATEGORY_LABELS,
            corrected=request.args.get("corrected") == "1",
        )

    @app.post("/versions/<int:version_id>/records/<path:record_no>/correct")
    def correct(version_id: int, record_no: str):
        version = db.get_version(version_id)
        if version is None:
            abort(404)
        if db.get_record(version_id, record_no) is None:
            abort(404)

        item_raw = request.form.get("item_index", "")
        value = request.form.get("value", "")
        if not item_raw.isdigit():
            flash("更正失败：检查项无效。", "error")
            return redirect(
                url_for(
                    "record_detail", version_id=version_id, record_no=record_no
                )
            )

        try:
            db.correct_answer(version_id, record_no, int(item_raw), value)
        except db.DataError as exc:
            flash(f"更正失败：{exc}", "error")
            return redirect(
                url_for(
                    "record_detail", version_id=version_id, record_no=record_no
                )
            )
        except Exception:
            app.logger.exception("correct_answer failed")
            flash("更正失败，答案、判定和计数均未改变，请稍后重试。", "error")
            return redirect(
                url_for(
                    "record_detail", version_id=version_id, record_no=record_no
                )
            )

        return redirect(
            url_for(
                "record_detail",
                version_id=version_id,
                record_no=record_no,
                corrected=1,
            )
        )

    return app


app = create_app()
