from rest_framework import serializers

from ..models import KubeResource


class KubeResourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = KubeResource
        fields = [
            'id', 'cluster', 'group', 'version', 'kind', 'namespace', 'name', 'uid',
            'manifest', 'status', 'kube_created_at', 'created_at',
        ]
        read_only_fields = fields
