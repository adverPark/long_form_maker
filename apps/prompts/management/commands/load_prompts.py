from pathlib import Path
from django.core.management.base import BaseCommand
from django.conf import settings
from apps.prompts.models import AgentPrompt


class Command(BaseCommand):
    help = '.claude/agents/ 폴더에서 에이전트 프롬프트 로드'

    def add_arguments(self, parser):
        parser.add_argument(
            '--source-dir',
            type=str,
            default=str(settings.AGENTS_DIR),
            help='프롬프트 파일이 있는 디렉토리 경로'
        )

    def handle(self, *args, **options):
        source_dir = Path(options['source_dir'])

        if not source_dir.exists():
            self.stdout.write(self.style.WARNING(f'디렉토리가 존재하지 않습니다: {source_dir}'))
            return

        # 에이전트 이름 매핑 (파일명 -> DB 이름)
        agent_mapping = {
            'script-writer.md': 'script_writer',
            'scene-planner.md': 'scene_planner',
            'image-prompter.md': 'image_prompter',
            'scene-generator.md': 'scene_generator',
            'video-generator.md': 'video_generator',
            'video-composer.md': 'video_composer',
            'thumbnail-generator.md': 'thumbnail_generator',
            'topic-finder.md': 'topic_finder',
            'researcher.md': 'researcher',
        }

        loaded_count = 0

        for filename, agent_name in agent_mapping.items():
            file_path = source_dir / filename

            if not file_path.exists():
                self.stdout.write(f'  건너뜀: {filename} (파일 없음)')
                continue

            content = file_path.read_text(encoding='utf-8')

            # 기존 프롬프트 확인
            existing = AgentPrompt.objects.filter(agent_name=agent_name, is_active=True).first()

            if existing:
                if existing.prompt_content == content:
                    self.stdout.write(f'  변경 없음: {agent_name}')
                    continue
                # 버전 증가
                AgentPrompt.objects.create(
                    agent_name=agent_name,
                    prompt_content=content,
                    version=existing.version + 1,
                    is_active=True
                )
                self.stdout.write(f'  업데이트: {agent_name} (v{existing.version + 1})')
            else:
                AgentPrompt.objects.create(
                    agent_name=agent_name,
                    prompt_content=content,
                    version=1,
                    is_active=True
                )
                self.stdout.write(f'  생성: {agent_name}')

            loaded_count += 1

        self.stdout.write(self.style.SUCCESS(f'프롬프트 로드 완료! ({loaded_count}개)'))
