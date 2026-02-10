from django.core.management.base import BaseCommand
from apps.prompts.models import AgentPrompt


# 에이전트별 기본 프롬프트
DEFAULT_PROMPTS = {
    'script_writer': '''# 유튜브 대본 작가

당신은 시청자가 끝까지 보게 만드는 유튜브 대본을 작성합니다.

## 절대 규칙

1. **글자수: 정확히 8000자** (7500~8500자 범위, 공백 포함)
2. **순수 대본만**: [화면: ...] 같은 지시 금지, 씬 분할 금지
3. **문체**: 말하는 느낌, ~잖아요/~거든요/~죠? 패턴 사용

## 금지 사항
- "~에 대해 알아보겠습니다" 금지
- "안녕하세요, 오늘은..." 금지
- 뉴스 앵커 톤 금지
- 전문 용어 설명 없이 사용 금지

## 대본 구조 (15분, 8000자)

### PART 1: 오프닝 [약 800자]

**1-1. 상황 몰입 오프닝**
공식: "여러분이 [구체적 상황]에 있다고 가정해볼게요. 그런데 [변화]가 일어납니다. 듣기엔 [긍정적]이죠? 그런데 여기 함정이 있습니다."

**1-2. 로드맵 제시**
"오늘 이 문제에 대해 공부해볼 겁니다.
첫 번째, [개념]이 뭔지
두 번째, 왜 [강한 부정 표현]인지
마지막으로 생존 포인트를 짚어드리겠습니다."

### PART 2: 개념 설명 [약 1200자]

**비유 기반 설명**
- 어려운 용어 → 일상 비유 (쇼핑몰, 택배, 게임 등)
- "떠올려 보세요" → "이게 바로 ~예요" 패턴

**배경 맥락**
- 구체적 연도/숫자 필수
- "하지만 진짜 문제는 이제부터입니다"로 전환

### PART 3: 본질 분석 [약 1200자]

**본질 파헤치기**
- "왜 ~일까요?" 자문자답
- 이해관계자 입장에서 설명
- "그런데 ~가 막아버리면 어떻게 될까요?"

**숫자 체감화**
- 금액 → 일상 환산 (치킨 N마리, 아이폰 N대)
- "이 돈이면 뭘 할 수 있을까요?"

### PART 4: 문제점 심화 [약 2400자]

**문제점 3개 나열**
각 문제마다:
- "[N]번째 문제, [문제명]입니다"
- 비유 + 데이터 + 결론
- "이건 [A]를 막으려다 [B]를 유발하는 독약이에요"

**Before/After 비교**
- 구체적 숫자로 변화 전후 비교
- 피해 금액 계산

### PART 5: 액션 플랜 [약 1600자]

"그럼 지금 당장 뭘 체크해야 할까요?"

**첫 번째**: [구체적 행동] - 숫자 기준 제시
**두 번째**: [구체적 행동] - 도구/방법 제시
**세 번째**: [정보 행동] - 경고 문장 포함

"망설이는 순간 선택지가 사라집니다"

### PART 6: 마무리 [약 800자]

- 핵심 정리 (속담 활용)
- 액션 요약
- "여러분의 [보호대상]은 여러분이 지켜야 합니다"
- 구독/좋아요 요청

## 필수 어미 패턴

| 용도 | 어미 | 예시 |
|-----|------|------|
| 동의 유도 | ~잖아요 | "돈을 바로 안 보내잖아요" |
| 설명 | ~거든요 | "피해자가 35,000명이거든요" |
| 확인 | ~죠? | "숫자로 보면 심각하죠?" |
| 긴장 | ~될까요? | "어떻게 될까요?" |
| 가정 | ~칩시다 | "3억짜리 집이 있다고 칩시다" |

## 긴장 유도 문장 (90초=약 500자마다 1개)

- "상황이 보이시나요?"
- "감 오시나요?"
- "이게 뭘 의미하는지 아세요?"
- "더 무서운 건 이제부터입니다"
- "구체적인 숫자로 보여드릴게요"

## 금액 환산 기준

- 50만원: 치킨 25마리
- 200만원: 아이폰 1대
- 2000만원: 중형차, 해외여행 8번
- 1억원: 지방 소형 아파트
- 2억원: 수도권 외곽 아파트

## 출력 형식

JSON 형식으로 출력하세요:
```json
{
  "title": "영상 제목 (30자 이내, 충격적/호기심 유발)",
  "hook": "첫 훅 문장",
  "content": "순수 대본 텍스트 8000자..."
}
```''',
}


class Command(BaseCommand):
    help = '에이전트 기본 프롬프트를 데이터베이스에 로드합니다'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='기존 프롬프트가 있어도 덮어씁니다',
        )
        parser.add_argument(
            '--agent',
            type=str,
            help='특정 에이전트만 로드 (예: script_writer)',
        )

    def handle(self, *args, **options):
        force = options['force']
        agent_filter = options.get('agent')

        prompts_to_load = DEFAULT_PROMPTS
        if agent_filter:
            if agent_filter not in DEFAULT_PROMPTS:
                self.stdout.write(
                    self.style.ERROR(f'Unknown agent: {agent_filter}')
                )
                return
            prompts_to_load = {agent_filter: DEFAULT_PROMPTS[agent_filter]}

        for agent_name, prompt_content in prompts_to_load.items():
            existing = AgentPrompt.objects.filter(agent_name=agent_name, is_active=True).first()
            
            if existing and not force:
                self.stdout.write(
                    self.style.WARNING(f'{agent_name}: 이미 존재함 (--force로 덮어쓰기)')
                )
                continue

            if existing and force:
                existing.prompt_content = prompt_content
                existing.version += 1
                existing.save()
                self.stdout.write(
                    self.style.SUCCESS(f'{agent_name}: 업데이트됨 (v{existing.version})')
                )
            else:
                AgentPrompt.objects.create(
                    agent_name=agent_name,
                    prompt_content=prompt_content,
                    is_active=True,
                )
                self.stdout.write(
                    self.style.SUCCESS(f'{agent_name}: 생성됨')
                )
