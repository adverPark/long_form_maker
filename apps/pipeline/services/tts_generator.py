import re
import requests
import base64
import zipfile
import io
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from django.conf import settings
from django.core.files.base import ContentFile
from .base import BaseStepService
from apps.pipeline.models import Scene


class TTSGeneratorService(BaseStepService):
    """TTS 음성 생성 서비스

    Fish Speech API를 사용하여 씬별 TTS 음성 생성
    - narration_tts 사용 (숫자가 한글로 변환된 버전)
    - 프리셋 음성 설정 적용
    - 10개씩 병렬 처리
    """

    agent_name = 'tts_generator'
    BATCH_SIZE = 2  # 병렬 처리 배치 크기 (GPU 경합 감소)
    REQUEST_TIMEOUT = 180  # 요청 타임아웃 (초)

    def _thread_log(self, message, log_type='info'):
        """스레드 안전 로그"""
        if hasattr(self, '_lock'):
            with self._lock:
                self.log(message, log_type)
        else:
            self.log(message, log_type)

    def _preprocess_for_tts(self, text: str) -> str:
        """TTS용 텍스트 전처리 - Fish Speech가 처리 못하는 특수문자 제거/변환

        Args:
            text: 원본 텍스트

        Returns:
            전처리된 텍스트
        """
        if not text:
            return text

        # 1. 모든 종류의 따옴표 제거 (유니코드 카테고리 기반)
        # 작은따옴표 류: U+0027('), U+2018('), U+2019('), U+201A(‚), U+201B(‛)
        # 큰따옴표 류: U+0022("), U+201C("), U+201D("), U+201E(„), U+201F(‟)
        quote_chars = "'\u2018\u2019\u201a\u201b\"\u201c\u201d\u201e\u201f"
        for char in quote_chars:
            text = text.replace(char, "")

        # 2. 기타 특수문자 정리
        # 말줄임표 정규화
        text = re.sub(r'…+', '...', text)
        text = re.sub(r'\.{4,}', '...', text)

        # 3. 연속 공백 제거
        text = re.sub(r'\s+', ' ', text).strip()

        return text

    def _parse_srt_timings(self, srt_content: str) -> list:
        """SRT에서 타이밍과 텍스트 추출

        Returns:
            list: [{"start": "00:00:00,000", "end": "00:00:00,500", "text": "단어"}, ...]
        """
        timings = []

        # 줄바꿈 정규화 (Windows \r\n -> Unix \n)
        srt_content = srt_content.replace('\r\n', '\n').replace('\r', '\n')

        # SRT 블록 단위로 분리 (빈 줄로 구분)
        blocks = re.split(r'\n\n+', srt_content.strip())

        for block in blocks:
            lines = block.strip().split('\n')
            if len(lines) >= 3:
                # 첫 줄: 번호, 둘째 줄: 타이밍, 셋째 줄 이후: 텍스트
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
        """SRT 타이밍에 원본 narration 단어 1:1 매핑

        Args:
            srt_timings: SRT에서 추출한 타이밍 목록
            narration: 원본 나레이션 (숫자 포함)
            scene_num: 씬 번호 (로깅용)

        Returns:
            tuple: (mapped_srt_content, is_valid, srt_word_count, narration_word_count)
        """
        # 따옴표 제거 후 단어 분리 (TTS 전처리와 동일하게)
        clean_narration = self._preprocess_for_tts(narration)
        narration_words = clean_narration.split()
        srt_word_count = len(srt_timings)
        narration_word_count = len(narration_words)

        # 단어 수 비교
        if srt_word_count != narration_word_count:
            self._thread_log(
                f'⚠️ 씬 {scene_num}: 단어 수 불일치! (원본: {narration_word_count}개, SRT: {srt_word_count}개)',
                'warning'
            )
            # 불일치 상세 로깅
            if srt_word_count < narration_word_count:
                missing_start = srt_word_count
                missing_words = narration_words[missing_start:missing_start + 5]
                self._thread_log(
                    f'   누락 시작: "{" ".join(missing_words)}..."',
                    'warning'
                )

        # 1:1 매핑으로 새 SRT 생성 (가능한 만큼만)
        mapped_entries = []
        for i, timing in enumerate(srt_timings):
            if i < len(narration_words):
                # 원본 단어로 교체
                word = narration_words[i]
            else:
                # SRT가 더 길면 원래 텍스트 유지
                word = timing["text"]

            mapped_entries.append(
                f'{i + 1}\n{timing["start"]} --> {timing["end"]}\n{word}\n'
            )

        mapped_srt = '\n'.join(mapped_entries)
        is_valid = (srt_word_count == narration_word_count)

        return (mapped_srt, is_valid, srt_word_count, narration_word_count)

    def _time_to_seconds(self, time_str: str) -> float:
        """SRT 시간 문자열을 초로 변환 (00:00:07,171 → 7.171)"""
        time_str = time_str.replace(',', '.')
        parts = time_str.split(':')
        return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])

    def _check_audio_truncation(self, srt_timings: list) -> tuple:
        """오디오 잘림 감지 - 마지막 단어들의 duration이 비정상적으로 짧으면 잘림 판단

        Returns:
            tuple: (is_truncated: bool, details: str)
        """
        if not srt_timings or len(srt_timings) < 2:
            return False, ''

        # 마지막 3개 단어 검사
        check_count = min(3, len(srt_timings))
        truncated_words = []

        for timing in srt_timings[-check_count:]:
            start = self._time_to_seconds(timing['start'])
            end = self._time_to_seconds(timing['end'])
            duration = end - start
            word = timing['text']

            # 2글자 이상 단어인데 duration이 0.15초 미만이면 잘림 의심
            if len(word) >= 2 and duration < 0.15:
                truncated_words.append(f'{word}({duration:.3f}s)')

        if truncated_words:
            return True, ', '.join(truncated_words)

        return False, ''

    def execute(self):
        self.update_progress(5, '씬 로딩 중...')
        self._lock = threading.Lock()  # 스레드 안전을 위한 락
        self.log('TTS 생성 시작')

        # DB에서 씬 가져오기 (TTS 변환된 것만)
        scenes = list(self.project.scenes.filter(
            narration_tts__isnull=False
        ).exclude(narration_tts='').order_by('scene_number'))

        if not scenes:
            raise ValueError('TTS 변환된 씬이 없습니다. TTS 변환을 먼저 실행해주세요.')

        total = len(scenes)
        all_scenes = self.project.scenes.count()
        self.log(f'TTS 변환된 씬: {total}개 / 전체: {all_scenes}개')

        # 음성 프리셋 정보 로깅
        voice = self.project.voice
        self.log(f'음성 프리셋: {voice.name if voice else "기본값"}')

        # 참조 음성 준비
        ref_audio_b64 = None
        ref_text = None
        if voice and voice.reference_audio:
            try:
                with open(voice.reference_audio.path, 'rb') as f:
                    ref_audio_b64 = base64.b64encode(f.read()).decode('utf-8')
                ref_text = voice.reference_text
                self.log(f'참조 음성 로드 완료')
            except Exception as e:
                self.log(f'참조 음성 로드 실패: {e}', 'error')

        # 생성할 씬 필터링
        scenes_to_process = []
        skip_count = 0

        for scene in scenes:
            if scene.audio:
                self.log(f'씬 {scene.scene_number} 건너뜀 - 오디오 존재')
                skip_count += 1
                continue

            tts_text = scene.narration_tts or scene.narration
            original_narration = scene.narration  # 자막용 원본 (숫자 포함)

            # TTS용 텍스트 전처리 (특수문자 제거)
            original_tts_text = tts_text
            tts_text = self._preprocess_for_tts(tts_text)

            # 전처리 로그
            if original_tts_text != tts_text:
                self.log(f'씬 {scene.scene_number} 텍스트 전처리: "{original_tts_text[:30]}..." → "{tts_text[:30]}..."')

            if not tts_text:
                self.log(f'씬 {scene.scene_number} 건너뜀 - 나레이션 없음', 'warning')
                skip_count += 1
                continue

            scenes_to_process.append((scene, tts_text, original_narration))

        if not scenes_to_process:
            self.log('생성할 씬이 없습니다.')
            self.update_progress(100, f'완료: 0개 생성, {skip_count}개 스킵')
            return

        self.log(f'{len(scenes_to_process)}개 씬 TTS 생성 예정 (배치 크기: {self.BATCH_SIZE})')

        # 배치 병렬 처리
        success_count = 0
        error_count = 0
        warning_count = 0
        truncated_scenes = []  # 잘림 감지된 씬 (재시도 대상)
        processed = 0

        for batch_start in range(0, len(scenes_to_process), self.BATCH_SIZE):
            # 매 배치 시작 전 취소 체크
            self.raise_if_cancelled()

            batch = scenes_to_process[batch_start:batch_start + self.BATCH_SIZE]
            batch_nums = [s[0].scene_number for s in batch]
            self.log(f'배치 처리 중: 씬 {batch_nums}')

            with ThreadPoolExecutor(max_workers=self.BATCH_SIZE) as executor:
                # scene, tts_text, original_narration 튜플
                future_to_scene = {
                    executor.submit(
                        self._generate_tts, tts_text, voice, ref_audio_b64, ref_text
                    ): (scene, tts_text, original_narration)
                    for scene, tts_text, original_narration in batch
                }

                for future in as_completed(future_to_scene):
                    scene, tts_text, original_narration = future_to_scene[future]
                    scene_num = scene.scene_number
                    processed += 1

                    try:
                        result = future.result()
                        if result:
                            audio_data, srt_data = result
                            # 오디오 저장
                            filename = f'scene_{scene_num:02d}.wav'
                            scene.audio.save(filename, ContentFile(audio_data), save=False)

                            # 오디오 길이 계산 (WAV 헤더에서)
                            try:
                                import wave
                                import io
                                with wave.open(io.BytesIO(audio_data), 'rb') as wav:
                                    frames = wav.getnframes()
                                    rate = wav.getframerate()
                                    scene.audio_duration = frames / float(rate)
                            except:
                                scene.audio_duration = 0

                            # 자막 검증 및 매핑 (원본 narration으로!)
                            if srt_data:
                                srt_content = srt_data.decode('utf-8')
                                srt_timings = self._parse_srt_timings(srt_content)

                                self._thread_log(f'씬 {scene_num}: SRT {len(srt_timings)}개 타이밍 파싱됨')

                                if srt_timings and original_narration:
                                    # 원본 narration으로 매핑
                                    mapped_srt, is_valid, srt_word_count, narration_word_count = self._map_srt_to_narration(
                                        srt_timings, original_narration, scene_num
                                    )

                                    # 자막 상태 저장
                                    scene.subtitle_word_count = srt_word_count
                                    scene.narration_word_count = narration_word_count

                                    # 오디오 잘림 감지
                                    is_truncated = False
                                    if srt_word_count < narration_word_count:
                                        # 단어 누락 = 명확한 잘림
                                        is_truncated = True
                                        self._thread_log(
                                            f'⚠️ 씬 {scene_num}: 오디오 잘림 - 단어 누락 ({narration_word_count - srt_word_count}개)',
                                            'warning'
                                        )
                                    elif is_valid:
                                        # 단어 수 일치해도 마지막 단어 duration 짧으면 잘림
                                        is_truncated, truncation_detail = self._check_audio_truncation(srt_timings)
                                        if is_truncated:
                                            self._thread_log(
                                                f'⚠️ 씬 {scene_num}: 오디오 잘림 - {truncation_detail}',
                                                'warning'
                                            )

                                    if is_truncated:
                                        scene.subtitle_status = 'truncated'
                                    elif is_valid:
                                        scene.subtitle_status = 'matched'
                                    else:
                                        scene.subtitle_status = 'mismatch'
                                        warning_count += 1

                                    # 매핑된 SRT 저장
                                    srt_filename = f'scene_{scene_num:02d}.srt'
                                    scene.subtitle_file.save(
                                        srt_filename,
                                        ContentFile(mapped_srt.encode('utf-8')),
                                        save=False
                                    )
                                else:
                                    # 타이밍 추출 실패 시 원본 저장
                                    srt_filename = f'scene_{scene_num:02d}.srt'
                                    scene.subtitle_file.save(srt_filename, ContentFile(srt_data), save=False)
                                    scene.subtitle_status = 'none'
                                    self._thread_log(f'씬 {scene_num}: SRT 파싱 실패 (timings={len(srt_timings)}, narration={bool(original_narration)})', 'warning')
                            else:
                                scene.subtitle_status = 'none'

                            # DB 저장 - UPDATE로 해당 필드만 업데이트 (unique constraint 회피)
                            update_fields = {
                                'audio': scene.audio.name if scene.audio else '',
                                'audio_duration': scene.audio_duration,
                                'subtitle_file': scene.subtitle_file.name if scene.subtitle_file else '',
                                'subtitle_word_count': scene.subtitle_word_count,
                                'narration_word_count': scene.narration_word_count,
                                'subtitle_status': scene.subtitle_status,
                            }
                            Scene.objects.filter(pk=scene.pk).update(**update_fields)
                            if scene.subtitle_status == 'truncated':
                                with self._lock:
                                    self.log(f'씬 {scene_num} 저장 (잘림 감지 - 재시도 대상)')
                                    truncated_scenes.append((scene, tts_text, original_narration))
                            else:
                                with self._lock:
                                    self.log(f'씬 {scene_num} 저장 완료')
                                success_count += 1
                        else:
                            self.log(f'씬 {scene_num} 생성 실패', 'error')
                            error_count += 1
                    except Exception as e:
                        self.log(f'씬 {scene_num} 오류: {str(e)[:50]}', 'error')
                        error_count += 1

                    progress = 5 + int((processed / len(scenes_to_process)) * 90)
                    self.update_progress(progress, f'{processed}/{len(scenes_to_process)} TTS 생성 중...')

        # 잘림 감지된 씬 재시도
        MAX_TRUNCATION_RETRIES = 2
        for retry_round in range(MAX_TRUNCATION_RETRIES):
            if not truncated_scenes:
                break

            self.log(f'오디오 잘림 재시도 [{retry_round + 1}/{MAX_TRUNCATION_RETRIES}]: {len(truncated_scenes)}개 씬')
            still_truncated = []

            # 시드 변경 (원본 시드 + 라운드 오프셋)
            base_seed = voice.seed if voice else 42
            retry_seed = base_seed + (retry_round + 1) * 100
            self.log(f'시드 변경: {base_seed} → {retry_seed}')

            for scene, retry_tts_text, retry_narration in truncated_scenes:
                scene_num = scene.scene_number
                self.log(f'씬 {scene_num} 재생성 중... (시드: {retry_seed})')

                # 기존 오디오/자막 삭제
                try:
                    if scene.audio:
                        scene.audio.delete(save=False)
                    if scene.subtitle_file:
                        scene.subtitle_file.delete(save=False)
                except Exception:
                    pass
                Scene.objects.filter(pk=scene.pk).update(
                    audio='', subtitle_file='', subtitle_status='', audio_duration=0
                )

                # TTS 재생성 (시드 변경 + 캐시 비활성화)
                result = self._generate_tts(retry_tts_text, voice, ref_audio_b64, ref_text, seed_override=retry_seed, disable_cache=True)
                if not result:
                    self.log(f'씬 {scene_num} 재생성 실패', 'error')
                    error_count += 1
                    continue

                audio_data, srt_data = result

                # 오디오 저장
                filename = f'scene_{scene_num:02d}.wav'
                scene.audio.save(filename, ContentFile(audio_data), save=False)

                try:
                    import wave as wave_mod
                    with wave_mod.open(io.BytesIO(audio_data), 'rb') as wav:
                        scene.audio_duration = wav.getnframes() / float(wav.getframerate())
                except Exception:
                    scene.audio_duration = 0

                # 자막 재검증
                retry_truncated = False
                if srt_data:
                    srt_content = srt_data.decode('utf-8')
                    srt_timings = self._parse_srt_timings(srt_content)

                    if srt_timings and retry_narration:
                        mapped_srt, is_valid, srt_wc, narr_wc = self._map_srt_to_narration(
                            srt_timings, retry_narration, scene_num
                        )
                        scene.subtitle_word_count = srt_wc
                        scene.narration_word_count = narr_wc

                        # 잘림 재검사
                        if srt_wc < narr_wc:
                            retry_truncated = True
                            self.log(f'씬 {scene_num}: 재시도 후에도 단어 누락 ({narr_wc - srt_wc}개)', 'warning')
                        elif is_valid:
                            is_trunc, trunc_detail = self._check_audio_truncation(srt_timings)
                            if is_trunc:
                                retry_truncated = True
                                self.log(f'씬 {scene_num}: 재시도 후에도 잘림 - {trunc_detail}', 'warning')

                        if retry_truncated:
                            scene.subtitle_status = 'truncated'
                        elif is_valid:
                            scene.subtitle_status = 'matched'
                        else:
                            scene.subtitle_status = 'mismatch'

                        srt_filename = f'scene_{scene_num:02d}.srt'
                        scene.subtitle_file.save(
                            srt_filename, ContentFile(mapped_srt.encode('utf-8')), save=False
                        )
                    else:
                        scene.subtitle_status = 'none'
                else:
                    scene.subtitle_status = 'none'

                # DB 저장
                Scene.objects.filter(pk=scene.pk).update(
                    audio=scene.audio.name if scene.audio else '',
                    audio_duration=scene.audio_duration,
                    subtitle_file=scene.subtitle_file.name if scene.subtitle_file else '',
                    subtitle_word_count=scene.subtitle_word_count,
                    narration_word_count=scene.narration_word_count,
                    subtitle_status=scene.subtitle_status,
                )

                if retry_truncated:
                    still_truncated.append((scene, retry_tts_text, retry_narration))
                    self.log(f'씬 {scene_num} 재시도 후에도 잘림 - 다음 라운드 대기')
                else:
                    success_count += 1
                    self.log(f'씬 {scene_num} 재생성 성공!')

            truncated_scenes = still_truncated

        # 최종 잘림 씬 처리 (재시도 후에도 해결 안 된 경우)
        if truncated_scenes:
            truncated_nums = [s[0].scene_number for s in truncated_scenes]
            self.log(f'⚠️ 재시도 후에도 잘린 씬: {truncated_nums}', 'warning')
            warning_count += len(truncated_scenes)
            # 잘렸지만 일단 성공으로 카운트 (오디오는 있으므로)
            success_count += len(truncated_scenes)

        # 완료 처리
        self.log(f'TTS 생성 완료', 'result', {
            'total': total,
            'success': success_count,
            'errors': error_count,
            'skipped': skip_count,
            'warnings': warning_count,
        })

        # 실패 처리 - 에러가 성공보다 많거나 모두 실패한 경우
        scenes_attempted = len(scenes_to_process)
        if scenes_attempted > 0:
            if success_count == 0:
                raise ValueError(f'TTS 생성 실패: {scenes_attempted}개 씬 시도했으나 모두 실패')
            elif error_count > success_count:
                raise ValueError(f'TTS 생성 실패: {scenes_attempted}개 중 {error_count}개 실패 (성공: {success_count}개)')

        # 에러 또는 경고가 있으면 메시지에 표시
        if error_count > 0:
            self.update_progress(100, f'완료: {success_count}개 생성, ⚠️ {error_count}개 실패')
        elif warning_count > 0:
            self.update_progress(100, f'완료: {success_count}개 생성, ⚠️ {warning_count}개 단어 불일치')
        else:
            self.update_progress(100, f'완료: {success_count}개 생성, {skip_count}개 스킵')

    def _generate_tts(self, text: str, voice, ref_audio_b64: str, ref_text: str, seed_override: int = None, disable_cache: bool = False) -> tuple:
        """Fish Speech API로 TTS 생성 (재시도 포함)

        Args:
            seed_override: 시드 오버라이드 (잘림 재시도 시 다른 시드 사용)
            disable_cache: 캐시 비활성화 (잘림 재시도 시 다른 결과 유도)

        Returns:
            tuple: (audio_data, srt_data) 또는 None
        """
        import time

        # API 요청 구성
        request_data = {
            'text': text,
            'format': 'wav',
            'use_memory_cache': 'off' if disable_cache else 'on',
            'max_new_tokens': 2048,
        }

        # 프리셋 파라미터
        if voice:
            request_data['temperature'] = voice.temperature
            request_data['top_p'] = voice.top_p
            request_data['repetition_penalty'] = voice.repetition_penalty
            request_data['seed'] = seed_override if seed_override is not None else voice.seed

            # 참조 음성
            if ref_audio_b64 and ref_text:
                request_data['references'] = [{
                    'audio': ref_audio_b64,
                    'text': ref_text
                }]
        else:
            # 기본값
            request_data['temperature'] = 0.7
            request_data['top_p'] = 0.7
            request_data['seed'] = seed_override if seed_override is not None else 42

        # 재시도 로직
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = requests.post(
                    f'{settings.FISH_SPEECH_URL}/v1/tts',
                    json=request_data,
                    timeout=self.REQUEST_TIMEOUT
                )

                if response.status_code == 200:
                    # ZIP 응답 처리
                    if response.content[:2] == b'PK':
                        with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
                            audio_data = zf.read('audio.wav')
                            # 자막 파일도 추출 (있으면)
                            srt_data = None
                            for name in zf.namelist():
                                if name.endswith('.srt'):
                                    srt_data = zf.read(name)
                                    break
                            return (audio_data, srt_data)
                    else:
                        # 직접 WAV 응답 (자막 없음)
                        return (response.content, None)
                else:
                    self._thread_log(f'TTS API 오류: HTTP {response.status_code}', 'error')
                    return None

            except requests.exceptions.Timeout:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt  # 1, 2, 4초
                    self._thread_log(f'TTS 타임아웃, {wait_time}초 후 재시도 ({attempt + 1}/{max_retries})', 'warning')
                    time.sleep(wait_time)
                else:
                    self._thread_log(f'TTS 타임아웃 (모든 재시도 실패)', 'error')
                    return None

            except Exception as e:
                self._thread_log(f'TTS 생성 오류: {str(e)[:100]}', 'error')
                return None

        return None
