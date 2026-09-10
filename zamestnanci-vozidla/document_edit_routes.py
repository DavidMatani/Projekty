from flask import flash, redirect, render_template, request, url_for
from flask_login import login_required


def _split_side_note(note):
    value = (note or "").strip()
    for label in ("Přední strana", "Zadní strana"):
        if value == label:
            return label, ""
        prefix = label + " – "
        if value.startswith(prefix):
            return label, value[len(prefix):]
    return None, value


def _compose_note(side_label, note):
    note = (note or "").strip()
    if not side_label:
        return note
    return side_label + (" – " + note if note else "")


def register_document_edit_routes(app, db, EmployeeDocument, VehicleDocument,
                                  employee_doc_types, vehicle_doc_types,
                                  save_upload, delete_stored_file):

    @app.route("/employee-documents/<int:document_id>/edit", methods=["GET", "POST"])
    @login_required
    def employee_document_edit(document_id):
        doc = db.get_or_404(EmployeeDocument, document_id)
        owner = doc.employee
        side_label, clean_note = _split_side_note(doc.note)

        if request.method == "POST":
            doc_type = request.form.get("doc_type", "").strip()
            if doc_type not in employee_doc_types:
                flash("Neplatný typ dokumentu.", "danger")
            else:
                new_file = request.files.get("file")
                old_stored_name = doc.stored_name
                try:
                    if new_file and new_file.filename:
                        original, stored = save_upload(new_file, f"employees/{owner.id}")
                        doc.original_name = original
                        doc.stored_name = stored

                    doc.doc_type = doc_type
                    doc.number = request.form.get("number", "").strip()
                    doc.issued_on = _parse_date(request.form.get("issued_on"))
                    doc.valid_until = _parse_date(request.form.get("valid_until"))
                    doc.note = _compose_note(side_label, request.form.get("note", ""))
                    db.session.commit()

                    if new_file and new_file.filename and old_stored_name and old_stored_name != doc.stored_name:
                        delete_stored_file(old_stored_name)

                    flash("Dokument byl upraven.", "success")
                    return redirect(url_for("employee_detail", employee_id=owner.id))
                except ValueError as exc:
                    db.session.rollback()
                    flash(str(exc), "danger")

        side_label, clean_note = _split_side_note(doc.note)
        return render_template(
            "document_edit.html",
            document=doc,
            owner=owner,
            owner_type="employee",
            doc_types=employee_doc_types,
            side_label=side_label,
            clean_note=clean_note,
        )

    @app.route("/vehicle-documents/<int:document_id>/edit", methods=["GET", "POST"])
    @login_required
    def vehicle_document_edit(document_id):
        doc = db.get_or_404(VehicleDocument, document_id)
        owner = doc.vehicle

        if request.method == "POST":
            doc_type = request.form.get("doc_type", "").strip()
            if doc_type not in vehicle_doc_types:
                flash("Neplatný typ dokumentu.", "danger")
            else:
                new_file = request.files.get("file")
                old_stored_name = doc.stored_name
                try:
                    if new_file and new_file.filename:
                        original, stored = save_upload(new_file, f"vehicles/{owner.id}")
                        doc.original_name = original
                        doc.stored_name = stored

                    doc.doc_type = doc_type
                    doc.number = request.form.get("number", "").strip()
                    doc.issued_on = _parse_date(request.form.get("issued_on"))
                    doc.valid_until = _parse_date(request.form.get("valid_until"))
                    doc.note = request.form.get("note", "").strip()
                    db.session.commit()

                    if new_file and new_file.filename and old_stored_name and old_stored_name != doc.stored_name:
                        delete_stored_file(old_stored_name)

                    flash("Dokument byl upraven.", "success")
                    return redirect(url_for("vehicle_detail", vehicle_id=owner.id))
                except ValueError as exc:
                    db.session.rollback()
                    flash(str(exc), "danger")

        return render_template(
            "document_edit.html",
            document=doc,
            owner=owner,
            owner_type="vehicle",
            doc_types=vehicle_doc_types,
            side_label=None,
            clean_note=doc.note or "",
        )


def _parse_date(value):
    if not value:
        return None
    from datetime import datetime
    return datetime.strptime(value, "%Y-%m-%d").date()
