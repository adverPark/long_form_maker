"""자동 파이프라인 서비스

주제 입력 후 전체 파이프라인 자동 실행:
1. 리서치 (researcher)
2. 대본 작성 (script_writer)
3. 씬 분할 (scene_planner)
4. 이미지 프롬프트 + TTS (병렬: image_prompter + tts_generator)
5. 이미지 생성 (scene_generator)
"""

import threading
import time
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

    # 실행할 단계들 (순서대로)
    PIPELINE_STEPS = [
        {'name': 'researcher', 'model': 'flash', 'display': '리서치'},
        {'name': 'script_writer', 'model': 'pro', 'display': '대본 작성'},
        {'name': 'scene_planner', 'model': 'flash', 'display': '씬 분할'},
        # TTS는 백그라운드로 시작
        {'name': 'tts_generator', 'model': None, 'display': 'TTS 생성', 'background': True},
        # 이미지 프롬프트 → 이미지 생성 (순차)
        {'name': 'image_prompter', 'model': 'flash', 'display': '이미지 프롬프트'},
        {'name': 'scene_generator', 'model': 'pro', 'display': '이미지 생성'},
    ]

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
        """실제 파이프라인 실행 로직"""
        # 같은 프로젝트에서 이미 실행 중인 자동 파이프라인이 있으면 취소
        running_auto = self.project.step_executions.filter(
            step__name='auto_pipeline',
            status='running'
        ).exclude(pk=self.execution.pk)

        if running_auto.exists():
            count = running_auto.count()
            self.log(f'이전 자동 파이프라인 {count}개 취소')
            running_auto.update(status='cancelled', progress_message='새 파이프라인으로 대체됨')

        # 이미 있는 데이터 확인
        has_research = hasattr(self.project, 'research') and self.project.research
        has_draft = hasattr(self.project, 'draft') and self.project.draft
        has_scenes = self.project.scenes.exists()

        skip_steps = set()
        if has_research:
            skip_steps.add('researcher')
            self.log('리서치 있음 - 건너뜀')
        if has_draft:
            skip_steps.add('script_writer')
            self.log('대본 있음 - 건너뜀')
        if has_scenes:
            skip_steps.add('scene_planner')
            self.log(f'씬 {self.project.scenes.count()}개 있음 - 씬분할 건너뜀')

        # 실행할 단계만 카운트 (백그라운드 제외)
        steps_to_run = [s for s in self.PIPELINE_STEPS
                        if s['name'] not in skip_steps and not s.get('background')]
        total_steps = len(steps_to_run)

        if total_steps == 0:
            self.log('모든 단계가 이미 완료됨')
            return

        current_step = 0
        background_threads = []  # 백그라운드 작업 추적

        for step_config in self.PIPELINE_STEPS:
            step_name = step_config['name']
            model_type = step_config['model']
            display_name = step_config['display']

            # 이미 데이터 있으면 건너뜀
            if step_name in skip_steps:
                continue

            # 백그라운드 실행
            if step_config.get('background'):
                self.log(f'{display_name} 백그라운드 시작')
                bg_thread = threading.Thread(
                    target=self._run_step_background,
                    args=(step_name, model_type, display_name)
                )
                bg_thread.start()
                background_threads.append((bg_thread, display_name))
                continue

            current_step += 1
            base_progress = int((current_step - 1) / total_steps * 100)
            self.update_progress(base_progress, f'{display_name} 시작...')
            self.log(f'[{current_step}/{total_steps}] {display_name} 시작')

            # 재시도 로직
            success = False
            last_error = ''

            for attempt in range(self.MAX_RETRIES):
                if attempt > 0:
                    wait_time = self.RETRY_DELAY * attempt
                    self.log(f'{display_name} 재시도 {attempt + 1}/{self.MAX_RETRIES} - 이전 오류: {last_error[:100]}', 'warning')
                    self.update_progress(base_progress, f'{display_name} 재시도 {attempt + 1}/{self.MAX_RETRIES} ({wait_time}초 대기)')
                    time.sleep(wait_time)

                try:
                    success, last_error = self._run_step(step_name, model_type)
                    if success:
                        break
                except Exception as e:
                    last_error = str(e)
                    self.log(f'{display_name} 예외 발생: {last_error}', 'error')

            if not success:
                self.log(f'{display_name} 최종 실패 (재시도 {self.MAX_RETRIES}회): {last_error}', 'error')
                raise Exception(f'{display_name} 실패: {last_error}')

            self.log(f'{display_name} 완료')

        # 백그라운드 작업 완료 대기
        for bg_thread, display_name in background_threads:
            if bg_thread.is_alive():
                self.log(f'{display_name} 완료 대기 중...')
                bg_thread.join()
                self.log(f'{display_name} 완료')

        self.log('자동 파이프라인 완료!', 'result')
        self.update_progress(100, '전체 완료!')

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

    def _run_step_background(self, step_name: str, model_type: str, display_name: str):
        """백그라운드로 단계 실행 (완료 안 기다림)"""
        try:
            for attempt in range(self.MAX_RETRIES):
                if attempt > 0:
                    wait_time = self.RETRY_DELAY * attempt
                    self.log(f'{display_name} 재시도 {attempt + 1}/{self.MAX_RETRIES}', 'warning')
                    time.sleep(wait_time)

                success, error = self._run_step(step_name, model_type)
                if success:
                    return
                else:
                    self.log(f'{display_name} 실패: {error[:100]}', 'error')

            self.log(f'{display_name} 최종 실패', 'error')
        finally:
            # 스레드 종료 시 DB 연결 정리
            close_old_connections()

    def _run_parallel_steps(self, step1_name: str, step2_name: str, model_type: str = None) -> tuple:
        """두 단계 병렬 실행

        Returns:
            tuple: (success: bool, error_message: str)
        """
        results = {'step1': (False, ''), 'step2': (False, '')}

        def run_step(step_name, result_key, model):
            try:
                success, error = self._run_step(step_name, model)
                results[result_key] = (success, error)
            except Exception as e:
                error = f'{step_name} 스레드 오류: {str(e)}'
                self.log(error, 'error')
                results[result_key] = (False, error)

        # 스레드 시작
        t1 = threading.Thread(target=run_step, args=(step1_name, 'step1', model_type))
        t2 = threading.Thread(target=run_step, args=(step2_name, 'step2', None))

        t1.start()
        t2.start()

        # 완료 대기
        t1.join()
        t2.join()

        # 결과 취합
        success1, error1 = results['step1']
        success2, error2 = results['step2']

        if success1 and success2:
            return True, ''
        else:
            errors = []
            if not success1:
                errors.append(f'{step1_name}: {error1}')
            if not success2:
                errors.append(f'{step2_name}: {error2}')
            return False, ' | '.join(errors)
