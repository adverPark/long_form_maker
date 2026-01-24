import json
import re
from .base import BaseStepService
from apps.pipeline.models import Scene


class ScenePlannerService(BaseStepService):
    """씬 분할 서비스 - 대본을 45-60개 씬으로 분할"""

    agent_name = 'scene_planner'

    def execute(self):
        self.update_progress(10, '대본 로딩 중...')

        # 입력 확인 (Draft 모델 또는 수동 입력)
        manual_input = self.get_manual_input()

        title = ''
        content = ''

        if manual_input:
            content = manual_input
            title = '사용자 입력 대본'
        elif hasattr(self.project, 'draft'):
            draft = self.project.draft
            title = draft.title
            content = draft.content

        if not content:
            raise ValueError('대본이 없습니다. 대본 작성을 먼저 완료하거나 직접 입력해주세요.')

        self.update_progress(20, '씬 분할 중...')

        # 프롬프트 가져오기
        prompt_template = self.get_prompt()

        if prompt_template:
            prompt = f"{prompt_template}\n\n## 대본:\n제목: {title}\n\n{content}"
        else:
            prompt = f"""대본을 45-60개의 씬으로 분할해주세요.

## 대본:
제목: {title}

{content}

## 분할 원칙
1. 각 씬은 10-20초 분량 (한글 40-80자)
2. 자연스러운 문장 단위로 분할
3. 캐릭터 등장 씬은 전체의 30% 이상
4. 섹션: intro, body_1, body_2, body_3, action, outro

## 출력 형식 (JSON)
```json
{{
  "scenes": [
    {{
      "scene_id": 1,
      "section": "intro",
      "narration": "자막에 표시될 내용",
      "narration_tts": "TTS로 읽을 내용 (숫자는 한글로)",
      "duration": 12,
      "has_character": true
    }}
  ]
}}
```

숫자 변환 예시:
- "470%" → "사백칠십퍼센트"
- "2024년" → "이천이십사년"
- "100억" → "백억" """

        # Gemini 호출
        self.update_progress(40, 'AI 씬 분할 중...')
        response = self.call_gemini(prompt)

        self.update_progress(70, '결과 파싱 중...')

        scenes_data = self._parse_response(response)

        if len(scenes_data) < 40:
            self.update_progress(75, f'씬 수 부족 ({len(scenes_data)}개), 재분할 중...')
            scenes_data = self._refine_scenes(scenes_data, content)

        # 기존 씬 삭제 후 새로 저장
        self.update_progress(85, 'DB에 저장 중...')
        self.project.scenes.all().delete()

        for scene_data in scenes_data:
            Scene.objects.create(
                project=self.project,
                scene_number=scene_data.get('scene_id', 0),
                section=scene_data.get('section', 'body_1'),
                narration=scene_data.get('narration', ''),
                narration_tts=scene_data.get('narration_tts', ''),
                duration=scene_data.get('duration', 10),
                has_character=scene_data.get('has_character', False),
                image_prompt='',  # image_prompter가 나중에 채움
            )

        self.update_progress(100, f'완료: {len(scenes_data)}개 씬')

    def _parse_response(self, response: str) -> list:
        """응답 파싱"""
        # JSON 블록 추출
        json_match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(1))
                return data.get('scenes', data) if isinstance(data, dict) else data
            except json.JSONDecodeError:
                pass

        # 직접 JSON 파싱
        try:
            data = json.loads(response)
            return data.get('scenes', data) if isinstance(data, dict) else data
        except json.JSONDecodeError:
            pass

        return []

    def _refine_scenes(self, scenes_data: list, original_content: str) -> list:
        """씬 수가 부족할 때 재분할"""
        refine_prompt = f"""현재 {len(scenes_data)}개의 씬이 있습니다.
45-60개가 되도록 더 세밀하게 분할해주세요.

원본 대본:
{original_content[:4000]}...

동일한 JSON 형식으로 출력해주세요:
```json
{{
  "scenes": [...]
}}
```"""

        response = self.call_gemini(refine_prompt)
        return self._parse_response(response)
