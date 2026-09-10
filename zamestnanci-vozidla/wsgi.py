from app import (
    app,
    db,
    Absence,
    EmployeeDocument,
    VehicleDocument,
    VehicleService,
    ABSENCE_TYPES,
    ABSENCE_COLORS,
    EMPLOYEE_DOC_TYPES,
    VEHICLE_DOC_TYPES,
    parse_date,
    parse_time,
    save_upload,
    delete_stored_file,
)
from document_edit_routes import register_document_edit_routes
from calendar_vehicle_routes import register_calendar_vehicle_routes
from absence_edit_routes import register_absence_edit_routes

register_document_edit_routes(
    app,
    db,
    EmployeeDocument,
    VehicleDocument,
    EMPLOYEE_DOC_TYPES,
    VEHICLE_DOC_TYPES,
    save_upload,
    delete_stored_file,
)

register_absence_edit_routes(
    app,
    db,
    Absence,
    ABSENCE_TYPES,
    parse_date,
    parse_time,
)

register_calendar_vehicle_routes(
    app,
    Absence,
    VehicleDocument,
    VehicleService,
    ABSENCE_TYPES,
    ABSENCE_COLORS,
)

if __name__ == "__main__":
    app.run()
