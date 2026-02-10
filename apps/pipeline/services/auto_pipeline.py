"""자동 파이프라인 서비스

주제 입력 후 전체 파이프라인 자동 실행:

Phase 1 - 순차 실행:
  (YouTube) YouTube 수집 → 자막 분석 → 댓글 분석 → 대본 계획
  리서치 → 대본 작성 → 씬 분할

Phase 2 - 병렬 실행 (씬 분할 완료 후):
  ├─ 트랙A: TTS 변환 → TTS 생성
  ├─ 트랙B: 스톡 영상
  └─ 트랙C: 이미지 프롬프트 → 이미지 생성

Phase 3 - 순차 실행 (병렬 완료 후):
  영상 조립
"""

import threading
import time
from decimal import Decimal
from django.conf import settings
from django.db import close_old_connections
from .base import BaseStepService
from apps.pipeline.models import PipelineStep, StepExecution

# 동시 실행 제한 (전역 세마포어)
_max_concurrent = getattr(settings, 'MAX_CONCURRENT_PIPELINES', 2)
_pipeline_semaphore = threading.Semaphore(_max_concurrent)
_running_count = 0
_count_lock = threading.Lock()


class AutoPipelineService(BaseStepService):
    """자동 파이프라인 실행 서비스"""

    agent_name = 'auto_pipeline'

    # 재시도 설정
    MAX_RETRIES = 3
    RETRY_DELAY = 30  # 초

    # Phase 1: 순차 실행
    SEQUENTIAL_STEPS = [
        {'name': 'youtube_collector', 'default_model': None, 'display': 'YouTube 수집', 'requires_youtube': True},
        {'name': 'transcript_analyzer', 'default_model': '2.5-flash', 'display': '자막 분석', 'requires_youtube': True},
        {'name': 'comment_analyzer', 'default_model': '2.5-flash', 'display': '댓글 분석', 'requires_youtube': True},
        {'name': 'script_planner', 'default_model': '2.5-flash', 'display': '대본 계획', 'requires_youtube': True},
        {'name': 'researcher', 'default_model': '2.5-flash', 'display': '리서치'},
        {'name': 'script_writer', 'default_model': '2.5-pro', 'display': '대본 작성'},
        {'name': 'scene_planner', 'default_model': None, 'display': '씬 분할'},
    ]

    # Phase 2: 병렬 실행 (씬 분할 완료 후, 트랙별 내부는 순차)
    PARALLEL_TRACKS = [
        # 트랙A: TTS
        [
            {'name': 'tts_converter', 'default_model': 'flash', 'display': 'TTS 변환'},
            {'name': 'tts_generator', 'default_model': None, 'display': 'TTS 생성'},
        ],
        # 트랙B: 스톡 영상
        [
            {'name': 'freepik_video', 'default_model': None, 'display': '스톡 영상'},
        ],
        # 트랙C: 이미지
        [
            {'name': 'image_prompter', 'default_model': 'flash', 'display': '이미지 프롬프트'},
            {'name': 'scene_generator', 'default_model': None, 'display': '이미지 생성'},
        ],
    ]

    TRACK_NAMES = ['TTS', '스톡영상', '이미지']

    # Phase 3: 최종 (병렬 완료 후)
    FINAL_STEPS = [
        {'name': 'video_composer', 'default_model': None, 'display': '영상 조립'},
    ]

    def get_pipeline_steps(self):
        """모델 설정이 적용된 파이프라인 단계 반환 (순차/병렬/최종)"""
        model_settings = {}
        if self.execution.intermediate_data:
            model_settings = self.execution.intermediate_data.get('model_settings', {})

        has_youtube = False
        if hasattr(self.project, 'topic') and self.project.topic:
            url = self.project.topic.url or ''
            has_youtube = 'youtube.com' in url or 'youtu.be' in url

        def apply_settings(steps):
            result = []
            for step in steps:
                if step.get('requires_youtube') and not has_youtube:
                    continue
                step_copy = step.copy()
                step_copy['model'] = model_settings.get(step['name'], step['default_model'])
                result.append(step_copy)
            return result

        return {
            'sequential': apply_settings(self.SEQUENTIAL_STEPS),
            'parallel': [apply_settings(track) for track in self.PARALLEL_TRACKS],
            'final': apply_settings(self.FINAL_STEPS),
        }

    def execute(self):
        global _running_count

        # 동시 실행 제한 확인
        with _count_lock:
            if _running_count >= _max_concurrent:
                self.log(f'동시 실행 제한: 현재 {_running_count}개 실행 중 (최대 {_max_concurrent}개)', 'warning')
                self.log('다른 파이프라인 완료 대기 중...')

        # 세마포어 획득 (대기)
        _pipeline_semaphore.acquire()
        with _count_lock:
            _running_count += 1
            self.log(f'파이프라인 시작 (현재 {_running_count}/{_max_concurrent}개 실행 중)')

        try:
            self._execute_pipeline()
        finally:
            # 세마포어 해제
            with _count_lock:
                _running_count -= 1
            _pipeline_semaphore.release()
            self.log(f'파이프라인 종료 (남은 실행: {_running_count}개)')

    def _execute_pipeline(self):
        """실제 파이프라인 실행 로직 (순차 → 병렬 → 최종)"""
        # 같은 프로젝트에서 이미 실행 중인 자동 파이프라인이 있으면 취소
        running_auto = self.project.step_executions.filter(
            step__name='auto_pipeline',
            status='running'
        ).exclude(pk=self.execution.pk)

        if running_auto.exists():
            count = running_auto.count()
            self.log(f'이전 자동 파이프라인 {count}개 취소')
            running_auto.update(status='cancelled', progress_message='새 파이프라인으로 대체됨')

        # 파이프라인 단계 가져오기 (모델 설정 적용)
        pipeline = self.get_pipeline_steps()

        # 선택된 모델 로깅
        model_settings = self.execution.intermediate_data.get('model_settings', {}) if self.execution.intermediate_data else {}
        if model_settings:
            self.log(f'모델 설정: {model_settings}')

        # 이미 있는 데이터 확인 (DB 직접 쿼리 - ORM 캐시로 인한 stale 데이터 방지)
        from apps.pipeline.models import Research, Draft
        has_research = Research.objects.filter(
            project=self.project
        ).exclude(sources__in=[None, []]).exists()
        has_draft = Draft.objects.filter(project=self.project).exists()
        has_scenes = self.project.scenes.exists()

        skip_steps = set()
        if has_research:
            skip_steps.add('researcher')
            self.log('리서치 결과 있음 - 건너뜀')
        if has_draft:
            skip_steps.add('script_writer')
            self.log('대본 있음 - 건너뜀')
        if has_scenes:
            skip_steps.add('scene_planner')
            self.log(f'씬 {self.project.scenes.count()}개 있음 - 씬분할 건너뜀')

        # === Phase 1: 순차 실행 ===
        sequential_steps = [s for s in pipeline['sequential'] if s['name'] not in skip_steps]
        total_seq = len(sequential_steps)

        for i, step_config in enumerate(sequential_steps):
            step_name = step_config['name']
            model_type = step_config['model']
            display_name = step_config['display']

            progress = int((i / max(total_seq, 1)) * 50)
            self.update_progress(progress, f'{display_name} 시작...')
            self.log(f'[{i + 1}/{total_seq}] {display_name} 시작')

            success, last_error = self._run_step_with_retry(step_name, model_type, display_name)
            if not success:
                raise Exception(f'{display_name} 실패: {last_error}')

            self.log(f'{display_name} 완료')

            # 씬 분할 후 나레이션 검증
            if step_name == 'scene_planner':
                self.project.refresh_from_db()
                scenes_without_narration = self.project.scenes.filter(narration='').count()
                total_scenes = self.project.scenes.count()
                if scenes_without_narration > 0:
                    self.log(f'나레이션 없는 씬: {scenes_without_narration}/{total_scenes}개', 'warning')
                    if scenes_without_narration == total_scenes:
                        raise Exception('모든 씬의 나레이션이 비어있습니다. 씬 분할 결과를 확인해주세요.')

        # === Phase 2: 병렬 실행 ===
        self.update_progress(50, '병렬 처리 시작 (TTS | 스톡영상 | 이미지)...')
        self.log('병렬 실행 시작: TTS | 스톡영상 | 이미지')

        track_errors = {}
        threads = []

        for i, track in enumerate(pipeline['parallel']):
            if not track:  # 빈 트랙 건너뜀
                continue
            track_name = self.TRACK_NAMES[i]
            t = threading.Thread(
                target=self._run_track,
                args=(track, track_name, track_errors)
            )
            threads.append((t, track_name))
            t.start()

        for t, track_name in threads:
            t.join()
            if track_name not in track_errors:
                self.log(f'[{track_name}] 트랙 완료')

        if track_errors:
            error_msgs = [f'{name}: {err}' for name, err in track_errors.items()]
            raise Exception(f'병렬 실행 실패 - {"; ".join(error_msgs)}')

        self.update_progress(85, '병렬 처리 완료')
        self.log('병렬 실행 모두 완료')

        # === Phase 3: 최종 ===
        for step_config in pipeline['final']:
            step_name = step_config['name']
            model_type = step_config['model']
            display_name = step_config['display']

            self.update_progress(88, f'{display_name} 시작...')
            self.log(f'{display_name} 시작')

            success, last_error = self._run_step_with_retry(step_name, model_type, display_name)
            if not success:
                raise Exception(f'{display_name} 실패: {last_error}')

            self.log(f'{display_name} 완료')

        self.log('자동 파이프라인 완료!', 'result')
        self.update_progress(100, '전체 완료!')

    def _run_step_with_retry(self, step_name, model_type, display_name):
        """단계 실행 + 재시도 (최대 MAX_RETRIES회)

        Returns:
            tuple: (success: bool, last_error: str)
        """
        last_error = ''
        for attempt in range(self.MAX_RETRIES):
            if attempt > 0:
                wait_time = self.RETRY_DELAY * attempt
                self.log(f'{display_name} 재시도 {attempt + 1}/{self.MAX_RETRIES} - 이전 오류: {last_error[:100]}', 'warning')
                time.sleep(wait_time)
            try:
                success, last_error = self._run_step(step_name, model_type)
                if success:
                    return True, ''
            except Exception as e:
                last_error = str(e)
                self.log(f'{display_name} 예외: {last_error}', 'error')

        self.log(f'{display_name} 최종 실패 (재시도 {self.MAX_RETRIES}회): {last_error}', 'error')
        return False, last_error

    def _run_track(self, track_steps, track_name, errors_dict):
        """병렬 트랙 실행 (트랙 내 단계는 순차 실행)"""
        try:
            for step_config in track_steps:
                step_name = step_config['name']
                model_type = step_config['model']
                display_name = step_config['display']

                self.log(f'[{track_name}] {display_name} 시작')

                success, last_error = self._run_step_with_retry(
                    step_name, model_type, f'[{track_name}] {display_name}'
                )

                if not success:
                    errors_dict[track_name] = f'{display_name}: {last_error}'
                    return

                self.log(f'[{track_name}] {display_name} 완료')
        finally:
            close_old_connections()

    def _run_step(self, step_name: str, model_type: str = None) -> tuple:
        """단일 단계 실행 (완료까지 대기)

        Returns:
            tuple: (success: bool, error_message: str)
        """
        # 지연 임포트 (순환 참조 방지)
        from . import get_service_class

        step = PipelineStep.objects.filter(name=step_name).first()
        if not step:
            error = f'단계를 찾을 수 없음: {step_name}'
            self.log(error, 'error')
            return False, error

        # 이전 실행 취소
        self.project.step_executions.filter(step=step, status='running').update(
            status='cancelled', progress_message='자동 파이프라인으로 대체됨'
        )

        # 이전 실행에서 토큰 가져오기 (누적)
        prev_execution = self.project.step_executions.filter(step=step).order_by('-created_at').first()
        prev_tokens = {
            'input_tokens': prev_execution.input_tokens if prev_execution else 0,
            'output_tokens': prev_execution.output_tokens if prev_execution else 0,
            'total_tokens': prev_execution.total_tokens if prev_execution else 0,
            'estimated_cost': prev_execution.estimated_cost if prev_execution else 0,
        }

        # 실행 생성 (이전 토큰 누적)
        execution = StepExecution.objects.create(
            project=self.project,
            step=step,
            model_type=model_type or 'flash',
            input_tokens=prev_tokens['input_tokens'],
            output_tokens=prev_tokens['output_tokens'],
            total_tokens=prev_tokens['total_tokens'],
            estimated_cost=prev_tokens['estimated_cost'],
        )

        # 서비스 실행
        service_class = get_service_class(step_name)
        if not service_class:
            error = f'서비스 없음: {step_name}'
            self.log(error, 'error')
            execution.fail(error)
            return False, error

        try:
            service = service_class(execution)

            # 백그라운드에서 실행
            import threading
            run_thread = threading.Thread(target=service.run)
            run_thread.start()

            # 진행률 모니터링 (완료될 때까지)
            display_name = step.display_name
            last_log_count = 0

            while run_thread.is_alive():
                time.sleep(1)  # 1초마다 체크

                # DB에서 직접 쿼리 (refresh_from_db는 JSONField 갱신 안 됨)
                fresh_exec = StepExecution.objects.filter(pk=execution.pk).values(
                    'progress_percent', 'progress_message', 'logs',
                    'input_tokens', 'output_tokens', 'total_tokens', 'estimated_cost'
                ).first()

                if not fresh_exec:
                    continue

                # 진행률 업데이트
                self.update_progress(
                    fresh_exec['progress_percent'],
                    f'{display_name}: {fresh_exec["progress_message"]}'
                )

                # 새 로그 실시간 복사
                logs = fresh_exec['logs'] or []
                if len(logs) > last_log_count:
                    new_logs = logs[last_log_count:]
                    for log in new_logs:
                        self.log(f'[{display_name}] {log.get("message", "")}', log.get('type', 'info'))
                    last_log_count = len(logs)

                # 토큰 사용량도 업데이트
                if fresh_exec['total_tokens'] and fresh_exec['total_tokens'] > 0:
                    self.execution.input_tokens = fresh_exec['input_tokens']
                    self.execution.output_tokens = fresh_exec['output_tokens']
                    self.execution.total_tokens = fresh_exec['total_tokens']
                    self.execution.estimated_cost = fresh_exec['estimated_cost']
                    self.execution.save(update_fields=['input_tokens', 'output_tokens', 'total_tokens', 'estimated_cost'])

            run_thread.join()

            # 스레드 종료 후 DB 연결 정리
            close_old_connections()

            # 결과 확인 (직접 쿼리)
            execution = StepExecution.objects.get(pk=execution.pk)

            # 남은 로그 복사
            if execution.logs and len(execution.logs) > last_log_count:
                for log in execution.logs[last_log_count:]:
                    self.log(f'[{display_name}] {log.get("message", "")}', log.get('type', 'info'))

            if execution.status == 'completed':
                return True, ''
            else:
                error = execution.error_message or f'{step_name} 실패 (원인 불명)'
                self.log(f'{step_name} 실패: {error}', 'error')
                return False, error

        except Exception as e:
            error = str(e)
            self.log(f'{step_name} 예외: {error}', 'error')
            return False, error

