from pathlib import Path
from django.db import models
from django.conf import settings
from django.utils import timezone


class PipelineStep(models.Model):
    """파이프라인 단계 정의"""
    STEP_CHOICES = [
        ('topic_finder', '주제 찾기'),
        ('youtube_collector', 'YouTube 수집'),
        ('content_analyzer', '콘텐츠 분석'),
        ('researcher', '리서치'),
        ('script_writer', '대본 작성'),
        ('scene_planner', '씬 분할'),
        ('image_prompter', '이미지 프롬프트'),
        ('scene_generator', '이미지 생성'),
        ('tts_generator', 'TTS 생성'),
        ('video_generator', '동영상 생성'),
        ('video_composer', '영상 편집'),
        ('thumbnail_generator', '썸네일 생성'),
        ('upload_info_generator', '업로드 정보 생성'),
        ('freepik_video', '스톡 영상'),
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

    # 이미지 생성 모델 선택
    IMAGE_MODEL_CHOICES = [
        ('gemini-3-pro', 'Gemini 3 Pro ($0.134/장, 한글 OK)'),
        ('gemini-2.5-flash', 'Gemini 2.5 Flash ($0.039/장, 한글 △)'),
        ('flux-schnell', 'Replicate FLUX.1-schnell ($0.003/장, 빠름)'),
        ('sdxl', 'Replicate SDXL ($0.005/장)'),
    ]
    image_model = models.CharField(max_length=30, choices=IMAGE_MODEL_CHOICES,
        default='gemini-3-pro', verbose_name="이미지 생성 모델")

    # 프리셋 선택 (전역 설정에서 선택)
    image_style = models.ForeignKey('ImageStylePreset', on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name="이미지 스타일")
    character = models.ForeignKey('CharacterPreset', on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name="캐릭터")
    voice = models.ForeignKey('VoicePreset', on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name="TTS 음성")
    thumbnail_style = models.ForeignKey('ThumbnailStylePreset', on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name="썸네일 스타일")

    # 스톡 영상 간격 (0=사용 안함)
    freepik_interval = models.IntegerField(
        default=2, verbose_name="스톡 영상 간격",
        help_text="N번째 씬마다 스톡 영상 삽입 (0=사용 안함)"
    )

    # 최종 결과물 (파일)
    final_video = models.FileField(upload_to='projects/videos/', blank=True, null=True, verbose_name="최종 영상")
    thumbnail = models.ImageField(upload_to='projects/thumbnails/', blank=True, null=True, verbose_name="썸네일")
    full_subtitles = models.FileField(upload_to='projects/subtitles/', blank=True, null=True, verbose_name="전체 자막")

    class Meta:
        verbose_name = "프로젝트"
        verbose_name_plural = "프로젝트"
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    def delete(self, *args, **kwargs):
        """프로젝트 삭제 시 관련 파일도 함께 삭제"""
        import glob
        from pathlib import Path
        from django.conf import settings

        # 씬의 파일들 삭제
        for scene in self.scenes.all():
            if scene.image:
                scene.image.delete(save=False)
            if scene.video:
                scene.video.delete(save=False)
            if scene.stock_video:
                scene.stock_video.delete(save=False)
            if scene.audio:
                scene.audio.delete(save=False)
            if scene.subtitle_file:
                scene.subtitle_file.delete(save=False)

        # 프로젝트 파일들 삭제
        if self.final_video:
            self.final_video.delete(save=False)
        if self.thumbnail:
            self.thumbnail.delete(save=False)
        if self.full_subtitles:
            self.full_subtitles.delete(save=False)

        # temp_clips 삭제 (영상 편집 임시 파일)
        temp_clips_dir = Path(settings.MEDIA_ROOT) / 'temp_clips'
        if temp_clips_dir.exists():
            for clip_file in temp_clips_dir.glob(f'{self.pk}_*.mp4'):
                try:
                    clip_file.unlink()
                except Exception:
                    pass
            # concat 리스트 파일
            concat_file = temp_clips_dir / f'{self.pk}_concat.txt'
            if concat_file.exists():
                try:
                    concat_file.unlink()
                except Exception:
                    pass

        # ASS 자막 디렉토리 삭제 (video_composer 생성)
        import shutil
        ass_dir = Path(settings.MEDIA_ROOT) / 'projects' / 'subtitles' / str(self.pk)
        if ass_dir.exists():
            try:
                shutil.rmtree(ass_dir)
            except Exception:
                pass

        super().delete(*args, **kwargs)

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

    # YouTube 자막 메타데이터
    transcript_language = models.CharField(max_length=10, blank=True, verbose_name="자막 언어")
    transcript_is_auto = models.BooleanField(default=False, verbose_name="자동생성 자막 여부")

    # 콘텐츠 분석 결과 (content_analyzer에서 생성)
    content_analysis = models.JSONField(default=dict, blank=True, verbose_name="콘텐츠 분석",
        help_text="summary, key_topics, viewer_interests, highlight_comments, draft_outline, research_keywords 등")

    # 타겟 리서치 키워드 (content_analyzer에서 추출)
    target_keywords = models.JSONField(default=list, blank=True, verbose_name="리서치 키워드")

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

    # 기사별 요약 (검색 결과 전체 보존)
    # [{"query": "검색어", "summary": "요약 내용", "sources": [...]}]
    article_summaries = models.JSONField(default=list, verbose_name="기사별 요약")

    # 수동 추가 자료 (리서치 없이 대본 작성용)
    manual_notes = models.TextField(blank=True, verbose_name="수동 추가 자료",
        help_text="직접 입력한 참고 자료. 대본 작성 시 활용됨.")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "리서치"
        verbose_name_plural = "리서치"

    def __str__(self):
        return f"{self.project.name} 리서치"


class YouTubeComment(models.Model):
    """YouTube 댓글"""
    research = models.ForeignKey(Research, on_delete=models.CASCADE, related_name='youtube_comments')
    comment_id = models.CharField(max_length=50, verbose_name="댓글 ID")
    author = models.CharField(max_length=100, verbose_name="작성자")
    text = models.TextField(verbose_name="댓글 내용")
    like_count = models.IntegerField(default=0, verbose_name="좋아요 수")
    reply_count = models.IntegerField(default=0, verbose_name="답글 수")
    published_at = models.DateTimeField(null=True, blank=True, verbose_name="작성일")
    is_highlighted = models.BooleanField(default=False, verbose_name="주요 댓글")

    class Meta:
        verbose_name = "YouTube 댓글"
        verbose_name_plural = "YouTube 댓글"
        ordering = ['-like_count']

    def __str__(self):
        return f"{self.author}: {self.text[:50]}"


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

    SUBTITLE_STATUS_CHOICES = [
        ('none', '없음'),
        ('matched', '매칭됨'),
        ('mismatch', '불일치'),
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
    stock_video = models.FileField(upload_to='projects/scenes/stock_videos/', blank=True, null=True, verbose_name="스톡 영상")
    stock_video_id = models.CharField(max_length=50, blank=True, default='', verbose_name="스톡 영상 ID")
    audio = models.FileField(upload_to='projects/scenes/audio/', blank=True, null=True, verbose_name="음성")
    subtitle_file = models.FileField(upload_to='projects/scenes/subtitles/', blank=True, null=True, verbose_name="자막")

    # TTS 생성 후 실제 오디오 길이
    audio_duration = models.FloatField(default=0, verbose_name="실제 오디오 길이(초)")

    # 자막 검증 상태
    subtitle_status = models.CharField(max_length=20, choices=SUBTITLE_STATUS_CHOICES, default='none', verbose_name="자막 상태")
    subtitle_word_count = models.IntegerField(default=0, verbose_name="SRT 단어 수")
    narration_word_count = models.IntegerField(default=0, verbose_name="원본 단어 수")

    class Meta:
        verbose_name = "씬"
        verbose_name_plural = "씬"
        ordering = ['scene_number']
        unique_together = ['project', 'scene_number']

    def __str__(self):
        return f"{self.project.name} - 씬 {self.scene_number}"


# =============================================
# 전역 프리셋 (설정에서 관리, 프로젝트에서 선택)
# =============================================

class ImageStylePreset(models.Model):
    """이미지 스타일 프리셋 - 사용자별 설정"""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='image_styles', null=True, blank=True)
    name = models.CharField(max_length=100, verbose_name="이름")
    description = models.TextField(blank=True, verbose_name="설명")

    # 스타일 프롬프트 (영어) - 선택사항
    style_prompt = models.TextField(blank=True, verbose_name="스타일 프롬프트",
        help_text="선택사항. 없으면 샘플 이미지만 참조")

    is_default = models.BooleanField(default=False, verbose_name="기본값")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "이미지 스타일"
        verbose_name_plural = "이미지 스타일"
        ordering = ['-is_default', 'name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if self.is_default and self.user:
            # 같은 사용자의 다른 기본값 해제
            ImageStylePreset.objects.filter(user=self.user, is_default=True).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)


class StyleSampleImage(models.Model):
    """스타일 프리셋의 샘플 이미지"""
    style = models.ForeignKey(ImageStylePreset, on_delete=models.CASCADE, related_name='sample_images')
    image = models.ImageField(upload_to='presets/styles/', verbose_name="샘플 이미지")
    description = models.CharField(max_length=200, blank=True, verbose_name="설명")
    order = models.IntegerField(default=0, verbose_name="순서")

    class Meta:
        verbose_name = "스타일 샘플 이미지"
        verbose_name_plural = "스타일 샘플 이미지"
        ordering = ['order']

    def __str__(self):
        return f"{self.style.name} 샘플 {self.order}"


class CharacterPreset(models.Model):
    """캐릭터 프리셋 - 사용자별 설정"""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='characters', null=True, blank=True)
    name = models.CharField(max_length=100, verbose_name="이름")
    description = models.TextField(blank=True, verbose_name="설명")

    # 캐릭터 이미지
    image = models.ImageField(upload_to='presets/characters/', verbose_name="캐릭터 이미지")

    # 캐릭터 프롬프트 설명 (영어) - 선택사항
    character_prompt = models.TextField(blank=True, verbose_name="캐릭터 프롬프트",
        help_text="선택사항. 없으면 캐릭터 이미지만 참조")

    is_default = models.BooleanField(default=False, verbose_name="기본값")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "캐릭터"
        verbose_name_plural = "캐릭터"
        ordering = ['-is_default', 'name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if self.is_default and self.user:
            CharacterPreset.objects.filter(user=self.user, is_default=True).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)


class VoicePreset(models.Model):
    """TTS 음성 프리셋 - 사용자별 설정"""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='voices', null=True, blank=True)
    name = models.CharField(max_length=100, verbose_name="이름")
    description = models.TextField(blank=True, verbose_name="설명")

    # 참조 음성
    reference_audio = models.FileField(upload_to='presets/voices/', verbose_name="참조 음성 (WAV)")
    reference_text = models.TextField(verbose_name="참조 음성 텍스트",
        help_text="참조 음성에서 말하는 내용")

    # TTS 파라미터
    temperature = models.FloatField(default=0.7, verbose_name="Temperature")
    top_p = models.FloatField(default=0.7, verbose_name="Top P")
    repetition_penalty = models.FloatField(default=1.2, verbose_name="Repetition Penalty")
    seed = models.IntegerField(default=42, verbose_name="Seed")

    is_default = models.BooleanField(default=False, verbose_name="기본값")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "TTS 음성"
        verbose_name_plural = "TTS 음성"
        ordering = ['-is_default', 'name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if self.is_default and self.user:
            VoicePreset.objects.filter(user=self.user, is_default=True).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)


class ThumbnailStylePreset(models.Model):
    """썸네일 스타일 프리셋 - 사용자별 설정"""
    STYLE_CHOICES = [
        ('youtube', 'YouTube (눈에 띄는)'),
        ('minimalist', '미니멀 (깔끔한)'),
        ('bold', '볼드 (강렬한)'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='thumbnail_styles', null=True, blank=True)
    name = models.CharField(max_length=100, verbose_name="이름")
    description = models.TextField(blank=True, verbose_name="설명")

    # 스타일 타입
    style_type = models.CharField(max_length=20, choices=STYLE_CHOICES, default='youtube', verbose_name="스타일 타입")

    # 예시 이미지
    example_image = models.ImageField(upload_to='presets/thumbnails/', blank=True, null=True, verbose_name="예시 이미지")

    # 썸네일 프롬프트 템플릿
    prompt_template = models.TextField(verbose_name="프롬프트 템플릿",
        help_text="사용 가능한 변수: {title}, {hook}, {main_keyword}",
        default="""YouTube thumbnail for a video.

Main visual: {main_keyword} related scene
Korean text: '{title}' (large, bold, contrasting color)
Style: {style_type}, dramatic lighting
Emotion: curiosity, urgency

Technical requirements:
- 16:9 aspect ratio (1280x720)
- High contrast for mobile visibility
- Clean composition with focal point""")

    is_default = models.BooleanField(default=False, verbose_name="기본값")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "썸네일 스타일"
        verbose_name_plural = "썸네일 스타일"
        ordering = ['-is_default', 'name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if self.is_default and self.user:
            ThumbnailStylePreset.objects.filter(user=self.user, is_default=True).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)


class UploadInfo(models.Model):
    """YouTube 업로드 정보"""
    project = models.OneToOneField('Project', on_delete=models.CASCADE, related_name='upload_info')

    # 기본 정보
    title = models.CharField(max_length=100, verbose_name="제목", help_text="70자 이내 권장")
    description = models.TextField(blank=True, verbose_name="설명")
    tags = models.JSONField(default=list, verbose_name="태그")

    # 타임라인 (자동 생성)
    timeline = models.JSONField(default=list, verbose_name="타임라인",
        help_text='[{"time": "0:00", "title": "인트로"}, ...]')

    # 썸네일 프롬프트
    thumbnail_prompt = models.TextField(blank=True, verbose_name="썸네일 프롬프트")

    # 메타 정보
    category_id = models.CharField(max_length=10, default='25', verbose_name="카테고리 ID")
    category_name = models.CharField(max_length=50, default='News & Politics', verbose_name="카테고리명")
    privacy_status = models.CharField(max_length=20, default='private', verbose_name="공개 상태")
    made_for_kids = models.BooleanField(default=False, verbose_name="아동용")
    contains_synthetic_media = models.BooleanField(default=True, verbose_name="AI 생성 콘텐츠")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "업로드 정보"
        verbose_name_plural = "업로드 정보"

    def __str__(self):
        return f"{self.project.name} 업로드 정보"

    def get_full_description(self) -> str:
        """타임라인과 태그가 포함된 전체 설명 생성"""
        parts = [self.description]

        # 타임라인 추가
        if self.timeline:
            parts.append("\n\n⏱️ 타임라인")
            for item in self.timeline:
                parts.append(f"{item['time']} {item['title']}")

        # 해시태그 추가
        if self.tags:
            parts.append("\n")
            parts.append(" ".join([f"#{tag}" for tag in self.tags]))

        return "\n".join(parts)

    def get_tags_string(self) -> str:
        """태그를 쉼표로 구분된 문자열로 반환"""
        return ", ".join(self.tags) if self.tags else ""


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
        ('2.5-flash', 'Gemini 2.5 Flash'),
        ('2.5-pro', 'Gemini 2.5 Pro'),
        ('flash', 'Gemini 3 Flash'),
        ('pro', 'Gemini 3 Pro'),
    ]
    model_type = models.CharField(max_length=20, choices=MODEL_CHOICES, default='2.5-flash', verbose_name="모델")

    # 실행 로그 (실시간 확인용)
    logs = models.JSONField(default=list, verbose_name="실행 로그")

    # 중간 저장 데이터 (이어하기용)
    intermediate_data = models.JSONField(default=dict, verbose_name="중간 데이터")

    # 토큰 사용량 & 비용
    input_tokens = models.IntegerField(default=0, verbose_name="입력 토큰")
    output_tokens = models.IntegerField(default=0, verbose_name="출력 토큰")
    total_tokens = models.IntegerField(default=0, verbose_name="총 토큰")
    estimated_cost = models.DecimalField(max_digits=10, decimal_places=6, default=0, verbose_name="예상 비용(USD)")

    # 에러
    error_message = models.TextField(blank=True, verbose_name="에러 메시지")

    # 완료 확인 (사용자가 "확인" 눌렀는지)
    acknowledged = models.BooleanField(default=False, verbose_name="확인됨")

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
        log_entry = {
            'time': timezone.localtime().strftime('%H:%M:%S'),
            'type': log_type,
            'message': message,
        }
        if data:
            log_entry['data'] = data

        if self.logs is None:
            self.logs = []
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


class TTSJob(models.Model):
    """TTS 작업 큐 - DB 기반 작업 관리"""
    STATUS_CHOICES = [
        ('pending', '대기'),
        ('processing', '처리 중'),
        ('completed', '완료'),
        ('cancelled', '취소'),
        ('failed', '실패'),
    ]

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='tts_jobs')
    scene = models.ForeignKey(Scene, on_delete=models.CASCADE, related_name='tts_jobs')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    # 시간 기록
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    # 에러 & 재시도
    error_message = models.TextField(blank=True)
    retry_count = models.IntegerField(default=0)

    class Meta:
        verbose_name = "TTS 작업"
        verbose_name_plural = "TTS 작업"
        ordering = ['created_at', 'scene__scene_number']

    def __str__(self):
        return f"TTS #{self.id} - 프로젝트 {self.project_id} 씬 {self.scene.scene_number} ({self.status})"

    @classmethod
    def create_jobs_for_project(cls, project):
        """프로젝트의 모든 씬에 대해 TTS 작업 생성 (이미 오디오 있는 씬 제외)"""
        # 이미 pending/processing 작업이 있으면 중복 방지
        existing = cls.objects.filter(
            project=project,
            status__in=['pending', 'processing']
        ).exists()
        if existing:
            return 0, "이미 진행 중인 TTS 작업이 있습니다."

        # 오디오 없는 씬만 대상
        scenes = project.scenes.filter(
            models.Q(audio='') | models.Q(audio__isnull=True)
        ).exclude(
            models.Q(narration='') & models.Q(narration_tts='')
        ).order_by('scene_number')

        jobs = []
        for scene in scenes:
            jobs.append(cls(project=project, scene=scene, status='pending'))

        if jobs:
            cls.objects.bulk_create(jobs)

        return len(jobs), f"{len(jobs)}개 TTS 작업 생성됨"

    @classmethod
    def cancel_project_jobs(cls, project):
        """프로젝트의 pending 작업 모두 취소"""
        count = cls.objects.filter(
            project=project,
            status='pending'
        ).update(status='cancelled')
        return count

    @classmethod
    def get_next_job(cls):
        """다음 처리할 작업 가져오기 (atomic)"""
        from django.db import transaction

        with transaction.atomic():
            job = cls.objects.select_for_update(skip_locked=True).filter(
                status='pending'
            ).first()

            if job:
                job.status = 'processing'
                job.started_at = timezone.now()
                job.save(update_fields=['status', 'started_at'])

            return job

    @classmethod
    def recover_stuck_jobs(cls, timeout_minutes=10):
        """stuck된 processing 작업 복구"""
        from datetime import timedelta

        cutoff = timezone.now() - timedelta(minutes=timeout_minutes)
        count = cls.objects.filter(
            status='processing',
            started_at__lt=cutoff
        ).update(status='pending', started_at=None)
        return count

    def mark_completed(self):
        """완료 처리"""
        self.status = 'completed'
        self.completed_at = timezone.now()
        self.save(update_fields=['status', 'completed_at'])

    def mark_failed(self, error_message: str):
        """실패 처리"""
        self.status = 'failed'
        self.completed_at = timezone.now()
        self.error_message = error_message
        self.retry_count += 1
        self.save(update_fields=['status', 'completed_at', 'error_message', 'retry_count'])
