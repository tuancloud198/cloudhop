from rest_framework import serializers

from common.cloud import CloudAPIError

from ..models import Account
from ..services import delete_credential_file, resolve_account, store_credential_file


class AccountSerializer(serializers.ModelSerializer):
    # Alternative to credential_ref: the key file itself, saved on the server
    credential_file = serializers.FileField(write_only=True, required=False)

    class Meta:
        model = Account
        fields = ['id', 'name', 'provider', 'external_id', 'project_id', 'credential_ref',
                  'credential_file', 'is_valid', 'is_active', 'added_at', 'updated_at']
        read_only_fields = ['external_id', 'project_id', 'is_valid', 'is_active', 'added_at', 'updated_at']
        extra_kwargs = {'credential_ref': {'required': False}}

    def validate(self, attrs):
        upload = attrs.pop('credential_file', None)
        if upload is not None:
            try:
                attrs['credential_ref'] = store_credential_file(upload)
            except CloudAPIError as exc:
                raise serializers.ValidationError({'credential_file': str(exc)})
        elif self.instance is None and not attrs.get('credential_ref'):
            raise serializers.ValidationError(
                {'credential_file': 'upload a credential file or give credential_ref'}
            )

        try:
            return self._resolve_identity(attrs, 'credential_file' if upload else 'credential_ref')
        except serializers.ValidationError:
            if upload is not None:
                delete_credential_file(attrs['credential_ref'])
            raise

    def _resolve_identity(self, attrs, error_field):
        # Identity is derived from the credential, so re-resolve whenever it may change
        if self.instance is not None and not {'provider', 'credential_ref'} & attrs.keys():
            return attrs

        account = Account(provider=attrs.get('provider', getattr(self.instance, 'provider', None)),
                          credential_ref=attrs.get('credential_ref', getattr(self.instance, 'credential_ref', None)))
        try:
            resolve_account(account)
        except CloudAPIError as exc:
            raise serializers.ValidationError({error_field: f'invalid credential: {exc}'})

        duplicates = Account.objects.filter(provider=account.provider, external_id=account.external_id)
        if self.instance is not None:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise serializers.ValidationError({error_field: 'account with this credential already exists'})

        attrs.update(external_id=account.external_id, project_id=account.project_id, is_valid=True)
        return attrs

    def update(self, instance, validated_data):
        old_ref = instance.credential_ref
        account = super().update(instance, validated_data)
        if account.credential_ref != old_ref:
            delete_credential_file(old_ref)
        return account
