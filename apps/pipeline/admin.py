from django.contrib import admin
from django.utils.html import format_html
from .models import (
    PipelineStep, Project, Topic, Research, Draft, Scene, StepExecution,
    ImageStylePreset, StyleSampleImage, CharacterPreset, VoicePreset
)


@admin.register(PipelineStep)
class PipelineStepAdmin(admin.ModelAdmin):
    list_display = ['name', 'display_name', 'order', 'can_run_independently']
    list_editable = ['order', 'can_run_independently']
    ordering = ['order']


# =============================================
# 전역 프리셋 관리
# =============================================

class StyleSampleImageInline(admin.TabularInline):
    model = StyleSampleImage
    extra = 1
    fields = ['image', 'description', 'order']


@admin.register(ImageStylePreset)
class ImageStylePresetAdmin(admin.ModelAdmin):
    list_display = ['name', 'is_default', 'sample_count', 'created_at']
    list_filter = ['is_default']
    search_fields = ['name', 'style_prompt']
    inlines = [StyleSampleImageInline]
    fieldsets = (
        (None, {
            'fields': ('name', 'description', 'is_default'),
        }),
        ('스타일 설정', {
            'fields': ('style_prompt',),
        }),
    )

    def sample_count(self, obj):
        return obj.sample_images.count()
    sample_count.short_description = '샘플 이미지'


@admin.register(CharacterPreset)
class CharacterPresetAdmin(admin.ModelAdmin):
    list_display = ['name', 'preview_image', 'is_default', 'created_at']
    list_filter = ['is_default']
    search_fields = ['name', 'character_prompt']
    fieldsets = (
        (None, {
            'fields': ('name', 'description', 'is_default'),
        }),
        ('캐릭터 설정', {
            'fields': ('image', 'character_prompt'),
        }),
    )

    def preview_image(self, obj):
        if obj.image:
            return format_html('<img src="{}" style="max-height: 50px;"/>', obj.image.url)
        return '-'
    preview_image.short_description = '미리보기'


@admin.register(VoicePreset)
class VoicePresetAdmin(admin.ModelAdmin):
    list_display = ['name', 'is_default', 'temperature', 'created_at']
    list_filter = ['is_default']
    search_fields = ['name', 'reference_text']
    fieldsets = (
        (None, {
            'fields': ('name', 'description', 'is_default'),
        }),
        ('참조 음성', {
            'fields': ('reference_audio', 'reference_text'),
        }),
        ('TTS 파라미터', {
            'fields': ('temperature', 'top_p', 'repetition_penalty', 'seed'),
            'classes': ('collapse',),
        }),
    )


# =============================================
# 프로젝트 관리
# =============================================

class TopicInline(admin.StackedInline):
    model = Topic
    extra = 0


class ResearchInline(admin.StackedInline):
    model = Research
    extra = 0


class DraftInline(admin.StackedInline):
    model = Draft
    extra = 0


class SceneInline(admin.TabularInline):
    model = Scene
    extra = 0
    fields = ['scene_number', 'section', 'narration', 'has_character', 'image', 'video']
    readonly_fields = ['scene_number']


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ['name', 'user', 'status', 'image_style', 'character', 'voice', 'get_current_step', 'created_at']
    list_filter = ['status', 'user', 'image_style', 'character', 'voice']
    search_fields = ['name']
    readonly_fields = ['created_at', 'updated_at']
    inlines = [TopicInline, ResearchInline, DraftInline]
    fieldsets = (
        (None, {
            'fields': ('user', 'name', 'status'),
        }),
        ('프리셋 설정', {
            'fields': ('image_style', 'character', 'voice'),
            'description': '전역 설정에서 등록한 프리셋 중 선택',
        }),
        ('결과물', {
            'fields': ('final_video', 'thumbnail'),
            'classes': ('collapse',),
        }),
        ('시간', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )


@admin.register(Topic)
class TopicAdmin(admin.ModelAdmin):
    list_display = ['project', 'title', 'channel', 'view_count', 'viral_ratio', 'created_at']
    search_fields = ['title', 'channel']


@admin.register(Research)
class ResearchAdmin(admin.ModelAdmin):
    list_display = ['project', 'topic', 'created_at']
    search_fields = ['topic', 'summary']


@admin.register(Draft)
class DraftAdmin(admin.ModelAdmin):
    list_display = ['project', 'title', 'char_count', 'created_at']
    search_fields = ['title', 'content']


@admin.register(Scene)
class SceneAdmin(admin.ModelAdmin):
    list_display = ['project', 'scene_number', 'section', 'has_character', 'duration']
    list_filter = ['project', 'section', 'has_character']
    search_fields = ['narration']
    ordering = ['project', 'scene_number']


@admin.register(StepExecution)
class StepExecutionAdmin(admin.ModelAdmin):
    list_display = ['project', 'step', 'status', 'progress_percent', 'total_tokens', 'estimated_cost', 'started_at', 'completed_at']
    list_filter = ['status', 'step']
    search_fields = ['project__name']
    readonly_fields = ['started_at', 'completed_at', 'created_at']
