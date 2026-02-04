"""대본 계획 서비스

자막 분석 + 댓글 분석 결과를 바탕으로 대본 구조를 설계합니다.
"""

from .base import BaseStepService
from apps.pipeline.models import Research


class ScriptPlannerService(BaseStepService):
    """대본 계획 서비스

    청중 욕구 + 소재 핵심 정보를 바탕으로 대본 구조 설계
    """

    agent_name = 'script_planner'

    DEFAULT_PROMPT = """당신은 유튜브 콘텐츠 기획자입니다. 앞서 분석한 **청중 욕구 리스트**와 **소재 핵심 정보**를 바탕으로 대본의 전체 구조를 설계하세요.

## 소재 핵심 정보 (자막 분석 결과)
{transcript_analysis}

## 청중 욕구 리스트 (댓글 분석 결과)
{comment_analysis}

---

### 계획 목표
- 어떤 욕구를 충족시킬지 결정
- 어떤 유형의 대본으로 갈지 결정
- 비트 시퀀스(전개 순서) 결정
- 가상 인물 설정
- 핵심 메시지 정의

---

## 계획 프로세스

### STEP 1: 욕구 선택 (1~3개)

청중 욕구 리스트에서 다룰 욕구를 선택합니다.

**선택 기준**:
| 기준 | 설명 |
|-----|------|
| 빈도 | 많은 댓글에서 언급된 욕구 우선 |
| 강도 | 강도가 "상"인 욕구 우선 |
| 충족 가능성 | 소재로 충족 가능한 욕구 선택 |
| 조합 | 정보 욕구 + 감정 욕구 조합 권장 |

**주의**:
- 모든 욕구를 다루려고 하지 말 것
- 핵심 욕구 1~3개에 집중
- 나머지는 과감히 버리기

### STEP 2: 유형 결정 (결정 트리)

소재 특성 체크 결과를 보고 아래 질문에 답하세요:

[Q1] 상식과 반대되는 현상이 있는가?
│
├─ Yes → [Q2] 숨겨진 수익구조/원리가 있는가?
│   ├─ Yes → 유형1: 사회현상 분석형
│   └─ No → [Q3] 특정 병목이 전체를 좌우하는가?
│       ├─ Yes → 유형5: 병목 지배형
│       └─ No → 유형7: 조건 나열형
│
└─ No → [Q4] 과거 특정 사건이 현재를 만들었는가?
    │
    ├─ Yes → 유형2: 기업/산업 분석형
    │
    └─ No → [Q5] 비교할 선택지가 있는가?
        │
        ├─ Yes → 유형3: 국제/방산 분석형
        │
        └─ No → [Q6] 여러 원인이 복합적인가?
            │
            ├─ Yes → [Q7] 개인의 선택이 핵심인가?
            │   ├─ Yes → 유형6: 개인 몰락→실용 가이드형
            │   └─ No → 유형4: 브랜드 몰락형
            │
            └─ No → [Q8] 미래 위기를 경고하는가?
                └─ Yes → 유형7: 위기 시뮬레이션형

### STEP 3: 비트 시퀀스 결정

선택한 유형에 맞는 기본 비트 시퀀스를 확인하고, 필요시 수정합니다.

**유형별 기본 비트 시퀀스**:

| 유형 | 도입 | 전개 | 마무리 |
|-----|------|------|--------|
| 1. 사회현상 분석형 | 역설 제시 | 통념→파괴→비유→증거→원인 | 되돌릴 수 없음 |
| 2. 기업/산업 분석형 | 몰락한 현재 | 과거 회귀→전환점→숨겨진 대응→데이터 비교 | 미래 시나리오+경고 |
| 3. 국제/방산 분석형 | 가정 질문 | 시장 분석→선택지 비교→배신자→성공자 | 기회 촉구 |
| 4. 브랜드 몰락형 | 충격 데이터 | N가지 이유 나열 (복수 가상인물) | FAQ+선택 |
| 5. 병목 지배형 | 역설 | 병목 구조 설명→숫자 계산→미래 시나리오 | 병목 강조 |
| 6. 개인 몰락→실용형 | 개인 몰락 스토리 | 시장 구조 분석→케이스 비교 | 실용 가이드 |
| 7. 위기 시뮬레이션형 | 해외 사례 | N가지 순서→시간순 시뮬레이션 | 위기 대비 촉구 |

### STEP 4: 가상 인물 설정

대본에 등장할 가상 인물을 설계합니다.

**가상 인물 유형**:
| 유형 | 설명 | 예시 |
|-----|------|------|
| 일반인 | 평범한 사람, 청중 대입용 | "32살 직장인 김과장" |
| 피해자 | 위기 상황 당사자 | "은행에 2억 예금한 김상우씨" |
| 업계 인물 | 내부자 시점 | "조선소 재무팀장 김대리" |
| 소비자 | 구매 결정자 | "벤츠 구매한 박대표" |

### STEP 5: 핵심 메시지 정의

**핵심 메시지 공식**:
| 유형 | 공식 |
|-----|------|
| 유형1 | "A하면 당연히 B인데, 실제론 C다. 왜냐하면 D 때문이다." |
| 유형2 | "X는 한때 최고였다. Y 사건 후 Z를 선택했고, 지금 이렇게 됐다." |
| 유형3 | "지금 시장은 X 상황이다. Y는 실패했고 Z는 성공했다. 당신은?" |
| 유형4 | "X가 무너졌다. 원인은 N가지다. 당신은 어떤 선택을 할 것인가?" |
| 유형5 | "X가 막히면 전체가 멈춘다. Y는 X가 없어서 Z에게 손 내밀 수밖에 없다." |
| 유형6 | "민수씨는 이렇게 망했다. 이건 혼자만의 이야기가 아니다. 당신은?" |
| 유형7 | "외국에서 이렇게 터졌다. 한국에서 터지면 이렇게 된다. 준비됐는가?" |

### STEP 6: 마무리 방향 결정

| 마무리 유형 | 적합한 상황 | 예시 |
|------------|------------|------|
| 되돌릴 수 없음 | 구조적 변화 강조 | "다시 돌아갈까요? 안 돌아갑니다" |
| 선택 촉구 | 청중 행동 유도 | "A할 것인가? B할 것인가?" |
| 미래 경고 | 위기 경각심 | "2028년이 되면 ~됩니다" |
| 실용 가이드 | 구체적 조언 필요 | "지금 당장 ~하세요" |
| FAQ | 궁금증 해소 | "Q: ~인가요? A: ~입니다" |

---

## 출력 형식 (Markdown)

## 대본 계획서

### 1. 선택한 욕구
| 순위 | 욕구 | 유형 | 선택 이유 |
|-----|------|------|----------|
| 1 | [욕구1] | 정보/감정 | [왜 선택했는지] |
| 2 | [욕구2] | 정보/감정 | [왜 선택했는지] |
| 3 | [욕구3] | 정보/감정 | [왜 선택했는지] |

### 2. 유형 결정
- 선택한 유형: [유형 번호와 이름]
- 결정 경로: Q1 → [Yes/No] → Q2 → [Yes/No] → ...
- 선택 이유: [왜 이 유형이 적합한지]

### 3. 비트 시퀀스
[도입부]
- 비트: [비트명]
- 내용 방향: [무엇을 말할지 간단히]

[전개부]
- 비트1: [비트명] - [내용 방향]
- 비트2: [비트명] - [내용 방향]
- 비트3: [비트명] - [내용 방향]
- 비트4: [비트명] - [내용 방향]
- 비트5: [비트명] - [내용 방향]

[마무리]
- 비트: [비트명]
- 내용 방향: [무엇을 말할지 간단히]

### 4. 가상 인물
| 이름 | 나이 | 직업/상황 | 역할 |
|-----|------|----------|------|
| [이름1] | [나이] | [직업/상황] | [어떤 관점 대변] |
| [이름2] | [나이] | [직업/상황] | [어떤 관점 대변] |

### 5. 핵심 메시지
> "[한 문장으로 정리한 핵심 메시지]"

### 6. 마무리 방향
- 유형: [되돌릴 수 없음/선택 촉구/미래 경고/실용 가이드/FAQ]
- 마지막 질문/메시지: "[청중에게 던질 마지막 말]"

### 7. 리서치 필요 항목
대본 작성 전에 추가로 조사해야 할 것:
- [ ] [필요한 데이터/숫자]
- [ ] [필요한 사례]
- [ ] [필요한 비교 대상]
- [ ] [필요한 전문가 의견]
- [ ] [필요한 과거 사건]

---

위 형식대로 Markdown으로 응답해주세요."""

    def execute(self):
        self.update_progress(5, '분석 데이터 준비 중...')

        # Research 확인
        if not hasattr(self.project, 'research') or not self.project.research:
            raise ValueError('리서치 데이터가 없습니다.')

        research = self.project.research
        content_analysis = research.content_analysis or {}

        transcript_analysis = content_analysis.get('transcript_analysis', '')
        comment_analysis = content_analysis.get('comment_analysis', '')

        if not transcript_analysis:
            raise ValueError('자막 분석 결과가 없습니다. 자막 분석을 먼저 실행하세요.')

        if not comment_analysis:
            raise ValueError('댓글 분석 결과가 없습니다. 댓글 분석을 먼저 실행하세요.')

        self.log('대본 계획 시작')

        # 프롬프트 생성
        self.update_progress(20, '대본 계획 중...')
        prompt = self._build_prompt(transcript_analysis, comment_analysis)

        # Gemini 호출
        result = self.call_gemini(prompt)

        # 결과 저장
        self.update_progress(90, '계획 저장 중...')
        self._save_plan(research, result)

        self.log('대본 계획 완료')
        self.update_progress(100, '대본 계획 완료')

    def _build_prompt(self, transcript_analysis: str, comment_analysis: str) -> str:
        """계획 프롬프트 생성"""
        system_prompt = self.get_prompt()

        if system_prompt:
            prompt = system_prompt.replace('{transcript_analysis}', transcript_analysis)
            prompt = prompt.replace('{comment_analysis}', comment_analysis)
        else:
            prompt = self.DEFAULT_PROMPT.replace('{transcript_analysis}', transcript_analysis)
            prompt = prompt.replace('{comment_analysis}', comment_analysis)

        return prompt

    def _save_plan(self, research: Research, result: str):
        """계획 결과를 Research에 저장"""
        existing = research.content_analysis or {}
        existing['script_plan'] = result
        research.content_analysis = existing
        research.save()
        self.log(f'대본 계획 저장 완료: Research ID={research.pk}')
