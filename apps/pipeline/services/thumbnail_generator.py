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
        from google import genai
        from google.genai import types
        from apps.pipeline.models import UploadInfo
        from PIL import Image, ImageDraw, ImageFont
        import io

        thumbnail_path = project_path / 'thumbnail.png'

        # 캐릭터 시트 확인
        character_sheet = project_path / 'character' / 'character_sheet.png'

        # 제목에서 핵심 키워드 추출 (10자 이내)
        short_title = title[:10] if len(title) > 10 else title

        # 신 SDK 클라이언트 생성
        client = genai.Client(api_key=gemini_key)

        # UploadInfo의 LLM 생성 썸네일 프롬프트 사용
        custom_prompt = None
        try:
            upload_info = UploadInfo.objects.filter(project=self.project).first()
            if upload_info and upload_info.thumbnail_prompt:
                custom_prompt = upload_info.thumbnail_prompt.strip()
                self.log(f'UploadInfo 썸네일 프롬프트 사용 ({len(custom_prompt)}자)')
        except Exception:
            pass

        if custom_prompt:
            prompt = f"""{custom_prompt}

Additional requirements:
- Size: 1280x720 (16:9 aspect ratio)
- High contrast, eye-catching YouTube thumbnail design"""
        else:
            prompt = f"""Create a YouTube thumbnail for a video.
Title: {title}

Requirements:
- Size: 1280x720 (16:9 aspect ratio)
- Style: Professional, eye-catching design
- Background should relate to the video topic
- High contrast, dramatic lighting"""
            self.log('UploadInfo 썸네일 프롬프트 없음 - 기본 프롬프트 사용')

        try:
            self.log(f'Gemini 이미지 생성 중... (키: {gemini_key[:10]}...)')

            contents = [prompt]

            # 썸네일 스타일 예시 이미지 추가
            thumbnail_style = self.project.thumbnail_style
            if thumbnail_style and thumbnail_style.example_image:
                try:
                    style_img = Image.open(thumbnail_style.example_image.path)
                    img_buf = io.BytesIO()
                    style_img.save(img_buf, format='PNG')
                    contents.insert(0, types.Part.from_bytes(data=img_buf.getvalue(), mime_type='image/png'))
                    contents[1] = f"Create a thumbnail in the same style as the reference image.\n\n{contents[1]}"
                    self.log(f'썸네일 스타일 예시 이미지 사용: {thumbnail_style.name}')
                except Exception as e:
                    self.log(f'썸네일 스타일 이미지 로드 실패: {e}', 'warning')

            # 캐릭터 시트 추가
            if character_sheet.exists():
                try:
                    char_img = Image.open(character_sheet)
                    img_buf = io.BytesIO()
                    char_img.save(img_buf, format='PNG')
                    contents.append(types.Part.from_bytes(data=img_buf.getvalue(), mime_type='image/png'))
                    self.log('캐릭터 시트 참조 사용')
                except Exception as e:
                    self.log(f'캐릭터 시트 로드 실패: {e}', 'warning')

            response = client.models.generate_content(
                model='gemini-3-pro-image-preview',
                contents=contents,
                config=types.GenerateContentConfig(
                    response_modalities=['IMAGE', 'TEXT']
                )
            )

            # 이미지 추출 (신 SDK)
            if response.candidates:
                for part in response.candidates[0].content.parts:
                    if part.inline_data and part.inline_data.data:
                        image_data = part.inline_data.data
                        image = Image.open(io.BytesIO(image_data))
                        image = image.resize((1280, 720), Image.Resampling.LANCZOS)

                        # 텍스트 오버레이 추가
                        image = self._add_text_overlay(image, short_title)

                        image.save(thumbnail_path)
                        self.log(f'썸네일 저장 완료: {thumbnail_path}')
                        return

            # 이미지 없음
            self.log('Gemini 응답에 이미지 없음', 'warning')
            self._create_placeholder_thumbnail(thumbnail_path, short_title)

        except Exception as e:
            self.log(f'썸네일 생성 에러: {type(e).__name__}: {str(e)[:200]}', 'error')
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

타임라인:
"""
        for time_str, section, section_name in timeline:
            content += f"{time_str} {section_name}\n"

        content += "\n"

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
