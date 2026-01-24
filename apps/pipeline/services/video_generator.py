import os
import time
import requests
from pathlib import Path
from .base import BaseStepService


class VideoGeneratorService(BaseStepService):
    """앞 4개 씬 동영상 생성 서비스 (Replicate seedance-1-pro)"""

    agent_name = 'video_generator'

    def execute(self):
        self.update_progress(5, 'script.json 로딩 중...')

        script_data = self.get_file('script.json')
        if not script_data:
            raise ValueError('script.json이 없습니다.')

        scenes = script_data.get('scenes', [])[:4]  # 앞 4개만

        if len(scenes) == 0:
            raise ValueError('씬이 없습니다.')

        # Replicate API 키 확인
        replicate_key = self.get_replicate_key()

        project_path = self.project.get_project_path()
        images_dir = project_path / 'images'
        videos_dir = project_path / 'videos'
        videos_dir.mkdir(exist_ok=True)

        total = len(scenes)

        for i, scene in enumerate(scenes):
            scene_id = scene.get('scene_id', i + 1)
            image_path = images_dir / f'scene_{scene_id:02d}.png'
            video_path = videos_dir / f'scene_{scene_id:02d}.mp4'

            # 이미 존재하면 스킵
            if video_path.exists():
                progress = 5 + int((i / total) * 90)
                self.update_progress(progress, f'씬 {scene_id}/{total} 건너뜀 (이미 존재)')
                continue

            if not image_path.exists():
                progress = 5 + int((i / total) * 90)
                self.update_progress(progress, f'씬 {scene_id}/{total} 건너뜀 (이미지 없음)')
                continue

            progress = 5 + int((i / total) * 90)
            self.update_progress(progress, f'씬 {scene_id}/{total} 동영상 생성 중...')

            self.execution.output_data = {
                'current_scene': scene_id,
                'total_scenes': total
            }
            self.execution.save(update_fields=['output_data'])

            prompt = scene.get('image_prompt', 'Subtle camera movement, cinematic')
            self._generate_video(image_path, video_path, prompt, replicate_key)

        self.update_progress(100, f'완료 ({total}개 동영상)')

    def _generate_video(self, image_path: Path, output_path: Path, prompt: str, api_key: str):
        """Replicate seedance-1-pro로 동영상 생성"""
        import base64

        # 이미지를 base64로 인코딩
        with open(image_path, 'rb') as f:
            image_base64 = base64.b64encode(f.read()).decode('utf-8')

        # Replicate API 호출
        headers = {
            'Authorization': f'Token {api_key}',
            'Content-Type': 'application/json'
        }

        # 예측 생성
        response = requests.post(
            'https://api.replicate.com/v1/predictions',
            headers=headers,
            json={
                'version': 'seedance-1-pro',  # 실제 버전 ID로 교체 필요
                'input': {
                    'image': f'data:image/png;base64,{image_base64}',
                    'prompt': prompt,
                    'duration': 5,
                    'fps': 30
                }
            },
            timeout=30
        )

        if response.status_code != 201:
            raise Exception(f'Replicate API 에러: {response.text}')

        prediction = response.json()
        prediction_id = prediction['id']

        # 완료될 때까지 대기
        max_wait = 300  # 5분
        start_time = time.time()

        while time.time() - start_time < max_wait:
            status_response = requests.get(
                f'https://api.replicate.com/v1/predictions/{prediction_id}',
                headers=headers,
                timeout=30
            )
            status = status_response.json()

            if status['status'] == 'succeeded':
                video_url = status['output']
                if isinstance(video_url, list):
                    video_url = video_url[0]

                # 비디오 다운로드
                video_response = requests.get(video_url, timeout=60)
                with open(output_path, 'wb') as f:
                    f.write(video_response.content)
                return

            elif status['status'] == 'failed':
                raise Exception(f'동영상 생성 실패: {status.get("error")}')

            time.sleep(5)

        raise Exception('동영상 생성 시간 초과')
