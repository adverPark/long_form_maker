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
from .base import BaseStepService
from apps.pipeline.models import PipelineStep, StepExecution


class AutoPipelineService(BaseStepService):
    """자동 파이프라인 실행 서비스"""

    agent_name = 'auto_pipeline'

    # 실행할 단계들 (순서대로)
    PIPELINE_STEPS = [
        {'name': 'researcher', 'model': 'flash', 'display': '리서치'},
        {'name': 'script_writer', 'model': 'pro', 'display': '대본 작성'},
        {'name': 'scene_planner', 'model': 'flash', 'display': '씬 분할'},
        # 병렬 실행
        {'name': 'image_prompter', 'model': 'flash', 'display': '이미지 프롬프트', 'parallel_with': 'tts_generator'},
        {'name': 'tts_generator', 'model': None, 'display': 'TTS 생성', 'skip_if_parallel': True},
        # 마지막
        {'name': 'scene_generator', 'model': 'pro', 'display': '이미지 생성'},
    ]

    def execute(self):
        self.log('자동 파이프라인 시작')
        total_steps = len([s for s in self.PIPELINE_STEPS if not s.get('skip_if_parallel')])
        current_step = 0

        for step_config in self.PIPELINE_STEPS:
            step_name = step_config['name']
            model_type = step_config['model']
            display_name = step_config['display']

            # 병렬 실행 시 스킵 (이미 실행됨)
            if step_config.get('skip_if_parallel'):
                continue

            current_step += 1
            base_progress = int((current_step - 1) / total_steps * 100)
            self.update_progress(base_progress, f'{display_name} 시작...')
            self.log(f'[{current_step}/{total_steps}] {display_name} 시작')

            # 병렬 실행할 단계가 있는지 확인
            parallel_step = step_config.get('parallel_with')

            if parallel_step:
                # 병렬 실행
                success = self._run_parallel_steps(step_name, parallel_step, model_type)
            else:
                # 순차 실행
                success = self._run_step(step_name, model_type)

            if not success:
                self.log(f'{display_name} 실패 - 파이프라인 중단', 'error')
                raise Exception(f'{display_name} 단계 실패')

            self.log(f'{display_name} 완료')

        self.log('자동 파이프라인 완료!', 'result')
        self.update_progress(100, '전체 완료!')

    def _run_step(self, step_name: str, model_type: str = None) -> bool:
        """단일 단계 실행 (완료까지 대기)"""
        # 지연 임포트 (순환 참조 방지)
        from . import get_service_class

        step = PipelineStep.objects.filter(name=step_name).first()
        if not step:
            self.log(f'단계를 찾을 수 없음: {step_name}', 'error')
            return False

        # 이전 실행 취소
        self.project.step_executions.filter(step=step, status='running').update(
            status='cancelled', progress_message='자동 파이프라인으로 대체됨'
        )

        # 실행 생성
        execution = StepExecution.objects.create(
            project=self.project,
            step=step,
            model_type=model_type or 'flash',
        )

        # 서비스 실행
        service_class = get_service_class(step_name)
        if not service_class:
            self.log(f'서비스를 찾을 수 없음: {step_name}', 'error')
            execution.fail(f'서비스 없음: {step_name}')
            return False

        try:
            service = service_class(execution)
            service.run()

            # 결과 확인
            execution.refresh_from_db()
            if execution.status == 'completed':
                return True
            else:
                self.log(f'{step_name} 실패: {execution.error_message}', 'error')
                return False

        except Exception as e:
            self.log(f'{step_name} 오류: {str(e)[:100]}', 'error')
            return False

    def _run_parallel_steps(self, step1_name: str, step2_name: str, model_type: str = None) -> bool:
        """두 단계 병렬 실행"""
        results = {'step1': None, 'step2': None}
        threads = []

        def run_step(step_name, result_key, model):
            try:
                success = self._run_step(step_name, model)
                results[result_key] = success
            except Exception as e:
                self.log(f'{step_name} 스레드 오류: {str(e)[:50]}', 'error')
                results[result_key] = False

        # 스레드 시작
        t1 = threading.Thread(target=run_step, args=(step1_name, 'step1', model_type))
        t2 = threading.Thread(target=run_step, args=(step2_name, 'step2', None))

        t1.start()
        t2.start()

        # 완료 대기
        t1.join()
        t2.join()

        return results['step1'] and results['step2']
