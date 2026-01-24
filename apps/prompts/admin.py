from django.contrib import admin
from .models import AgentPrompt, AgentPromptHistory


@admin.register(AgentPrompt)
class AgentPromptAdmin(admin.ModelAdmin):
    list_display = ['agent_name', 'version', 'is_active', 'updated_at', 'updated_by']
    list_filter = ['agent_name', 'is_active']
    search_fields = ['agent_name', 'prompt_content']
    readonly_fields = ['created_at', 'updated_at']

    fieldsets = [
        ('기본 정보', {
            'fields': ['agent_name', 'is_active', 'version']
        }),
        ('프롬프트', {
            'fields': ['prompt_content'],
            'classes': ['wide']
        }),
        ('메타 정보', {
            'fields': ['updated_by', 'created_at', 'updated_at'],
            'classes': ['collapse']
        }),
    ]

    def save_model(self, request, obj, form, change):
        # 변경 시 히스토리 저장
        if change and 'prompt_content' in form.changed_data:
            old_obj = AgentPrompt.objects.get(pk=obj.pk)
            AgentPromptHistory.objects.create(
                prompt=obj,
                previous_content=old_obj.prompt_content,
                previous_version=old_obj.version,
                changed_by=request.user
            )
            obj.version += 1

        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(AgentPromptHistory)
class AgentPromptHistoryAdmin(admin.ModelAdmin):
    list_display = ['prompt', 'previous_version', 'changed_by', 'changed_at']
    list_filter = ['prompt__agent_name']
    readonly_fields = ['prompt', 'previous_content', 'previous_version', 'changed_by', 'changed_at']
