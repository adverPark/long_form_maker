from django import forms
from django.contrib import admin
from django.utils.html import format_html
from .models import AgentPrompt, AgentPromptHistory


class AgentPromptAdminForm(forms.ModelForm):
    """프롬프트 편집을 위한 커스텀 폼"""
    class Meta:
        model = AgentPrompt
        fields = '__all__'
        widgets = {
            'prompt_content': forms.Textarea(attrs={
                'rows': 40,
                'style': 'width: 100%; font-family: monospace; font-size: 13px; line-height: 1.5;'
            }),
        }


@admin.register(AgentPrompt)
class AgentPromptAdmin(admin.ModelAdmin):
    form = AgentPromptAdminForm
    list_display = ['agent_name', 'version', 'is_active', 'char_count', 'updated_at', 'updated_by']
    list_filter = ['agent_name', 'is_active']
    search_fields = ['agent_name', 'prompt_content']
    readonly_fields = ['created_at', 'updated_at', 'char_count_display']

    fieldsets = [
        ('기본 정보', {
            'fields': ['agent_name', 'is_active', 'version']
        }),
        ('프롬프트', {
            'fields': ['prompt_content', 'char_count_display'],
            'classes': ['wide'],
            'description': 'Markdown 형식으로 작성하세요. 저장하면 버전이 자동으로 올라갑니다.'
        }),
        ('메타 정보', {
            'fields': ['updated_by', 'created_at', 'updated_at'],
            'classes': ['collapse']
        }),
    ]

    def char_count(self, obj):
        """글자수 표시"""
        count = len(obj.prompt_content) if obj.prompt_content else 0
        return f'{count:,}자'
    char_count.short_description = '글자수'

    def char_count_display(self, obj):
        """글자수 표시 (상세 페이지용)"""
        count = len(obj.prompt_content) if obj.prompt_content else 0
        return format_html('<strong style="font-size: 14px;">{:,}자</strong>', count)
    char_count_display.short_description = '프롬프트 글자수'

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
