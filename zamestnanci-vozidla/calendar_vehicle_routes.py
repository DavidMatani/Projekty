from datetime import date, datetime, timedelta

from flask import jsonify, request, url_for
from flask_login import login_required


VEHICLE_WARNING_COLOR = "#b7791f"
VEHICLE_DUE_COLOR = "#334155"
VEHICLE_OVERDUE_COLOR = "#b42318"


def _parse_date(value):
    return datetime.strptime(value, "%Y-%m-%d").date() if value else None


def _in_range(day, start, end):
    if not day:
        return False
    if start and day < start:
        return False
    if end and day >= end:
        return False
    return True


def _vehicle_note(vehicle, label, due, extra=""):
    parts = [f"Vozidlo: {vehicle.label}", f"SPZ: {vehicle.plate}", f"{label}: {due.strftime('%d.%m.%Y')}"]
    if extra:
        parts.append(extra)
    return "\n".join(parts)


def register_calendar_vehicle_routes(
    app,
    Absence,
    VehicleDocument,
    VehicleService,
    ABSENCE_TYPES,
    ABSENCE_COLORS,
):
    @app.route("/api/calendar-events")
    @login_required
    def api_calendar_events():
        start = _parse_date((request.args.get("start") or "")[:10])
        end = _parse_date((request.args.get("end") or "")[:10])
        source = (request.args.get("source") or "all").lower()
        employee_id = request.args.get("employee_id", type=int)
        vehicle_id = request.args.get("vehicle_id", type=int)
        events = []

        if source in {"all", "employees"}:
            query = Absence.query
            if employee_id:
                query = query.filter_by(employee_id=employee_id)
            if start and end:
                query = query.filter(Absence.start_date < end, Absence.end_date >= start)

            for item in query.all():
                timed = item.start_time is not None
                if timed:
                    start_value = datetime.combine(item.start_date, item.start_time).isoformat()
                    event_end_time = item.end_time or item.start_time
                    end_value = datetime.combine(item.end_date, event_end_time).isoformat()
                else:
                    start_value = item.start_date.isoformat()
                    end_value = (item.end_date + timedelta(days=1)).isoformat()

                events.append({
                    "id": f"absence-{item.id}",
                    "title": f"{item.employee.full_name} – {ABSENCE_TYPES.get(item.absence_type, item.absence_type)}",
                    "start": start_value,
                    "end": end_value,
                    "allDay": not timed,
                    "backgroundColor": ABSENCE_COLORS.get(item.absence_type, "#6c757d"),
                    "borderColor": ABSENCE_COLORS.get(item.absence_type, "#6c757d"),
                    "extendedProps": {
                        "event_kind": "employee",
                        "note": item.note or "",
                    },
                })

        if source in {"all", "vehicles"}:
            today = date.today()

            document_query = VehicleDocument.query.filter(VehicleDocument.valid_until.isnot(None))
            if vehicle_id:
                document_query = document_query.filter(VehicleDocument.vehicle_id == vehicle_id)
            if start:
                document_query = document_query.filter(VehicleDocument.valid_until >= start)
            if end:
                document_query = document_query.filter(VehicleDocument.valid_until < end + timedelta(days=30))

            for doc in document_query.all():
                due = doc.valid_until
                vehicle = doc.vehicle
                warning_day = due - timedelta(days=30)
                detail_url = url_for("vehicle_detail", vehicle_id=vehicle.id)
                extra = f"Číslo dokumentu: {doc.number}" if doc.number else ""
                note = _vehicle_note(vehicle, "Platnost do", due, extra)

                if due >= today and _in_range(warning_day, start, end):
                    events.append({
                        "id": f"vehicle-document-warning-{doc.id}",
                        "title": f"⚠ {vehicle.label} – {doc.doc_type} za 30 dní",
                        "start": warning_day.isoformat(),
                        "allDay": True,
                        "backgroundColor": VEHICLE_WARNING_COLOR,
                        "borderColor": VEHICLE_WARNING_COLOR,
                        "extendedProps": {
                            "event_kind": "vehicle",
                            "vehicle_id": vehicle.id,
                            "detail_url": detail_url,
                            "note": note,
                        },
                    })

                if _in_range(due, start, end):
                    due_color = VEHICLE_OVERDUE_COLOR if due < today else VEHICLE_DUE_COLOR
                    due_word = "po termínu" if due < today else "termín"
                    events.append({
                        "id": f"vehicle-document-due-{doc.id}",
                        "title": f"🚚 {vehicle.label} – {doc.doc_type} {due_word}",
                        "start": due.isoformat(),
                        "allDay": True,
                        "backgroundColor": due_color,
                        "borderColor": due_color,
                        "extendedProps": {
                            "event_kind": "vehicle",
                            "vehicle_id": vehicle.id,
                            "detail_url": detail_url,
                            "note": note,
                        },
                    })

            service_query = VehicleService.query.filter(VehicleService.next_date.isnot(None))
            if vehicle_id:
                service_query = service_query.filter(VehicleService.vehicle_id == vehicle_id)
            if start:
                service_query = service_query.filter(VehicleService.next_date >= start)
            if end:
                service_query = service_query.filter(VehicleService.next_date < end + timedelta(days=30))

            for service in service_query.all():
                due = service.next_date
                vehicle = service.vehicle
                warning_day = due - timedelta(days=30)
                detail_url = url_for("vehicle_detail", vehicle_id=vehicle.id)
                extra_parts = []
                if service.description:
                    extra_parts.append(f"Servis: {service.description}")
                if service.next_odometer:
                    extra_parts.append(f"Další servis při {service.next_odometer:,} km".replace(",", " "))
                note = _vehicle_note(vehicle, "Další servis", due, "\n".join(extra_parts))

                if due >= today and _in_range(warning_day, start, end):
                    events.append({
                        "id": f"vehicle-service-warning-{service.id}",
                        "title": f"⚠ {vehicle.label} – servis za 30 dní",
                        "start": warning_day.isoformat(),
                        "allDay": True,
                        "backgroundColor": VEHICLE_WARNING_COLOR,
                        "borderColor": VEHICLE_WARNING_COLOR,
                        "extendedProps": {
                            "event_kind": "vehicle",
                            "vehicle_id": vehicle.id,
                            "detail_url": detail_url,
                            "note": note,
                        },
                    })

                if _in_range(due, start, end):
                    due_color = VEHICLE_OVERDUE_COLOR if due < today else VEHICLE_DUE_COLOR
                    due_word = "po termínu" if due < today else "termín"
                    events.append({
                        "id": f"vehicle-service-due-{service.id}",
                        "title": f"🔧 {vehicle.label} – servis {due_word}",
                        "start": due.isoformat(),
                        "allDay": True,
                        "backgroundColor": due_color,
                        "borderColor": due_color,
                        "extendedProps": {
                            "event_kind": "vehicle",
                            "vehicle_id": vehicle.id,
                            "detail_url": detail_url,
                            "note": note,
                        },
                    })

        return jsonify(events)
