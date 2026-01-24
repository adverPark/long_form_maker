from django.db import models
from django.conf import settings


class AgentPrompt(models.Model):
    """에이전트별 프롬프트 관리"""
    AGENT_CHOICES = [
        ('topic_finder', 'Topic Finder'),
        ('researcher', 'Researcher'),
        ('script_writer', 'Script Writer'),
        ('scene_planner', 'Scene Planner'),
        ('image_prompter', 'Image Prompter'),
        ('scene_generator', 'Scene Generator'),
        ('video_generator', 'Video Generator'),
        ('video_composer', 'Video Composer'),
        ('thumbnail_generator', 'Thumbnail Generator'),
    ]

    agent_name = models.CharField(max_length=50, choices=AGENT_CHOICES, verbose_name="에이전트")
    prompt_content = models.TextField(verbose_name="프롬프트 내용", help_text="Markdown 형식")
    version = models.IntegerField(default=1, verbose_name="버전")
    is_active = models.BooleanField(default=True, verbose_name="활성화")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='updated_prompts',
        verbose_name="수정자"
    )

    class Meta:
        verbose_name = "에이전트 프롬프트"
        verbose_name_plural = "에이전트 프롬프트"
        ordering = ['agent_name', '-version']

    def __str__(self):
        return f"{self.get_agent_name_display()} v{self.version}"

    def save(self, *args, **kwargs):
        # 활성화 시 같은 에이전트의 다른 프롬프트는 비활성화
        if self.is_active:
            AgentPrompt.objects.filter(
                agent_name=self.agent_name,
                is_active=True
            ).exclude(pk=self.pk).update(is_active=False)
        super().save(*args, **kwargs)


class AgentPromptHistory(models.Model):
    """프롬프트 변경 히스토리"""
    prompt = models.ForeignKey(AgentPrompt, on_delete=models.CASCADE, related_name='history')
    previous_content = models.TextField(verbose_name="이전 내용")
    previous_version = models.IntegerField(verbose_name="이전 버전")
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='prompt_changes',
        verbose_name="변경자"
    )
    changed_at = models.DateTimeField(auto_now_add=True)
    change_note = models.TextField(blank=True, verbose_name="변경 메모")

    class Meta:
        verbose_name = "프롬프트 변경 이력"
        verbose_name_plural = "프롬프트 변경 이력"
        ordering = ['-changed_at']

    def __str__(self):
        return f"{self.prompt} - {self.changed_at}"
