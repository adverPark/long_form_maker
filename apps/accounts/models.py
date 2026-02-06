from django.db import models
from django.contrib.auth.models import AbstractUser
from cryptography.fernet import Fernet


class User(AbstractUser):
    """커스텀 사용자 모델"""
    GEMINI_MODEL_CHOICES = [
        ('2.5-flash', 'Gemini 2.5 Flash (최신, 저렴)'),
        ('2.5-pro', 'Gemini 2.5 Pro (최신, 고품질)'),
        ('flash', 'Gemini 3 Flash (빠름)'),
        ('pro', 'Gemini 3 Pro (고품질)'),
    ]

    encryption_key = models.BinaryField(blank=True, null=True, help_text="API 키 암호화용 키")
    gemini_model = models.CharField(
        max_length=20,
        choices=GEMINI_MODEL_CHOICES,
        default='flash',
        verbose_name="Gemini 모델",
        help_text="리서치/대본 작성에 사용할 모델"
    )

    class Meta:
        verbose_name = "사용자"
        verbose_name_plural = "사용자"

    def save(self, *args, **kwargs):
        # 암호화 키가 없으면 생성
        if not self.encryption_key:
            self.encryption_key = Fernet.generate_key()
        super().save(*args, **kwargs)

    def get_fernet(self):
        """암호화/복호화용 Fernet 인스턴스 반환"""
        if not self.encryption_key:
            self.encryption_key = Fernet.generate_key()
            self.save(update_fields=['encryption_key'])
        # PostgreSQL BinaryField는 memoryview로 반환될 수 있음
        key = bytes(self.encryption_key) if isinstance(self.encryption_key, memoryview) else self.encryption_key
        return Fernet(key)


class APIKey(models.Model):
    """사용자별 API 키 저장 (암호화) - 여러 개 등록 가능"""
    SERVICE_CHOICES = [
        ('gemini', 'Google Gemini'),
        ('replicate', 'Replicate'),
        ('freepik', 'Freepik'),
        ('freepik_cookie', 'Freepik 쿠키'),
        ('freepik_wallet', 'Freepik Wallet ID'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='api_keys')
    service = models.CharField(max_length=50, choices=SERVICE_CHOICES, verbose_name="서비스")
    name = models.CharField(max_length=100, default="기본", verbose_name="키 이름", help_text="구분용 이름 (예: 개인용, 회사용)")
    encrypted_key = models.BinaryField(verbose_name="암호화된 API 키")
    is_default = models.BooleanField(default=True, verbose_name="기본 키")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "API 키"
        verbose_name_plural = "API 키"
        ordering = ['service', '-is_default', 'name']

    def __str__(self):
        default_mark = " (기본)" if self.is_default else ""
        return f"{self.get_service_display()} - {self.name}{default_mark}"

    def save(self, *args, **kwargs):
        # 기본 키로 설정 시 같은 서비스의 다른 키는 기본 해제
        if self.is_default:
            APIKey.objects.filter(
                user=self.user,
                service=self.service,
                is_default=True
            ).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)

    def set_key(self, raw_key: str):
        """API 키를 암호화하여 저장"""
        fernet = self.user.get_fernet()
        self.encrypted_key = fernet.encrypt(raw_key.encode())

    def get_key(self) -> str:
        """암호화된 API 키를 복호화하여 반환"""
        fernet = self.user.get_fernet()
        # PostgreSQL BinaryField는 memoryview로 반환될 수 있음
        encrypted = bytes(self.encrypted_key) if isinstance(self.encrypted_key, memoryview) else self.encrypted_key
        return fernet.decrypt(encrypted).decode()

    def get_masked_key(self) -> str:
        """마스킹된 API 키 반환 (앞 4자리만 표시)"""
        key = self.get_key()
        if len(key) > 8:
            return f"{key[:4]}...{key[-4:]}"
        return "****"


class VoiceSample(models.Model):
    """사용자별 목소리 샘플"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='voice_samples')
    name = models.CharField(max_length=100, verbose_name="목소리 이름")
    description = models.TextField(blank=True, verbose_name="설명")
    audio_file = models.FileField(upload_to='voices/', verbose_name="오디오 파일")
    is_default = models.BooleanField(default=False, verbose_name="기본 목소리")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "목소리 샘플"
        verbose_name_plural = "목소리 샘플"

    def __str__(self):
        return f"{self.user.username} - {self.name}"

    def save(self, *args, **kwargs):
        # 기본 목소리로 설정 시 다른 목소리는 기본 해제
        if self.is_default:
            VoiceSample.objects.filter(user=self.user, is_default=True).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)
