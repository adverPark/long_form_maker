from django.contrib import admin
from .models import PipelineStep, Project, Topic, Research, Draft, Scene, CharacterSheet, StepExecution


@admin.register(PipelineStep)
class PipelineStepAdmin(admin.ModelAdmin):
    list_display = ['name', 'display_name', 'order', 'can_run_independently']
    list_editable = ['order', 'can_run_independently']
    ordering = ['order']


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
    list_display = ['name', 'user', 'status', 'get_current_step', 'created_at']
    list_filter = ['status', 'user']
    search_fields = ['name']
    readonly_fields = ['created_at', 'updated_at']
    inlines = [TopicInline, ResearchInline, DraftInline]


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


@admin.register(CharacterSheet)
class CharacterSheetAdmin(admin.ModelAdmin):
    list_display = ['project', 'created_at']


@admin.register(StepExecution)
class StepExecutionAdmin(admin.ModelAdmin):
    list_display = ['project', 'step', 'status', 'progress_percent', 'started_at', 'completed_at']
    list_filter = ['status', 'step']
    search_fields = ['project__name']
    readonly_fields = ['started_at', 'completed_at', 'created_at']
