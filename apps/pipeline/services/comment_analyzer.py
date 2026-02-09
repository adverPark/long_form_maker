"""댓글 분석 서비스

YouTube 댓글을 분석하여 청중 욕구를 추출합니다.
7가지 기준: 좋아요/반복/질문/감정/미언급/실용/논쟁
"""

from .base import BaseStepService
from apps.pipeline.models import Research


class CommentAnalyzerService(BaseStepService):
    """댓글 분석 서비스

    YouTube 댓글을 Gemini로 분석하여 Markdown 형식으로 결과 반환
    """

    agent_name = 'comment_analyzer'

    DEFAULT_PROMPT = """당신은 유튜브 콘텐츠 기획자입니다. 아래 댓글들을 분석하여 **청중이 진짜 원하는 것**을 추출하세요.

## 시청자 댓글 (인기순)
{comments}

---

## 분석 목표
댓글에서 **청중의 욕구**를 찾아내는 것입니다. 욕구는 두 가지로 나뉩니다:
- **정보 욕구**: "~를 알고 싶다" (원인, 결과, 데이터, 전망, 방법)
- **감정 욕구**: "~를 느끼고 싶다" (쾌감, 안도, 분노 해소, 우월감, 카타르시스)

## 분석 기준 (7가지)

| 기준 | 설명 | 찾는 방법 |
|-----|------|----------|
| **[좋아요]** | 많은 사람이 공감 | 좋아요 50개 이상 댓글 우선 확인 |
| **[반복]** | 같은 주제가 반복됨 | 비슷한 내용이 3개 이상 댓글에서 등장 |
| **[질문]** | 직접적 질문 | "~는 어떻게?", "~는 왜?", "~는 언제?" |
| **[감정]** | 강한 감정 표현 | 분노, 조롱, 쾌감, 불안, 한탄, 비꼼 |
| **[미언급]** | 영상에서 안 다룬 것 | "~는 왜 안 다뤘지?", "~도 다뤄줬으면" |
| **[실용]** | 행동/투자 관련 | "그래서 어떻게?", "뭘 해야?", "뭘 사야?" |
| **[논쟁]** | 의견이 갈림 | 반박, 반대 의견, 토론이 붙는 댓글 |

## 분석 프로세스

**STEP 1**: 전체 댓글을 빠르게 읽으면서 반복되는 키워드/주제를 메모. 좋아요 50개 이상 댓글 별도 표시.

**STEP 2**: 각 댓글에 해당하는 기준 태그 붙이기 (하나의 댓글에 여러 기준 가능)

**STEP 3**: 비슷한 댓글들을 묶어서 하나의 "욕구"로 정리. 표면적 내용이 아닌 **그 뒤에 숨은 욕구**를 파악.

**STEP 4**: 각 욕구를 "정보" 또는 "감정"으로 분류. "~를 알고 싶다" / "~를 느끼고 싶다" 형식으로 표현.

**STEP 5**: 빈도와 좋아요 수 기준으로 정렬. 상위 10개만 선별.

## 주의사항

1. **표면 vs 숨은 욕구 구분**: "일본 망해라" → 숨은 욕구: "일본이 망하는 모습을 보며 쾌감을 느끼고 싶다"
2. **비슷한 욕구는 반드시 묶기**: "빚투 위험해", "신용 쓰면 깡통" → 하나의 욕구로 통합
3. **소수 의견도 체크**: 좋아요 적어도 [논쟁]이나 [질문]에 해당하면 기록
4. **감정 욕구를 놓치지 말 것**: 카타르시스/우월감/분노해소 욕구도 찾기

## 출력 형식 (Markdown)

## 청중 욕구 분석

### 욕구 리스트

#### 1. [욕구 제목]
- **유형**: 정보/감정
- **욕구**: ~를 알고 싶다 / ~를 느끼고 싶다
- **강도**: 상/중/하
- **빈도**: 약 N개 댓글에서 언급
- **기준**: [좋아요] [반복] ...
- **근거**: "댓글 원문" (좋아요 N개)

#### 2. [욕구 제목]
...

### 주요 댓글 선별
1. "댓글 원문 1"
2. "댓글 원문 2"
...

### 시청자 관심사 요약
- 관심사 1
- 관심사 2
...

---

위 형식대로 Markdown으로 응답해주세요."""

    def execute(self):
        self.update_progress(5, '댓글 데이터 준비 중...')

        # Research 확인 (DB에서 최신 데이터 직접 읽기 - ORM 캐시 회피)
        research = Research.objects.filter(project=self.project).first()
        if not research:
            raise ValueError('리서치 데이터가 없습니다. YouTube 수집을 먼저 실행하세요.')

        comments = list(research.youtube_comments.all()[:200])

        if not comments:
            raise ValueError('분석할 댓글이 없습니다.')

        self.log(f'댓글 {len(comments)}개 분석 시작')

        # 분석 프롬프트 생성
        self.update_progress(20, '댓글 분석 중...')
        prompt = self._build_prompt(comments)

        # Gemini 호출 (Markdown 텍스트)
        result = self.call_gemini(prompt)

        # 결과 저장
        self.update_progress(90, '분석 결과 저장 중...')
        self._save_analysis(research, result)

        self.log('댓글 분석 완료')
        self.update_progress(100, '댓글 분석 완료')

    def _build_prompt(self, comments: list) -> str:
        """분석 프롬프트 생성"""
        # 댓글 텍스트 생성
        comments_text = ""
        for i, c in enumerate(comments[:100], 1):
            like_str = f"[좋아요 {c.like_count}]" if c.like_count else ""
            comments_text += f"{i}. {like_str} {c.text[:300]}\n"

        # DB에서 커스텀 프롬프트 가져오기
        system_prompt = self.get_prompt()

        if system_prompt:
            prompt = system_prompt.replace('{comments}', comments_text)
        else:
            prompt = self.DEFAULT_PROMPT.replace('{comments}', comments_text)

        return prompt

    def _save_analysis(self, research: Research, result: str):
        """분석 결과를 Research에 저장 (Markdown 텍스트)"""
        # DB에서 최신 content_analysis를 직접 읽어서 업데이트 (레이스 컨디션 방지)
        fresh = Research.objects.filter(pk=research.pk).values_list('content_analysis', flat=True).first()
        existing = fresh or {}
        existing['comment_analysis'] = result
        Research.objects.filter(pk=research.pk).update(content_analysis=existing)
        self.log(f'댓글 분석 저장 완료: Research ID={research.pk}')
