import subprocess
import json
import os
import sys
from pathlib import Path
from django.conf import settings
from .base import BaseStepService


class VideoComposerService(BaseStepService):
    """영상 편집 서비스 (TTS + 자막 + 합성)"""

    agent_name = 'video_composer'

    def execute(self):
        self.update_progress(5, 'script.json 로딩 중...')

        script_data = self.get_file('script.json')
        if not script_data:
            raise ValueError('script.json이 없습니다.')

        scenes = script_data.get('scenes', [])
        total = len(scenes)

        if total == 0:
            raise ValueError('씬이 없습니다.')

        project_path = self.project.get_project_path()

        # 디렉토리 생성
        (project_path / 'audio' / 'scenes').mkdir(parents=True, exist_ok=True)
        (project_path / 'subtitles' / 'scenes').mkdir(parents=True, exist_ok=True)
        (project_path / 'clips').mkdir(parents=True, exist_ok=True)

        # 1. TTS 생성
        self.update_progress(10, 'TTS 음성 생성 중...')
        self._generate_tts(scenes, project_path)

        # 2. 자막 생성
        self.update_progress(40, '자막 생성 중...')
        self._generate_subtitles(scenes, project_path)

        # 3. 클립 생성
        self.update_progress(60, '씬 클립 생성 중...')
        self._generate_clips(scenes, project_path)

        # 4. 최종 합치기
        self.update_progress(90, '최종 영상 합성 중...')
        self._concat_clips(scenes, project_path)

        # 5. 전체 자막 병합
        self.update_progress(95, '자막 파일 생성 중...')
        self._merge_subtitles(scenes, project_path)

        self.update_progress(100, '완료')

    def _generate_tts(self, scenes: list, project_path: Path):
        """Fish Speech로 TTS 생성"""
        import requests

        audio_dir = project_path / 'audio' / 'scenes'
        total = len(scenes)

        for i, scene in enumerate(scenes):
            scene_id = scene.get('scene_id', i + 1)
            audio_path = audio_dir / f'scene_{scene_id:02d}.wav'

            if audio_path.exists():
                continue

            text = scene.get('narration_tts', scene.get('narration', ''))
            if not text:
                continue

            progress = 10 + int((i / total) * 30)
            self.update_progress(progress, f'TTS 생성 중 ({scene_id}/{total})...')

            try:
                response = requests.post(
                    f'{settings.FISH_SPEECH_URL}/v1/tts',
                    json={
                        'text': text,
                        'temperature': 0.7,
                        'top_p': 0.7,
                        'seed': 42
                    },
                    timeout=60
                )

                if response.status_code == 200:
                    with open(audio_path, 'wb') as f:
                        f.write(response.content)
            except Exception as e:
                print(f'TTS 에러 (씬 {scene_id}): {e}')

    def _generate_subtitles(self, scenes: list, project_path: Path):
        """자막 파일 생성 (SRT)"""
        audio_dir = project_path / 'audio' / 'scenes'
        subtitle_dir = project_path / 'subtitles' / 'scenes'

        for i, scene in enumerate(scenes):
            scene_id = scene.get('scene_id', i + 1)
            audio_path = audio_dir / f'scene_{scene_id:02d}.wav'
            srt_path = subtitle_dir / f'scene_{scene_id:02d}.srt'

            if srt_path.exists():
                continue

            narration = scene.get('narration', '')
            duration = scene.get('duration', 10)

            # 간단한 SRT 생성 (전체 시간에 걸쳐 표시)
            srt_content = f"""1
00:00:00,000 --> 00:00:{duration:02d},000
{narration}
"""
            with open(srt_path, 'w', encoding='utf-8') as f:
                f.write(srt_content)

    def _generate_clips(self, scenes: list, project_path: Path):
        """씬별 클립 생성 (이미지/영상 + 오디오 + 자막)"""
        images_dir = project_path / 'images'
        videos_dir = project_path / 'videos'
        audio_dir = project_path / 'audio' / 'scenes'
        subtitle_dir = project_path / 'subtitles' / 'scenes'
        clips_dir = project_path / 'clips'

        total = len(scenes)

        for i, scene in enumerate(scenes):
            scene_id = scene.get('scene_id', i + 1)
            clip_path = clips_dir / f'scene_{scene_id:02d}.mp4'

            if clip_path.exists():
                continue

            progress = 60 + int((i / total) * 30)
            self.update_progress(progress, f'클립 생성 중 ({scene_id}/{total})...')

            # 입력 파일 결정
            video_input = videos_dir / f'scene_{scene_id:02d}.mp4'
            image_input = images_dir / f'scene_{scene_id:02d}.png'
            audio_path = audio_dir / f'scene_{scene_id:02d}.wav'

            if video_input.exists():
                input_file = str(video_input)
                input_opts = ['-i', input_file]
            elif image_input.exists():
                input_file = str(image_input)
                duration = scene.get('duration', 10)
                input_opts = ['-loop', '1', '-i', input_file, '-t', str(duration)]
            else:
                continue

            # FFmpeg 명령어 구성
            cmd = ['ffmpeg', '-y']
            cmd.extend(input_opts)

            if audio_path.exists():
                cmd.extend(['-i', str(audio_path)])

            # 출력 설정
            cmd.extend([
                '-vf', 'scale=1920:1080,format=yuv420p',
                '-r', '30',
                '-c:v', 'libx264',
                '-preset', 'fast',
                '-crf', '23'
            ])

            if audio_path.exists():
                cmd.extend(['-c:a', 'aac', '-b:a', '192k', '-shortest'])
            else:
                cmd.extend(['-an'])

            cmd.append(str(clip_path))

            try:
                subprocess.run(cmd, capture_output=True, timeout=120)
            except Exception as e:
                print(f'클립 생성 에러 (씬 {scene_id}): {e}')

    def _concat_clips(self, scenes: list, project_path: Path):
        """클립들을 하나로 합치기"""
        clips_dir = project_path / 'clips'
        output_path = project_path / 'final.mp4'

        # concat 리스트 파일 생성
        list_file = project_path / 'concat_list.txt'
        with open(list_file, 'w') as f:
            for i, scene in enumerate(scenes):
                scene_id = scene.get('scene_id', i + 1)
                clip_path = clips_dir / f'scene_{scene_id:02d}.mp4'
                if clip_path.exists():
                    f.write(f"file '{clip_path}'\n")

        # FFmpeg concat
        cmd = [
            'ffmpeg', '-y',
            '-f', 'concat',
            '-safe', '0',
            '-i', str(list_file),
            '-c:v', 'libx264',
            '-c:a', 'aac',
            str(output_path)
        ]

        subprocess.run(cmd, capture_output=True, timeout=600)

    def _merge_subtitles(self, scenes: list, project_path: Path):
        """전체 자막 SRT 파일 생성"""
        output_path = project_path / 'full_subtitles.srt'

        total_time = 0
        srt_index = 1
        srt_content = []

        for i, scene in enumerate(scenes):
            narration = scene.get('narration', '')
            duration = scene.get('duration', 10)

            if not narration:
                total_time += duration
                continue

            start_time = self._format_srt_time(total_time)
            end_time = self._format_srt_time(total_time + duration)

            srt_content.append(f"{srt_index}")
            srt_content.append(f"{start_time} --> {end_time}")
            srt_content.append(narration)
            srt_content.append("")

            srt_index += 1
            total_time += duration

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(srt_content))

    def _format_srt_time(self, seconds: float) -> str:
        """초를 SRT 시간 형식으로 변환"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
