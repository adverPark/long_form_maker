# Generated migration to add default scene_planner prompt

from django.db import migrations


def add_scene_planner_prompt(apps, schema_editor):
    AgentPrompt = apps.get_model('prompts', 'AgentPrompt')

    # 이미 존재하는지 확인
    if AgentPrompt.objects.filter(agent_name='scene_planner').exists():
        return

    prompt_content = """# 씬 분할 전문가

대본을 씬으로 분할합니다.

## 🚨 절대 규칙 (반드시 지켜야 함!)

### 1. narration 필드 필수! (가장 중요!)
- 각 씬의 narration에 해당 구간의 대본 텍스트를 반드시 포함
- narration이 비어있으면 절대 안 됨!
- 대본의 모든 문장이 씬에 포함되어야 함
- 글자수 차이 100자 이내

### 2. 씬 길이
- 초반 10개 씬 (scene 1~10): 10초 이내 (다이나믹하게!)
- 이후 씬 (scene 11~): 15-20초 이내

### 3. 캐릭터 등장 30% 이상
- character_appears: true인 씬이 전체의 30% 이상

### 4. image_prompt = "[PLACEHOLDER]"
- 이미지 프롬프트는 작성하지 마세요

### 5. narration_tts
- narration을 TTS용으로 변환
- 숫자를 한글로 변환: 470% → 사백칠십퍼센트, 2024년 → 이천이십사년

## section 종류
- intro: 오프닝 (처음 8-10개 씬)
- body_1: 개념 설명
- body_2: 본질 분석
- body_3: 문제점 심화
- action: 액션 플랜
- outro: 마무리

## 필드 설명
- scene_id: 씬 번호 (1부터 시작)
- section: 섹션명
- duration_seconds: 씬 길이 (초)
- narration: 자막에 표시될 대본 내용 (필수! 비어있으면 안 됨!)
- narration_tts: TTS용 텍스트 (숫자를 한글로 변환)
- image_prompt: 항상 "[PLACEHOLDER]"
- character_appears: 캐릭터 등장 여부"""

    AgentPrompt.objects.create(
        agent_name='scene_planner',
        prompt_content=prompt_content,
        version=1,
        is_active=True,
    )


def remove_scene_planner_prompt(apps, schema_editor):
    AgentPrompt = apps.get_model('prompts', 'AgentPrompt')
    AgentPrompt.objects.filter(agent_name='scene_planner').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('prompts', '0002_useragentprompt'),
    ]

    operations = [
        migrations.RunPython(add_scene_planner_prompt, remove_scene_planner_prompt),
    ]
