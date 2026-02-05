"""자막 분석 서비스

YouTube 자막을 분석하여 대본 작성에 필요한 핵심 정보를 추출합니다.
"""

from .base import BaseStepService
from apps.pipeline.models import Research


class TranscriptAnalyzerService(BaseStepService):
    """자막 분석 서비스

    YouTube 자막을 Gemini로 분석하여 Markdown 형식으로 결과 반환
    """

    agent_name = 'transcript_analyzer'

    DEFAULT_PROMPT = """당신은 유튜브 콘텐츠 기획자입니다. 아래 자막을 분석하여 **대본 작성에 필요한 핵심 정보**를 추출하세요.

## 영상 자막
{transcript}

---

## 분석 목표
자막에서 **대본에 활용할 수 있는 재료**를 찾아내는 것입니다:
- 충격적인 숫자/데이터
- 인용할 수 있는 발언
- 스토리로 만들 수 있는 사건
- 비교할 수 있는 대상
- 소재의 구조적 특성

## 추출 항목

**1. 기본 정보**
- 주제: 한 문장으로 요약
- 핵심 주장: 영상이 말하고자 하는 것
- 톤: 경고/분석/폭로/교육/비판 중 선택

**2. 데이터/숫자**
- 충격적인 숫자 (그 자체로 임팩트 있는 것)
- 비교 숫자 (A vs B)
- 변화 숫자 (과거 → 현재)
- 계산 가능한 숫자 (환산, 누적 등)

**3. 등장 인물/대상**
- 인물: 이름, 직책, 역할
- 기업/기관: 이름, 역할
- 국가: 이름, 입장/상황

**4. 사건/타임라인**
- 과거 사건: 날짜 + 무슨 일
- 전환점: 상황이 바뀐 계기
- 현재 상황: 지금 어떤 상태인지

**5. 인용/발언**
- 직접 인용 가능한 발언
- 누가 한 말인지 출처

**6. 소재 특성 체크**
아래 항목 중 해당하는 것에 체크:
- 상식과 반대되는 현상이 있다 (역설)
- 과거 특정 사건이 현재를 만들었다 (인과)
- 비교할 선택지가 있다 (국가/제품/기업)
- 여러 원인/요인이 복합적이다 (N가지)
- 개인의 선택이 핵심이다 (개인 스토리)
- 미래 위기를 경고한다 (경고)
- 숨겨진 수익구조/원리가 있다 (통념 파괴)
- 특정 병목이 전체를 좌우한다 (병목)

## 주의사항

1. **숫자는 맥락과 함께**: "30조" (X) → "빚투 규모 30조, 역대 최대" (O)
2. **발언은 정확히 인용**: 요약하지 말고 원문 그대로 기록
3. **소재 특성은 복수 선택 가능**: 가장 강한 특성을 판단할 것
4. **대본에 안 쓸 정보도 일단 추출**: 나중에 선별하므로 일단 다 기록

## 출력 형식 (Markdown)

## 소재 핵심 정보

### 기본 정보
- 주제: [한 문장 요약]
- 핵심 주장: [영상이 말하고자 하는 것]
- 톤: [경고/분석/폭로/교육/비판]

### 데이터/숫자
| 숫자 | 맥락 | 활용 가능성 |
|-----|------|------------|
| [숫자1] | [무엇에 대한 숫자인지] | 충격/비교/변화/계산 |

### 등장 인물/대상
| 이름 | 유형 | 역할 |
|-----|------|------|
| [이름1] | 인물/기업/국가 | [어떤 역할인지] |

### 사건/타임라인
| 시점 | 사건 | 중요도 |
|-----|------|--------|
| [날짜/시기1] | [무슨 일] | 전환점/배경/결과 |

### 인용/발언
- "[발언 내용1]" - [누가]
- "[발언 내용2]" - [누가]

### 소재 특성 체크
- [X] 상식과 반대되는 현상이 있다 (역설)
- [ ] 과거 특정 사건이 현재를 만들었다 (인과)
- [ ] 비교할 선택지가 있다 (국가/제품/기업)
- [ ] 여러 원인/요인이 복합적이다 (N가지)
- [ ] 개인의 선택이 핵심이다 (개인 스토리)
- [ ] 미래 위기를 경고한다 (경고)
- [ ] 숨겨진 수익구조/원리가 있다 (통념 파괴)
- [ ] 특정 병목이 전체를 좌우한다 (병목)

**가장 강한 특성**: [특성명] - [이유]

---

위 형식대로 Markdown으로 응답해주세요."""

    def execute(self):
        self.update_progress(5, '자막 데이터 준비 중...')

        # Research 확인
        if not hasattr(self.project, 'research') or not self.project.research:
            raise ValueError('리서치 데이터가 없습니다. YouTube 수집을 먼저 실행하세요.')

        research = self.project.research
        transcript = research.transcript or ''

        if not transcript:
            raise ValueError('분석할 자막이 없습니다.')

        self.log(f'자막 {len(transcript)}자 분석 시작')

        # 분석 프롬프트 생성
        self.update_progress(20, '자막 분석 중...')
        prompt = self._build_prompt(transcript)

        # Gemini 호출 (Markdown 텍스트)
        result = self.call_gemini(prompt)

        # 결과 저장
        self.update_progress(90, '분석 결과 저장 중...')
        self._save_analysis(research, result)

        self.log('자막 분석 완료')
        self.update_progress(100, '자막 분석 완료')

    def _build_prompt(self, transcript: str) -> str:
        """분석 프롬프트 생성"""
        transcript_text = transcript[:15000]

        system_prompt = self.get_prompt()

        if system_prompt:
            prompt = system_prompt.replace('{transcript}', transcript_text)
        else:
            prompt = self.DEFAULT_PROMPT.replace('{transcript}', transcript_text)

        return prompt

    def _save_analysis(self, research: Research, result: str):
        """분석 결과를 Research에 저장 (Markdown 텍스트)"""
        # DB에서 최신 content_analysis를 직접 읽어서 업데이트 (레이스 컨디션 방지)
        fresh = Research.objects.filter(pk=research.pk).values_list('content_analysis', flat=True).first()
        existing = fresh or {}
        existing['transcript_analysis'] = result
        Research.objects.filter(pk=research.pk).update(content_analysis=existing)
        self.log(f'자막 분석 저장 완료: Research ID={research.pk}')
