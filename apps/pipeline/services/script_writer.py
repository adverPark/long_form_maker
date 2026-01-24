import json
import re
from .base import BaseStepService
from apps.pipeline.models import Draft


class ScriptWriterService(BaseStepService):
    """대본 작성 서비스 - 8000자 분량의 영상 대본"""

    agent_name = 'script_writer'

    def execute(self):
        self.update_progress(10, '대본 작성 준비 중...')

        # 입력 확인 (Research 모델 또는 수동 입력)
        manual_input = self.get_manual_input()

        research_content = ''
        topic = ''

        if manual_input:
            research_content = manual_input
            topic = '사용자 입력 주제'
        elif hasattr(self.project, 'research'):
            research = self.project.research
            topic = research.topic
            research_content = f"""
주제: {research.topic}

요약: {research.summary}

핵심 포인트:
{chr(10).join(f'- {p}' for p in research.key_points)}

통계/수치:
{chr(10).join(f'- {s}' for s in research.statistics)}

인용구:
{chr(10).join(f'- {q}' for q in research.quotes)}

추가 정보:
{research.additional_info}
"""

        if not research_content:
            raise ValueError('리서치 자료가 필요합니다.')

        self.update_progress(20, '대본 생성 중...')

        # 프롬프트 가져오기
        prompt_template = self.get_prompt()

        if prompt_template:
            prompt = f"{prompt_template}\n\n리서치 자료:\n{research_content}"
        else:
            prompt = f"""다음 리서치 자료를 바탕으로 15분 분량의 경제 유튜브 영상 대본을 작성해주세요.

리서치 자료:
{research_content}

요구사항:
1. 총 8000자 (7500~8500자 범위)
2. 스토리텔링 형식 (뉴스 앵커 톤 금지)
3. "상황 가정 → 동의 유도 → 반전" 공식 사용
4. 시청자에게 질문하며 대화하는 느낌
5. "안녕하세요, 오늘은..." 같은 인사 금지
6. 숫자는 그대로 표기 (예: 470%, 1조원)

대본만 출력해주세요. 설명이나 메타 정보 없이 순수 대본 텍스트만.
"""

        # Gemini 호출
        self.update_progress(40, 'AI 대본 생성 중...')
        content = self.call_gemini(prompt)

        # 글자수 확인
        char_count = len(content)
        self.update_progress(70, f'대본 생성 완료 ({char_count}자)')

        # 글자수 부족하면 보강
        if char_count < 7000:
            self.update_progress(75, f'글자수 부족, 보강 중...')
            content = self._expand_content(content)
            char_count = len(content)

        # 제목 추출 (첫 줄 또는 topic 사용)
        title = topic
        lines = content.strip().split('\n')
        if lines and len(lines[0]) < 100:
            first_line = lines[0].strip()
            if first_line and not first_line.startswith(('만약', '여러분', '혹시')):
                title = first_line.replace('#', '').strip()

        # DB에 저장
        self.update_progress(90, '저장 중...')
        Draft.objects.update_or_create(
            project=self.project,
            defaults={
                'title': title or '제목 없음',
                'content': content,
            }
        )

        self.update_progress(100, f'대본 완료: {char_count}자')

    def _expand_content(self, content: str) -> str:
        """글자수 보강"""
        expand_prompt = f"""아래 대본의 글자수가 {len(content)}자로 부족합니다.
8000자 이상이 되도록 내용을 보강해주세요.
기존 내용의 흐름을 유지하면서 설명을 더 풍부하게 추가하세요.

기존 대본:
{content}

보강된 대본만 출력해주세요:"""
        return self.call_gemini(expand_prompt)
