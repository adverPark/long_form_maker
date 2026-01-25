import os
import time
import base64
import replicate
import requests
from pathlib import Path
from django.conf import settings
from django.core.files.base import ContentFile
from .base import BaseStepService


class VideoGeneratorService(BaseStepService):
    """앞 4개 씬 동영상 생성 서비스 (Replicate seedance-1-pro)

    인트로 씬(1~4번)을 이미지에서 동영상으로 변환
    - 모델: bytedance/seedance-1-pro (Replicate)
    - 입력: 씬 이미지 + image_prompt
    - 출력: 5초 480p 동영상
    """

    agent_name = 'video_generator'
    MODEL_ID = "bytedance/seedance-1-pro"

    # 필수 프롬프트 (한글 보존 + 입 움직임 금지)
    REQUIRED_PROMPT_SUFFIX = """
Bring this image to life with subtle, natural motion.
CRITICAL: Preserve all text and Korean characters exactly as shown.
Character should NOT move lips or appear to be talking.
Only subtle natural movements like blinking, slight head movement, or breathing."""

    def execute(self):
        self.update_progress(5, '씬 로딩 중...')
        self.log('인트로 영상 생성 시작')

        # DB에서 앞 4개 씬 가져오기
        scenes = list(self.project.scenes.filter(scene_number__lte=4).order_by('scene_number'))

        if not scenes:
            raise ValueError('씬이 없습니다. 씬 분할을 먼저 완료해주세요.')

        total = len(scenes)
        self.log(f'앞 {total}개 씬 동영상 생성 예정')

        # Replicate API 키 확인 (DB에서 가져오기)
        from apps.accounts.models import APIKey
        api_key_obj = APIKey.objects.filter(
            user=self.project.user,
            service='replicate',
            is_default=True
        ).first()

        if not api_key_obj:
            raise ValueError('Replicate API 키가 설정되지 않았습니다. 설정에서 추가해주세요.')

        replicate_key = api_key_obj.get_key()

        success_count = 0
        skip_count = 0

        for i, scene in enumerate(scenes):
            scene_num = scene.scene_number

            # 이미 영상 있으면 스킵
            if scene.video:
                self.log(f'씬 {scene_num} 건너뜀 - 영상 존재')
                skip_count += 1
                progress = 5 + int(((i + 1) / total) * 90)
                self.update_progress(progress, f'씬 {scene_num}/{total} 건너뜀')
                continue

            # 이미지 없으면 스킵
            if not scene.image:
                self.log(f'씬 {scene_num} 건너뜀 - 이미지 없음', 'warning')
                skip_count += 1
                continue

            progress = 5 + int((i / total) * 90)
            self.update_progress(progress, f'씬 {scene_num}/{total} 동영상 생성 중...')
            self.log(f'씬 {scene_num} 동영상 생성 시작')

            try:
                # 프롬프트: 이미지 프롬프트 기반 + 필수 문구
                base_prompt = scene.image_prompt[:300] if scene.image_prompt else "Subtle camera movement"
                prompt = f"{base_prompt}\n{self.REQUIRED_PROMPT_SUFFIX}"

                video_data = self._generate_video(scene.image.path, prompt, replicate_key)

                if video_data:
                    # DB에 저장
                    filename = f'scene_{scene_num:02d}.mp4'
                    scene.video.save(filename, ContentFile(video_data), save=True)
                    self.log(f'씬 {scene_num} 동영상 저장 완료')
                    success_count += 1
                else:
                    self.log(f'씬 {scene_num} 동영상 생성 실패', 'error')

            except Exception as e:
                self.log(f'씬 {scene_num} 오류: {str(e)[:100]}', 'error')

        self.log(f'인트로 영상 생성 완료', 'result', {
            'total': total,
            'success': success_count,
            'skipped': skip_count,
        })
        self.update_progress(100, f'완료: {success_count}개 생성, {skip_count}개 스킵')

    def _image_to_data_uri(self, image_path: str) -> str:
        """이미지를 data URI로 변환"""
        ext = Path(image_path).suffix.lower()
        mime_type = 'image/png' if ext == '.png' else 'image/jpeg'

        with open(image_path, 'rb') as f:
            image_base64 = base64.b64encode(f.read()).decode('utf-8')

        return f'data:{mime_type};base64,{image_base64}'

    def _generate_video(self, image_path: str, prompt: str, api_key: str) -> bytes:
        """Replicate seedance-1-pro로 동영상 생성

        Args:
            image_path: 입력 이미지 경로
            prompt: 동영상 프롬프트
            api_key: Replicate API 토큰

        Returns:
            bytes: 동영상 데이터 또는 None
        """
        # Replicate API 키 설정
        os.environ['REPLICATE_API_TOKEN'] = api_key

        # 입력 파라미터 (원본 스크립트와 동일)
        input_params = {
            'prompt': prompt,
            'duration': 5,
            'aspect_ratio': '16:9',
            'resolution': '480p',
            'seed': -1,  # 랜덤
            'image': self._image_to_data_uri(image_path),
        }

        self.log(f'Replicate API 호출 중... (모델: {self.MODEL_ID})')

        try:
            # replicate 라이브러리 사용 (동기 호출)
            output = replicate.run(self.MODEL_ID, input=input_params)

            # output은 URL 또는 URL 리스트
            video_url = output
            if isinstance(output, list):
                video_url = output[0]

            if video_url:
                self.log(f'동영상 다운로드 중...')
                video_response = requests.get(video_url, timeout=300)
                if video_response.status_code == 200:
                    return video_response.content
                else:
                    self.log(f'다운로드 실패: {video_response.status_code}', 'error')
                    return None
            else:
                self.log('출력 URL 없음', 'error')
                return None

        except replicate.exceptions.ReplicateError as e:
            self.log(f'Replicate 에러: {str(e)[:200]}', 'error')
            return None
        except Exception as e:
            self.log(f'예외 발생: {str(e)[:200]}', 'error')
            return None
