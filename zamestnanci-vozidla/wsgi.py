from app import (
    app,
    db,
    EmployeeDocument,
    VehicleDocument,
    EMPLOYEE_DOC_TYPES,
    VEHICLE_DOC_TYPES,
    save_upload,
    delete_stored_file,
)
from document_edit_routes import register_document_edit_routes

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

if __name__ == "__main__":
    app.run()
