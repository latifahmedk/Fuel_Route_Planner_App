from django.apps import AppConfig


class RoutePlannerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'route_planner'

    def ready(self):
        # Pre-load fuel data at startup so first request is fast
        from .fuel_data import get_stations
        stations = get_stations()
        print(f"[FuelRoutePlanner] Loaded {len(stations)} fuel stations.")
