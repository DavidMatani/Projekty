from flask import flash, redirect, render_template, request, url_for
from flask_login import login_required


def register_absence_edit_routes(app, db, Absence, ABSENCE_TYPES, parse_date, parse_time):
    @app.route("/absences/<int:absence_id>/edit", methods=["GET", "POST"])
    @login_required
    def absence_edit(absence_id):
        item = db.get_or_404(Absence, absence_id)
        employee = item.employee

        if request.method == "POST":
            start_date = parse_date(request.form.get("start_date"))
            end_date = parse_date(request.form.get("end_date"))
            absence_type = request.form.get("absence_type")

            if not start_date or not end_date or end_date < start_date:
                flash("Zadejte platné datum od–do.", "danger")
            elif absence_type not in ABSENCE_TYPES:
                flash("Neplatný typ události.", "danger")
            else:
                item.absence_type = absence_type
                item.start_date = start_date
                item.end_date = end_date
                item.start_time = parse_time(request.form.get("start_time"))
                item.end_time = parse_time(request.form.get("end_time"))
                item.note = request.form.get("note", "").strip()
                db.session.commit()
                flash("Událost byla upravena.", "success")
                return redirect(url_for("employee_detail", employee_id=employee.id))

        return render_template(
            "absence_form.html",
            employee=employee,
            absence=item,
            edit_mode=True,
        )
