from __future__ import annotations
import json
from pathlib import Path
from typing import TYPE_CHECKING
from .base import BaseStepService

if TYPE_CHECKING:
    from PIL import Image


class ThumbnailGeneratorService(BaseStepService):
    """썸네일 + 업로드 정보 생성 서비스"""

    agent_name = 'thumbnail_generator'

    def execute(self):
        self.update_progress(10, 'script.json 로딩 중...')

        script_data = self.get_file('script.json')
        if not script_data:
            raise ValueError('script.json이 없습니다.')

        title = script_data.get('title', '제목 없음')
        scenes = script_data.get('scenes', [])

        project_path = self.project.get_project_path()
        gemini_key = self.get_gemini_key()

        # 1. 썸네일 생성
        self.update_progress(30, '썸네일 생성 중...')
        self._generate_thumbnail(title, scenes, project_path, gemini_key)

        # 2. 업로드 정보 생성
        self.update_progress(70, '업로드 정보 생성 중...')
        self._generate_upload_info(title, scenes, project_path)

        self.update_progress(100, '완료')

    def _generate_thumbnail(self, title: str, scenes: list, project_path: Path, gemini_key: str):
        """썸네일 이미지 생성 (1280x720)"""
        import google.generativeai as genai
        from PIL import Image, ImageDraw, ImageFont
        import io

        thumbnail_path = project_path / 'thumbnail.png'

        # 캐릭터 시트 확인
        character_sheet = project_path / 'character' / 'character_sheet.png'

        # 제목에서 핵심 키워드 추출 (10자 이내)
        short_title = title[:10] if len(title) > 10 else title

        genai.configure(api_key=gemini_key)

        prompt = f"""Create a YouTube thumbnail for an economics video.
Title: {title}

Requirements:
- Size: 1280x720 (16:9 aspect ratio)
- Style: Professional news/documentary look
- Include a presenter character on the left side
- Dramatic lighting
- Background should relate to the video topic
- Space for text overlay on the right side
- Yellow accent colors for emphasis
- High contrast, eye-catching design"""

        try:
            model = genai.GenerativeModel('gemini-2.0-flash-exp')

            if character_sheet.exists():
                ref_image = Image.open(character_sheet)
                response = model.generate_content([prompt, ref_image])
            else:
                response = model.generate_content(prompt)

            # 이미지 추출
            if hasattr(response, 'candidates') and response.candidates:
                for part in response.candidates[0].content.parts:
                    if hasattr(part, 'inline_data') and part.inline_data:
                        image_data = part.inline_data.data
                        image = Image.open(io.BytesIO(image_data))
                        image = image.resize((1280, 720), Image.Resampling.LANCZOS)

                        # 텍스트 오버레이 추가
                        image = self._add_text_overlay(image, short_title)

                        image.save(thumbnail_path)
                        return

            # 실패 시 플레이스홀더
            self._create_placeholder_thumbnail(thumbnail_path, short_title)

        except Exception as e:
            print(f'썸네일 생성 에러: {e}')
            self._create_placeholder_thumbnail(thumbnail_path, short_title)

    def _add_text_overlay(self, image: Image.Image, text: str) -> Image.Image:
        """이미지에 텍스트 오버레이 추가"""
        from PIL import ImageDraw

        draw = ImageDraw.Draw(image)

        # 폰트 시도 (시스템 폰트)
        try:
            from PIL import ImageFont
            font = ImageFont.truetype('/System/Library/Fonts/AppleSDGothicNeo.ttc', 72)
        except:
            font = None

        # 텍스트 위치 (우측 하단)
        x = 700
        y = 550

        # 노란색 텍스트 + 검은색 외곽선
        outline_color = (0, 0, 0)
        text_color = (255, 255, 0)  # 노란색

        # 외곽선
        for dx in [-3, 0, 3]:
            for dy in [-3, 0, 3]:
                if dx != 0 or dy != 0:
                    draw.text((x + dx, y + dy), text, font=font, fill=outline_color)

        # 본문
        draw.text((x, y), text, font=font, fill=text_color)

        return image

    def _create_placeholder_thumbnail(self, path: Path, text: str):
        """플레이스홀더 썸네일 생성"""
        from PIL import Image, ImageDraw

        image = Image.new('RGB', (1280, 720), color=(30, 30, 30))
        draw = ImageDraw.Draw(image)

        # 간단한 텍스트
        draw.text((640, 360), text, fill=(255, 255, 0), anchor='mm')

        image.save(path)

    def _generate_upload_info(self, title: str, scenes: list, project_path: Path):
        """upload_info.txt 생성"""
        output_path = project_path / 'upload_info.txt'

        # 타임라인 생성
        timeline = []
        total_time = 0

        for scene in scenes:
            section = scene.get('section', '')
            duration = scene.get('duration', 10)

            # 섹션이 바뀔 때만 타임라인 추가
            if section and (not timeline or timeline[-1][1] != section):
                time_str = self._format_time(total_time)
                section_name = self._get_section_name(section)
                timeline.append((time_str, section, section_name))

            total_time += duration

        # 내용 작성
        content = f"""제목:
{title}

설명:
{title}

경제 이슈를 쉽고 재미있게 풀어드립니다.

타임라인:
"""
        for time_str, section, section_name in timeline:
            content += f"{time_str} {section_name}\n"

        content += """
태그:
경제, 경제뉴스, 경제이슈, 주식, 투자, 재테크, 부동산, 금융, 돈, 자산관리

#경제 #투자 #재테크 #주식 #부동산
"""

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(content)

    def _format_time(self, seconds: float) -> str:
        """초를 MM:SS 형식으로 변환"""
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes:02d}:{secs:02d}"

    def _get_section_name(self, section: str) -> str:
        """섹션 코드를 한글 이름으로 변환"""
        names = {
            'intro': '인트로',
            'body_1': '본론 1',
            'body_2': '본론 2',
            'body_3': '본론 3',
            'action': '시청자 행동 유도',
            'outro': '마무리',
        }
        return names.get(section, section)
