from fastapi.routing import APIRoute

from api import routes_admin
from api.auth import require_admin, require_csrf_admin


def dependencies(path: str, method: str):
    route = next(
        item for item in routes_admin.router.routes
        if isinstance(item, APIRoute) and item.path == path and method in item.methods
    )
    return {dependency.call for dependency in route.dependant.dependencies}


def test_maintenance_reads_require_admin():
    assert require_admin in dependencies("/admin/maintenance", "GET")
    assert require_admin in dependencies("/admin/maintenance/cleanup-preview", "GET")
    assert require_admin in dependencies("/admin/maintenance/runs", "GET")


def test_system_overview_requires_admin():
    assert require_admin in dependencies("/admin/system-overview", "GET")


def test_maintenance_mutations_require_admin_csrf():
    assert require_csrf_admin in dependencies("/admin/maintenance/settings", "PATCH")
    assert require_csrf_admin in dependencies("/admin/maintenance/cleanup", "POST")


def test_legacy_sweep_route_is_removed():
    assert not any(
        isinstance(item, APIRoute) and item.path == "/admin/sweep"
        for item in routes_admin.router.routes
    )
