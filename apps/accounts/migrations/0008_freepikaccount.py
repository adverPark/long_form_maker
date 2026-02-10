from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def migrate_apikey_to_freepik_account(apps, schema_editor):
    """기존 APIKey의 freepik_cookie + freepik_wallet 데이터를 FreepikAccount로 변환"""
    APIKey = apps.get_model('accounts', 'APIKey')
    FreepikAccount = apps.get_model('accounts', 'FreepikAccount')

    # freepik_cookie가 있는 사용자별로 처리
    cookie_keys = APIKey.objects.filter(service='freepik_cookie')
    for cookie_key in cookie_keys:
        user = cookie_key.user
        wallet_key = APIKey.objects.filter(user=user, service='freepik_wallet').first()

        FreepikAccount.objects.create(
            user=user,
            name='계정1',
            # encrypted_key (BinaryField)를 encrypted_cookie (TextField)로 변환
            encrypted_cookie=bytes(cookie_key.encrypted_key).decode('utf-8') if cookie_key.encrypted_key else '',
            encrypted_wallet_id=bytes(wallet_key.encrypted_key).decode('utf-8') if wallet_key and wallet_key.encrypted_key else '',
            order=0,
            is_active=True,
            download_count=0,
            is_exhausted=False,
        )

    # 마이그레이션 후 기존 APIKey 레코드 삭제
    APIKey.objects.filter(service__in=['freepik_cookie', 'freepik_wallet']).delete()


def reverse_migration(apps, schema_editor):
    """역방향: FreepikAccount → APIKey 복원 (데이터 손실 가능)"""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0007_update_freepik_service_choices'),
    ]

    operations = [
        migrations.CreateModel(
            name='FreepikAccount',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100, verbose_name='계정 이름')),
                ('encrypted_cookie', models.TextField(blank=True, default='')),
                ('encrypted_wallet_id', models.TextField(blank=True, default='')),
                ('order', models.IntegerField(default=0, verbose_name='사용 순서')),
                ('is_active', models.BooleanField(default=True, verbose_name='활성화')),
                ('download_count', models.IntegerField(default=0, verbose_name='다운로드 횟수')),
                ('first_download_at', models.DateTimeField(blank=True, null=True, verbose_name='첫 다운로드 시각')),
                ('is_exhausted', models.BooleanField(default=False, verbose_name='소진 여부')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='freepik_accounts', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Freepik 계정',
                'verbose_name_plural': 'Freepik 계정',
                'ordering': ['order', 'pk'],
            },
        ),
        migrations.RunPython(migrate_apikey_to_freepik_account, reverse_migration),
    ]
