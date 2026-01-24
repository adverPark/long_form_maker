from django.core.management.base import BaseCommand
from apps.pipeline.models import PipelineStep


class Command(BaseCommand):
    help = '파이프라인 단계 초기 데이터 생성'

    def handle(self, *args, **options):
        steps = [
            {
                'name': 'topic_finder',
                'display_name': '1. 주제 입력',
                'description': '영상으로 만들 주제와 참고 URL을 입력합니다.',
                'order': 1,
                'can_run_independently': True,
                'manual_input_description': '주제를 입력하세요.',
            },
            {
                'name': 'researcher',
                'display_name': '2. 리서치',
                'description': '선정된 주제에 대해 자막 분석 + 웹 검색으로 자료를 수집합니다.',
                'order': 2,
                'can_run_independently': True,
                'manual_input_description': '리서치할 주제 또는 유튜브 URL을 입력하세요.',
            },
            {
                'name': 'script_writer',
                'display_name': '3. 대본 작성',
                'description': '8000자 분량의 영상 대본을 작성합니다.',
                'order': 3,
                'can_run_independently': True,
                'manual_input_description': '리서치 자료 또는 대본 주제를 입력하세요.',
            },
            {
                'name': 'scene_planner',
                'display_name': '4. 씬 분할',
                'description': '대본을 45-60개의 씬으로 분할합니다.',
                'order': 4,
                'can_run_independently': True,
                'manual_input_description': '대본을 붙여넣으세요 (8000자 권장).',
            },
            {
                'name': 'image_prompter',
                'display_name': '5. 이미지 프롬프트',
                'description': '각 씬의 이미지 프롬프트를 작성합니다.',
                'order': 5,
                'can_run_independently': False,
            },
            {
                'name': 'scene_generator',
                'display_name': '6. 이미지 생성',
                'description': '각 씬의 이미지를 생성합니다 (Gemini).',
                'order': 6,
                'can_run_independently': False,
            },
            {
                'name': 'video_generator',
                'display_name': '7. 동영상 생성',
                'description': '앞 4개 씬의 동영상을 생성합니다 (Replicate).',
                'order': 7,
                'can_run_independently': False,
            },
            {
                'name': 'video_composer',
                'display_name': '8. 영상 편집',
                'description': 'TTS + 자막 + 영상을 합성합니다.',
                'order': 8,
                'can_run_independently': False,
            },
            {
                'name': 'thumbnail_generator',
                'display_name': '9. 썸네일',
                'description': '썸네일과 업로드 정보를 생성합니다.',
                'order': 9,
                'can_run_independently': False,
            },
        ]

        for step_data in steps:
            step, created = PipelineStep.objects.update_or_create(
                name=step_data['name'],
                defaults=step_data
            )
            status = '생성' if created else '업데이트'
            self.stdout.write(f'{status}: {step.display_name}')

        self.stdout.write(self.style.SUCCESS('파이프라인 단계 설정 완료!'))
