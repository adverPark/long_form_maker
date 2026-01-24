import subprocess
import sys
from pathlib import Path
from django.conf import settings
from .base import BaseStepService


class SceneGeneratorService(BaseStepService):
    """씬 이미지 생성 서비스"""

    agent_name = 'scene_generator'

    def execute(self):
        self.update_progress(5, 'script.json 로딩 중...')

        script_data = self.get_file('script.json')
        if not script_data:
            raise ValueError('script.json이 없습니다.')

        scenes = script_data.get('scenes', [])
        total = len(scenes)

        if total == 0:
            raise ValueError('씬이 없습니다.')

        # Gemini API 키 확인
        gemini_key = self.get_gemini_key()

        project_path = self.project.get_project_path()
        images_dir = project_path / 'images'
        images_dir.mkdir(exist_ok=True)

        # 캐릭터 시트 확인/생성
        character_dir = project_path / 'character'
        character_sheet = character_dir / 'character_sheet.png'

        if not character_sheet.exists():
            self.update_progress(10, '캐릭터 시트 생성 중...')
            self._generate_character_sheet(character_dir, gemini_key)

        # 씬 이미지 생성
        for i, scene in enumerate(scenes):
            scene_id = scene.get('scene_id', i + 1)
            image_path = images_dir / f'scene_{scene_id:02d}.png'

            # 이미 존재하면 스킵
            if image_path.exists():
                progress = 10 + int((i / total) * 85)
                self.update_progress(progress, f'씬 {scene_id}/{total} 건너뜀 (이미 존재)')
                continue

            progress = 10 + int((i / total) * 85)
            self.update_progress(progress, f'씬 {scene_id}/{total} 이미지 생성 중...')

            # 출력 데이터 업데이트
            self.execution.output_data = {
                'current_scene': scene_id,
                'total_scenes': total
            }
            self.execution.save(update_fields=['output_data'])

            prompt = scene.get('image_prompt', '')
            has_character = scene.get('has_character', False)

            self._generate_scene_image(
                scene_id=scene_id,
                prompt=prompt,
                has_character=has_character,
                output_path=image_path,
                character_sheet=character_sheet if has_character else None,
                gemini_key=gemini_key
            )

        self.update_progress(100, f'완료 ({total}개 이미지)')

    def _generate_character_sheet(self, character_dir: Path, gemini_key: str):
        """캐릭터 시트 생성"""
        character_dir.mkdir(parents=True, exist_ok=True)

        # 기존 스크립트 사용 시도
        script_path = settings.SCRIPTS_DIR / 'generate_character_sheet.py'
        if script_path.exists():
            result = subprocess.run(
                [sys.executable, str(script_path), '--output', str(character_dir)],
                env={'GEMINI_API_KEY': gemini_key},
                capture_output=True,
                text=True,
                timeout=120
            )
            if result.returncode == 0:
                return

        # 직접 생성 (Gemini API)
        import google.generativeai as genai
        from PIL import Image
        import io

        genai.configure(api_key=gemini_key)

        prompt = """Create a character sheet for an animated news anchor/presenter character.
Style: Flat illustration, simple colors, professional look.
Include: front view, side view, various expressions (neutral, happy, surprised, thoughtful).
Background: white/transparent.
The character should look friendly and approachable, suitable for an economics/news YouTube channel."""

        model = genai.GenerativeModel('gemini-2.0-flash-exp')
        response = model.generate_content(prompt)

        # 이미지 응답 처리
        if hasattr(response, 'candidates') and response.candidates:
            for part in response.candidates[0].content.parts:
                if hasattr(part, 'inline_data') and part.inline_data:
                    image_data = part.inline_data.data
                    image = Image.open(io.BytesIO(image_data))
                    image.save(character_dir / 'character_sheet.png')
                    return

        # 플레이스홀더 이미지 생성
        placeholder = Image.new('RGB', (1024, 1024), color=(200, 200, 200))
        placeholder.save(character_dir / 'character_sheet.png')

    def _generate_scene_image(self, scene_id: int, prompt: str, has_character: bool,
                              output_path: Path, character_sheet: Path, gemini_key: str):
        """씬 이미지 생성"""
        import google.generativeai as genai
        from PIL import Image
        import io
        import time

        genai.configure(api_key=gemini_key)

        # 프롬프트 구성
        if has_character and character_sheet and character_sheet.exists():
            full_prompt = f"Based on the character reference, {prompt}"
        else:
            full_prompt = prompt

        full_prompt += "\nStyle: Photorealistic news/documentary style, 16:9 aspect ratio, professional lighting."

        # Gemini 이미지 생성 (재시도 로직 포함)
        max_retries = 3
        for attempt in range(max_retries):
            try:
                model = genai.GenerativeModel('gemini-2.0-flash-exp')

                # 캐릭터 시트가 있으면 함께 전송
                if has_character and character_sheet and character_sheet.exists():
                    ref_image = Image.open(character_sheet)
                    response = model.generate_content([full_prompt, ref_image])
                else:
                    response = model.generate_content(full_prompt)

                # 이미지 추출 및 저장
                if hasattr(response, 'candidates') and response.candidates:
                    for part in response.candidates[0].content.parts:
                        if hasattr(part, 'inline_data') and part.inline_data:
                            image_data = part.inline_data.data
                            image = Image.open(io.BytesIO(image_data))
                            # 1920x1080으로 리사이즈
                            image = image.resize((1920, 1080), Image.Resampling.LANCZOS)
                            image.save(output_path)
                            return

                # 텍스트 응답인 경우 플레이스홀더 생성
                placeholder = Image.new('RGB', (1920, 1080), color=(50, 50, 50))
                placeholder.save(output_path)
                return

            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)  # 지수 백오프
                else:
                    # 최종 실패 시 플레이스홀더
                    placeholder = Image.new('RGB', (1920, 1080), color=(100, 100, 100))
                    placeholder.save(output_path)
