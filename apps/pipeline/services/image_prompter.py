import json
import re
from .base import BaseStepService


class ImagePrompterService(BaseStepService):
    """이미지 프롬프트 작성 서비스"""

    agent_name = 'image_prompter'

    def execute(self):
        self.update_progress(10, 'script.json 로딩 중...')

        script_data = self.get_file('script.json')
        if not script_data:
            raise ValueError('script.json이 없습니다. 씬 분할을 먼저 완료해주세요.')

        scenes = script_data.get('scenes', [])
        total = len(scenes)

        if total == 0:
            raise ValueError('씬이 없습니다.')

        self.update_progress(20, '프롬프트 준비 중...')

        prompt_template = self.get_prompt()
        if not prompt_template:
            prompt_template = self._get_default_prompt()

        # 배치로 처리 (10개씩)
        batch_size = 10
        for i in range(0, total, batch_size):
            batch = scenes[i:i + batch_size]
            batch_end = min(i + batch_size, total)

            progress = 20 + int((i / total) * 70)
            self.update_progress(progress, f'이미지 프롬프트 생성 중 ({i + 1}-{batch_end}/{total})...')

            batch_prompts = self._generate_batch_prompts(batch, prompt_template)

            for j, prompt in enumerate(batch_prompts):
                if i + j < len(scenes):
                    scenes[i + j]['image_prompt'] = prompt

        script_data['scenes'] = scenes

        self.update_progress(95, '저장 중...')
        self.save_output('script.json', script_data)

        self.update_progress(100, '완료')

    def _get_default_prompt(self):
        return """각 씬의 narration을 분석하여 뉴스/다큐멘터리 스타일의 이미지 프롬프트를 작성하세요.

## 원칙
1. 영어로 작성
2. 실사 사진/뉴스 그래픽 스타일
3. 대본 내용이 시각적으로 표현되도록
4. 캐릭터 씬: 캐릭터가 화자 역할, 배경이 대본 내용 표현

## 프롬프트 구조
- 캐릭터 있음: "Same character from reference sheet, [pose], in front of [background describing the narration content], news documentary style"
- 캐릭터 없음: "[Visual description of narration content], photorealistic, news infographic style"

## 출력: 프롬프트만 한 줄씩"""

    def _generate_batch_prompts(self, batch: list, prompt_template: str) -> list:
        """배치로 프롬프트 생성"""
        scenes_text = "\n".join([
            f"씬 {s['scene_id']}: {s['narration']} (캐릭터: {'있음' if s.get('has_character') else '없음'})"
            for s in batch
        ])

        full_prompt = f"{prompt_template}\n\n## 씬 목록:\n{scenes_text}\n\n각 씬에 대한 이미지 프롬프트를 한 줄씩 출력하세요:"

        response = self.call_gemini(full_prompt)

        # 줄 단위로 파싱
        lines = [line.strip() for line in response.strip().split('\n') if line.strip()]

        # 숫자 prefix 제거
        prompts = []
        for line in lines:
            # "1. ", "씬 1: " 등 제거
            cleaned = re.sub(r'^(씬\s*)?\d+[\.:]\s*', '', line)
            if cleaned:
                prompts.append(cleaned)

        # 부족하면 기본 프롬프트로 채우기
        while len(prompts) < len(batch):
            prompts.append("News studio background, professional lighting, documentary style")

        return prompts[:len(batch)]
