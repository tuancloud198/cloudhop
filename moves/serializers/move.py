from rest_framework import serializers

from clusters.models.cluster import Clusters
from moves.models.move import Move
from moves.models.move_event import MoveEvent
from moves.services.move import steps_of


class MoveEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = MoveEvent
        fields = ['id', 'step', 'level', 'message', 'created_at']
        read_only_fields = fields


class ClusterRefSerializer(serializers.ModelSerializer):
    account = serializers.IntegerField(source='account_id_id')
    account_name = serializers.CharField(source='account_id.name')

    class Meta:
        model = Clusters
        fields = ['id', 'name', 'location', 'account', 'account_name']
        read_only_fields = fields


class MoveSerializer(serializers.ModelSerializer):
    source_cluster = ClusterRefSerializer(read_only=True)
    target_cluster = ClusterRefSerializer(read_only=True)
    # The statuses this move goes through, in order, for showing its progress
    steps = serializers.SerializerMethodField()

    class Meta:
        model = Move
        fields = [
            'id', 'source_cluster', 'target_cluster', 'namespaces', 'mode', 'storage_class_mapping',
            'storage_location', 'status', 'steps', 'waiting_on', 'failed_step', 'error', 'backup_name',
            'restore_name', 'replicas', 'step_started_at', 'checked_at', 'finished_at', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_steps(self, move):
        return steps_of(move.mode)


class MoveDetailSerializer(MoveSerializer):
    events = MoveEventSerializer(many=True, read_only=True)

    class Meta(MoveSerializer.Meta):
        fields = MoveSerializer.Meta.fields + ['events']
        read_only_fields = fields


class MoveCreateSerializer(serializers.Serializer):
    source_cluster = serializers.PrimaryKeyRelatedField(queryset=Clusters.objects.select_related('account_id'))
    target_cluster = serializers.PrimaryKeyRelatedField(queryset=Clusters.objects.select_related('account_id'))
    namespaces = serializers.ListField(child=serializers.CharField(max_length=63), allow_empty=False, max_length=50)
    mode = serializers.ChoiceField(choices=Move.Mode.choices, default=Move.Mode.CUTOVER)
    storage_class_mapping = serializers.DictField(
        child=serializers.CharField(max_length=253), required=False, default=dict
    )
    storage_location = serializers.CharField(max_length=253, required=False, default='default')
