from pathlib import Path
from django.db import models
from django.conf import settings
from django.utils import timezone


class PipelineStep(models.Model):
    """파이프라인 단계 정의"""
    STEP_CHOICES = [
        ('topic_finder', '주제 찾기'),
        ('researcher', '리서치'),
        ('script_writer', '대본 작성'),
        ('scene_planner', '씬 분할'),
        ('image_prompter', '이미지 프롬프트'),
        ('scene_generator', '이미지 생성'),
        ('video_generator', '동영상 생성'),
        ('video_composer', '영상 편집'),
        ('thumbnail_generator', '썸네일 생성'),
    ]

    name = models.CharField(max_length=50, choices=STEP_CHOICES, unique=True, verbose_name="단계명")
    display_name = models.CharField(max_length=100, verbose_name="표시명")
    description = models.TextField(blank=True, verbose_name="설명")
    order = models.IntegerField(default=0, verbose_name="순서")
    can_run_independently = models.BooleanField(default=False, verbose_name="독립 실행 가능")
    manual_input_description = models.TextField(blank=True, verbose_name="수동 입력 설명")

    class Meta:
        verbose_name = "파이프라인 단계"
        verbose_name_plural = "파이프라인 단계"
        ordering = ['order']

    def __str__(self):
        return self.display_name


class Project(models.Model):
    """프로젝트 (영상 제작 단위)"""
    STATUS_CHOICES = [
        ('draft', '초안'),
        ('in_progress', '진행 중'),
        ('completed', '완료'),
        ('failed', '실패'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='projects')
    name = models.CharField(max_length=200, verbose_name="프로젝트명")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft', verbose_name="상태")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # 최종 결과물 (파일)
    final_video = models.FileField(upload_to='projects/videos/', blank=True, null=True, verbose_name="최종 영상")
    thumbnail = models.ImageField(upload_to='projects/thumbnails/', blank=True, null=True, verbose_name="썸네일")

    class Meta:
        verbose_name = "프로젝트"
        verbose_name_plural = "프로젝트"
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    def get_current_step(self) -> int:
        """현재 완료된 단계 번호 반환"""
        if hasattr(self, 'topic') and self.topic:
            if hasattr(self, 'research') and self.research:
                if hasattr(self, 'draft') and self.draft:
                    if self.scenes.exists():
                        if self.scenes.filter(image_prompt__isnull=False).exists():
                            if self.scenes.filter(image__isnull=False).exclude(image='').exists():
                                if self.scenes.filter(video__isnull=False).exclude(video='').exists():
                                    if self.final_video:
                                        if self.thumbnail:
                                            return 9
                                        return 8
                                    return 7
                                return 6
                            return 5
                        return 4
                    return 3
                return 2
            return 1
        return 0


class Topic(models.Model):
    """주제 선정 결과"""
    project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name='topic')

    video_id = models.CharField(max_length=50, verbose_name="영상 ID")
    title = models.CharField(max_length=500, verbose_name="제목")
    url = models.URLField(verbose_name="URL")
    channel = models.CharField(max_length=200, blank=True, verbose_name="채널")
    view_count = models.BigIntegerField(default=0, verbose_name="조회수")
    viral_ratio = models.FloatField(default=0, verbose_name="바이럴 비율")
    reason = models.TextField(blank=True, verbose_name="선정 이유")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "주제"
        verbose_name_plural = "주제"

    def __str__(self):
        return self.title


class Research(models.Model):
    """리서치 결과 - 터지는 영상을 위한 재료 수집"""
    project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name='research')

    # 기본 정보
    source_url = models.URLField(blank=True, verbose_name="원본 URL")
    topic = models.CharField(max_length=500, blank=True, verbose_name="주제")
    transcript = models.TextField(blank=True, verbose_name="자막 원본")
    summary = models.TextField(blank=True, verbose_name="요약")

    # 제목 관련
    title_candidates = models.JSONField(default=list, verbose_name="제목 후보")
    # best_title: {"title": "...", "pattern": "인용구형", "hook": "..."}
    best_title = models.JSONField(default=dict, verbose_name="선정 제목")

    # 인용구 (3개 이상)
    # [{"quote": "...", "source": "...", "emotion": "한탄/충격/역설", "usable_for": "인트로/제목/..."}]
    quotes = models.JSONField(default=list, verbose_name="인용구")

    # 숫자/통계 (10개 이상)
    # [{"number": "78%", "context": "창업 후 5년 내 폐업률", "impact": "높은 실패율 강조"}]
    numbers = models.JSONField(default=list, verbose_name="숫자/통계")

    # 시간 변화 스토리
    # {"past": {"year": "2010년", "situation": "...", "numbers": "..."},
    #  "turning_point": {"year": "...", "event": "...", "impact": "..."},
    #  "present": {"year": "...", "situation": "...", "numbers": "..."}}
    time_change = models.JSONField(default=dict, verbose_name="시간 변화")

    # 인물 사례 (2개 이상)
    # [{"name": "...", "age": "...", "career": "...", "past": "...", "present": "...", "quote": "...", "emotion": "..."}]
    person_stories = models.JSONField(default=list, verbose_name="인물 사례")

    # 역설/반전 요소
    # {"common_belief": "열심히 하면 성공한다", "reality": "...", "insight": "..."}
    paradox = models.JSONField(default=dict, verbose_name="역설 요소")

    # 시청자 연결
    # {"direct_impact": "...", "indirect_impact": "...", "self_check": "..."}
    viewer_connection = models.JSONField(default=dict, verbose_name="시청자 연결")

    # 서사 구조
    # {"intro": {"hook": "...", "setup": "..."},
    #  "act1": {"title": "...", "points": [...]}, ...}
    narrative_structure = models.JSONField(default=dict, verbose_name="서사 구조")

    # 출처
    # [{"title": "...", "url": "...", "publisher": "...", "key_info": "..."}]
    sources = models.JSONField(default=list, verbose_name="출처")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "리서치"
        verbose_name_plural = "리서치"

    def __str__(self):
        return f"{self.project.name} 리서치"


class Draft(models.Model):
    """대본"""
    project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name='draft')

    title = models.CharField(max_length=500, verbose_name="제목")
    content = models.TextField(verbose_name="대본 내용")
    char_count = models.IntegerField(default=0, verbose_name="글자수")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "대본"
        verbose_name_plural = "대본"

    def __str__(self):
        return f"{self.project.name} 대본"

    def save(self, *args, **kwargs):
        self.char_count = len(self.content) if self.content else 0
        super().save(*args, **kwargs)


class Scene(models.Model):
    """씬 (45-60개)"""
    SECTION_CHOICES = [
        ('intro', '인트로'),
        ('body_1', '본문 1'),
        ('body_2', '본문 2'),
        ('body_3', '본문 3'),
        ('action', '액션'),
        ('outro', '아웃트로'),
    ]

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='scenes')
    scene_number = models.IntegerField(verbose_name="씬 번호")
    section = models.CharField(max_length=20, choices=SECTION_CHOICES, default='body_1', verbose_name="섹션")

    # 텍스트
    narration = models.TextField(verbose_name="나레이션 (자막용)")
    narration_tts = models.TextField(blank=True, verbose_name="나레이션 (TTS용)")

    # 메타
    duration = models.FloatField(default=0, verbose_name="길이(초)")
    has_character = models.BooleanField(default=False, verbose_name="캐릭터 등장")

    # 이미지 프롬프트
    image_prompt = models.TextField(blank=True, verbose_name="이미지 프롬프트")

    # 생성된 파일들
    image = models.ImageField(upload_to='projects/scenes/images/', blank=True, null=True, verbose_name="이미지")
    video = models.FileField(upload_to='projects/scenes/videos/', blank=True, null=True, verbose_name="영상")
    audio = models.FileField(upload_to='projects/scenes/audio/', blank=True, null=True, verbose_name="음성")
    subtitle_file = models.FileField(upload_to='projects/scenes/subtitles/', blank=True, null=True, verbose_name="자막")

    class Meta:
        verbose_name = "씬"
        verbose_name_plural = "씬"
        ordering = ['scene_number']
        unique_together = ['project', 'scene_number']

    def __str__(self):
        return f"{self.project.name} - 씬 {self.scene_number}"


class CharacterSheet(models.Model):
    """캐릭터 시트"""
    project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name='character_sheet')

    image = models.ImageField(upload_to='projects/characters/', verbose_name="캐릭터 시트")
    description = models.TextField(blank=True, verbose_name="캐릭터 설명")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "캐릭터 시트"
        verbose_name_plural = "캐릭터 시트"

    def __str__(self):
        return f"{self.project.name} 캐릭터"


class StepExecution(models.Model):
    """단계 실행 기록"""
    STATUS_CHOICES = [
        ('pending', '대기'),
        ('running', '실행 중'),
        ('completed', '완료'),
        ('failed', '실패'),
        ('cancelled', '취소'),
    ]

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='step_executions')
    step = models.ForeignKey(PipelineStep, on_delete=models.CASCADE, verbose_name="단계")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', verbose_name="상태")
    progress_percent = models.IntegerField(default=0, verbose_name="진행률")
    progress_message = models.CharField(max_length=500, blank=True, verbose_name="진행 메시지")

    # 수동 입력 (독립 실행용)
    manual_input = models.TextField(blank=True, default='', verbose_name="수동 입력")

    # 모델 선택 (단계별로 다르게 지정 가능)
    MODEL_CHOICES = [
        ('flash', 'Gemini 3 Flash'),
        ('pro', 'Gemini 3 Pro'),
    ]
    model_type = models.CharField(max_length=20, choices=MODEL_CHOICES, default='flash', verbose_name="모델")

    # 실행 로그 (실시간 확인용)
    logs = models.JSONField(default=list, verbose_name="실행 로그")

    # 중간 저장 데이터 (이어하기용)
    intermediate_data = models.JSONField(default=dict, verbose_name="중간 데이터")

    # 에러
    error_message = models.TextField(blank=True, verbose_name="에러 메시지")

    # 시간 기록
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "단계 실행"
        verbose_name_plural = "단계 실행"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.project.name} - {self.step.display_name}"

    def start(self):
        """실행 시작"""
        self.status = 'running'
        self.started_at = timezone.now()
        self.progress_percent = 0
        self.progress_message = '시작 중...'
        self.save()

    def update_progress(self, percent: int, message: str = ''):
        """진행률 업데이트"""
        self.progress_percent = min(percent, 100)
        if message:
            self.progress_message = message
        self.save(update_fields=['progress_percent', 'progress_message'])

    def add_log(self, log_type: str, message: str, data: dict = None):
        """로그 추가 (실시간 확인용)

        Args:
            log_type: 'info', 'search', 'result', 'error'
            message: 로그 메시지
            data: 추가 데이터 (검색 결과 등)
        """
        from django.utils import timezone
        log_entry = {
            'time': timezone.now().strftime('%H:%M:%S'),
            'type': log_type,
            'message': message,
        }
        if data:
            log_entry['data'] = data

        self.logs.append(log_entry)
        self.save(update_fields=['logs'])

    def complete(self):
        """완료 처리"""
        self.status = 'completed'
        self.completed_at = timezone.now()
        self.progress_percent = 100
        self.progress_message = '완료'
        self.save()

    def fail(self, error_message: str):
        """실패 처리"""
        self.status = 'failed'
        self.completed_at = timezone.now()
        self.error_message = error_message
        self.progress_message = f'실패: {error_message[:100]}'
        self.save()
