from rest_framework import serializers

from clusters.models import Clusters


class ClusterSerializer(serializers.ModelSerializer):
    class Meta:
        model = Clusters
        fields = [
            'id', 'account_id', 'name', 'location', 'external_id', 'status',
            'kubernetes_version', 'spec', 'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = fields
