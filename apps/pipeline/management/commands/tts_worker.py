"""
TTS 워커 - DB 기반 작업 큐 처리

사용법:
    python manage.py tts_worker

기능:
    - DB에서 pending 작업 가져와서 순차 처리
    - 작업 취소 시 다음 루프에서 스킵
    - stuck 작업 자동 복구
    - Ctrl+C로 안전 종료
"""

import time
import signal
import sys
import re
import io
import base64
import zipfile
import requests
import wave

from django.core.management.base import BaseCommand
from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone

from apps.pipeline.models import TTSJob


class Command(BaseCommand):
    help = 'TTS 작업 워커 실행'

    def __init__(self):
        super().__init__()
        self.running = True
        self.current_job = None

    def add_arguments(self, parser):
        parser.add_argument(
            '--interval',
            type=int,
            default=2,
            help='폴링 간격 (초, 기본값: 2)'
        )
        parser.add_argument(
            '--timeout',
            type=int,
            default=180,
            help='TTS 요청 타임아웃 (초, 기본값: 180)'
        )

    def handle(self, *args, **options):
        interval = options['interval']
        timeout = options['timeout']

        # 시그널 핸들러 등록 (안전 종료)
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

        self.stdout.write(self.style.SUCCESS('TTS 워커 시작'))
        self.stdout.write(f'  - 폴링 간격: {interval}초')
        self.stdout.write(f'  - 요청 타임아웃: {timeout}초')
        self.stdout.write(f'  - Fish Speech: {settings.FISH_SPEECH_URL}')
        self.stdout.write('Ctrl+C로 종료\n')

        while self.running:
            try:
                # stuck 작업 복구 (10분 이상 processing)
                recovered = TTSJob.recover_stuck_jobs(timeout_minutes=10)
                if recovered:
                    self.stdout.write(f'[복구] {recovered}개 stuck 작업 복구됨')

                # 다음 작업 가져오기
                job = TTSJob.get_next_job()

                if job:
                    self.current_job = job
                    self.process_job(job, timeout)
                    self.current_job = None
                else:
                    # 작업 없으면 대기
                    time.sleep(interval)

            except Exception as e:
                self.stdout.write(self.style.ERROR(f'워커 오류: {e}'))
                time.sleep(interval)

        self.stdout.write(self.style.SUCCESS('\nTTS 워커 종료'))

    def signal_handler(self, signum, frame):
        """안전 종료 핸들러"""
        self.stdout.write('\n종료 신호 수신, 현재 작업 완료 후 종료합니다...')
        self.running = False

    def process_job(self, job: TTSJob, timeout: int):
        """단일 TTS 작업 처리"""
        scene = job.scene
        project = job.project

        self.stdout.write(
            f'[처리] 프로젝트 {project.id} 씬 {scene.scene_number} 시작'
        )

        try:
            # 취소 확인
            job.refresh_from_db()
            if job.status == 'cancelled':
                self.stdout.write(f'  -> 취소됨, 스킵')
                return

            # TTS 텍스트 준비
            tts_text = scene.narration_tts or scene.narration
            original_narration = scene.narration

            if not tts_text:
                job.mark_failed('나레이션 없음')
                self.stdout.write(self.style.WARNING(f'  -> 나레이션 없음'))
                return

            # 참조 음성 준비
            voice = project.voice
            ref_audio_b64 = None
            ref_text = None

            if voice and voice.reference_audio:
                try:
                    with open(voice.reference_audio.path, 'rb') as f:
                        ref_audio_b64 = base64.b64encode(f.read()).decode('utf-8')
                    ref_text = voice.reference_text
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f'  -> 참조 음성 로드 실패: {e}'))

            # TTS 생성
            result = self._generate_tts(tts_text, voice, ref_audio_b64, ref_text, timeout)

            if not result:
                job.mark_failed('TTS 생성 실패')
                self.stdout.write(self.style.ERROR(f'  -> TTS 생성 실패'))
                return

            audio_data, srt_data = result

            # 오디오 저장
            filename = f'scene_{scene.scene_number:02d}.wav'
            scene.audio.save(filename, ContentFile(audio_data), save=False)

            # 오디오 길이 계산
            try:
                with wave.open(io.BytesIO(audio_data), 'rb') as wav:
                    frames = wav.getnframes()
                    rate = wav.getframerate()
                    scene.audio_duration = frames / float(rate)
            except:
                scene.audio_duration = 0

            # 자막 처리
            if srt_data:
                srt_content = srt_data.decode('utf-8')
                srt_timings = self._parse_srt_timings(srt_content)

                if srt_timings and original_narration:
                    mapped_srt, is_valid, srt_word_count, narration_word_count = \
                        self._map_srt_to_narration(srt_timings, original_narration, scene.scene_number)

                    scene.subtitle_word_count = srt_word_count
                    scene.narration_word_count = narration_word_count
                    scene.subtitle_status = 'matched' if is_valid else 'mismatch'

                    srt_filename = f'scene_{scene.scene_number:02d}.srt'
                    scene.subtitle_file.save(srt_filename, ContentFile(mapped_srt.encode('utf-8')), save=False)
                else:
                    srt_filename = f'scene_{scene.scene_number:02d}.srt'
                    scene.subtitle_file.save(srt_filename, ContentFile(srt_data), save=False)
                    scene.subtitle_status = 'none'
            else:
                scene.subtitle_status = 'none'

            scene.save()
            job.mark_completed()

            self.stdout.write(self.style.SUCCESS(
                f'  -> 완료 (길이: {scene.audio_duration:.1f}초)'
            ))

        except Exception as e:
            job.mark_failed(str(e)[:500])
            self.stdout.write(self.style.ERROR(f'  -> 오류: {e}'))

    def _generate_tts(self, text: str, voice, ref_audio_b64: str, ref_text: str, timeout: int):
        """Fish Speech API로 TTS 생성"""
        request_data = {
            'text': text,
            'format': 'wav',
            'use_memory_cache': 'on',
        }

        if voice:
            request_data['temperature'] = voice.temperature
            request_data['top_p'] = voice.top_p
            request_data['repetition_penalty'] = voice.repetition_penalty
            request_data['seed'] = voice.seed

            if ref_audio_b64 and ref_text:
                request_data['references'] = [{
                    'audio': ref_audio_b64,
                    'text': ref_text
                }]
        else:
            request_data['temperature'] = 0.7
            request_data['top_p'] = 0.7
            request_data['seed'] = 42

        try:
            response = requests.post(
                f'{settings.FISH_SPEECH_URL}/v1/tts',
                json=request_data,
                timeout=timeout
            )

            if response.status_code == 200:
                if response.content[:2] == b'PK':
                    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
                        audio_data = zf.read('audio.wav')
                        srt_data = None
                        for name in zf.namelist():
                            if name.endswith('.srt'):
                                srt_data = zf.read(name)
                                break
                        return (audio_data, srt_data)
                else:
                    return (response.content, None)
            else:
                self.stdout.write(self.style.ERROR(f'TTS API 오류: HTTP {response.status_code}'))
                return None

        except requests.exceptions.Timeout:
            self.stdout.write(self.style.ERROR(f'TTS 타임아웃'))
            return None
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'TTS 오류: {e}'))
            return None

    def _parse_srt_timings(self, srt_content: str) -> list:
        """SRT에서 타이밍과 텍스트 추출"""
        timings = []
        srt_content = srt_content.replace('\r\n', '\n').replace('\r', '\n')
        blocks = re.split(r'\n\n+', srt_content.strip())

        for block in blocks:
            lines = block.strip().split('\n')
            if len(lines) >= 3:
                timing_match = re.match(
                    r'(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})',
                    lines[1]
                )
                if timing_match:
                    timings.append({
                        "index": len(timings) + 1,
                        "start": timing_match.group(1).replace('.', ','),
                        "end": timing_match.group(2).replace('.', ','),
                        "text": ' '.join(lines[2:]).strip()
                    })

        return timings

    def _map_srt_to_narration(self, srt_timings: list, narration: str, scene_num: int) -> tuple:
        """SRT 타이밍에 원본 narration 단어 1:1 매핑"""
        narration_words = narration.split()
        srt_word_count = len(srt_timings)
        narration_word_count = len(narration_words)

        if srt_word_count != narration_word_count:
            self.stdout.write(self.style.WARNING(
                f'  -> 씬 {scene_num}: 단어 수 불일치 (원본: {narration_word_count}, SRT: {srt_word_count})'
            ))

        mapped_entries = []
        for i, timing in enumerate(srt_timings):
            if i < len(narration_words):
                word = narration_words[i]
            else:
                word = timing["text"]

            mapped_entries.append(
                f'{i + 1}\n{timing["start"]} --> {timing["end"]}\n{word}\n'
            )

        mapped_srt = '\n'.join(mapped_entries)
        is_valid = (srt_word_count == narration_word_count)

        return (mapped_srt, is_valid, srt_word_count, narration_word_count)
