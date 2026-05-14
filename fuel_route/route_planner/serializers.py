from rest_framework import serializers


class RoutePlanRequestSerializer(serializers.Serializer):
    start = serializers.CharField(
        max_length=300,
        help_text="Starting location within the USA (city/state or full address)",
    )
    finish = serializers.CharField(
        max_length=300,
        help_text="Destination location within the USA (city/state or full address)",
    )

    def validate_start(self, value):
        return value.strip()

    def validate_finish(self, value):
        return value.strip()
