import re
from .base import BaseStepService
from apps.pipeline.models import Topic


class TopicFinderService(BaseStepService):
    """주제 입력 서비스 - 수동으로 주제 입력"""

    agent_name = 'topic_finder'

    def execute(self):
        self.update_progress(10, '주제 저장 중...')

        # 수동 입력 가져오기
        manual_input = self.get_manual_input()

        if not manual_input:
            raise ValueError('주제를 입력해주세요.')

        # URL과 제목 분리 (URL이 있으면)
        title = manual_input
        url = ''
        video_id = ''

        lines = manual_input.strip().split('\n')
        for line in lines:
            line = line.strip()
            if 'youtube.com' in line or 'youtu.be' in line:
                url = line
                video_id = self._extract_video_id(line)
            elif line and not title:
                title = line
            elif line and title == manual_input:
                title = line

        if url and title == manual_input:
            title = '입력된 주제'

        self.update_progress(50, '저장 중...')

        # DB에 저장
        Topic.objects.update_or_create(
            project=self.project,
            defaults={
                'video_id': video_id,
                'title': title,
                'url': url,
                'channel': '',
                'view_count': 0,
                'viral_ratio': 0,
                'reason': manual_input,
            }
        )

        # YouTube URL이 있으면 로그
        if video_id:
            self.log(f'YouTube 영상 감지: {video_id}')

        self.update_progress(100, f'주제 저장 완료: {title[:50]}')

    def _extract_video_id(self, url: str) -> str:
        """URL에서 YouTube video_id 추출"""
        patterns = [
            r'(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})',
            r'youtube\.com/embed/([a-zA-Z0-9_-]{11})',
            r'youtube\.com/v/([a-zA-Z0-9_-]{11})',
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return ''
