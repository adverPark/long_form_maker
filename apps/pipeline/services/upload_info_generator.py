import json
import re
import wave
from .base import BaseStepService
from apps.pipeline.models import UploadInfo, Research


class UploadInfoGeneratorService(BaseStepService):
    """업로드 정보 생성 서비스 (제목, 설명, 타임라인, 태그, 썸네일 프롬프트)"""

    agent_name = 'upload_info_generator'

    def execute(self):
        self.update_progress(5, '데이터 준비 중...')

        # 씬 정보 수집
        scenes = list(self.project.scenes.all().order_by('scene_number'))
        if not scenes:
            raise ValueError('씬이 없습니다. 씬 분할을 먼저 진행하세요.')

        self.log(f'총 {len(scenes)}개 씬 로드')

        # 씬 시간 + 섹션 + 나레이션 수집
        scene_info_list = []
        current_time = 0

        for scene in scenes:
            duration = 0
            if scene.audio:
                try:
                    with wave.open(scene.audio.path, 'rb') as wav:
                        duration = wav.getnframes() / float(wav.getframerate())
                except Exception:
                    pass
            if duration == 0:
                duration = scene.audio_duration or scene.duration or 0

            scene_info_list.append({
                'scene': scene.scene_number,
                'time': current_time,
                'section': scene.section,
                'narration': scene.narration or '',
            })
            current_time += duration

        total_duration = current_time
        total_mins = int(total_duration // 60)
        total_secs = int(total_duration % 60)

        # script_plan 가져오기 (DB에서 최신 데이터 직접 읽기 - ORM 캐시 회피)
        script_plan = ''
        try:
            research = Research.objects.filter(project=self.project).first()
            if research and research.content_analysis:
                script_plan = research.content_analysis.get('script_plan', '')
                if script_plan:
                    self.log(f'script_plan 로드: {len(str(script_plan))}자')
        except Exception:
            pass

        if not script_plan:
            self.log('script_plan 없음 - 씬 정보만으로 진행', 'warning')

        # 씬 정보 텍스트 변환
        scenes_text = ""
        for s in scene_info_list:
            mins = int(s['time'] // 60)
            secs = int(s['time'] % 60)
            scenes_text += f"[{mins:02d}:{secs:02d}] 씬{s['scene']} ({s['section']}): {s['narration']}\n"

        # UploadInfo 가져오거나 생성
        info, created = UploadInfo.objects.get_or_create(
            project=self.project,
            defaults={'title': self.project.name}
        )

        # ===== 1단계: 제목 + 설명 + 타임라인 생성 =====
        self.update_progress(20, '업로드 정보 생성 중...')
        self.raise_if_cancelled()

        script_plan_section = ""
        if script_plan:
            script_plan_text = json.dumps(script_plan, ensure_ascii=False, indent=2) if isinstance(script_plan, (dict, list)) else str(script_plan)
            script_plan_section = f"""
## 대본 생성 계획
{script_plan_text}
"""

        prompt = f"""YouTube 영상 업로드 정보를 생성해주세요.

## 영상 정보
- 총 길이: {total_mins}분 {total_secs}초
- 씬 개수: {len(scene_info_list)}개
{script_plan_section}
## 전체 씬 (시간 + 나레이션)
{scenes_text}

## 생성해주세요

1. **제목** (50자 이내): 클릭 유도하는 매력적인 제목
2. **설명**: 훅(1-2문장) + 요약(3-4문장) + 구독 요청
3. **타임라인**: 섹션별 시작 시간 + 내용 기반 제목 (10자 이내)
   - intro, body_1, body_2, body_3, action, outro 각각
   - "본론 1" 같은 의미없는 제목 금지!

JSON 형식:
{{
    "title": "영상 제목",
    "description": "훅\\n\\n요약\\n\\n📌 구독과 좋아요 부탁드려요!\\n🔔 알림 설정하세요!",
    "timeline": [
        {{"time": "00:00", "title": "시작 제목"}},
        {{"time": "01:16", "title": "다음 제목"}},
        ...
    ]
}}

주의: JSON만 응답 (```json 없이)"""

        response_text = self.call_gemini(prompt)

        # JSON 파싱
        response_text = response_text.strip()
        if response_text.startswith('```'):
            response_text = response_text.split('\n', 1)[1]
            if response_text.endswith('```'):
                response_text = response_text[:-3]

        result = json.loads(response_text)
        info.title = result.get('title', self.project.name)[:100]
        info.description = result.get('description', '').strip()
        info.timeline = result.get('timeline', [])

        self.log(f'제목: {info.title}')
        self.log(f'타임라인: {len(info.timeline)}개 항목')

        # ===== 2단계: 태그 생성 =====
        self.update_progress(60, '태그 생성 중...')
        self.raise_if_cancelled()

        excluded_keywords = {'유흥', '술집', '노래방', '호프', '소주', '맥주', '주류', '성인'}
        tags = []

        if info.title:
            words = re.findall(r'[가-힣]+', info.title)
            for word in words:
                if len(word) >= 2 and word not in excluded_keywords and word not in tags:
                    tags.append(word)
                    if len(tags) >= 15:
                        break

        info.tags = tags[:15]
        self.log(f'태그: {len(info.tags)}개')

        # ===== 3단계: 참고자료 생성 (리서치 출처 기반) =====
        self.update_progress(70, '참고자료 생성 중...')
        self.raise_if_cancelled()

        try:
            research = Research.objects.filter(project=self.project).first()
            research_text = ''
            if research:
                # content_analysis의 research_result (출처 포함 리서치 텍스트)
                if research.content_analysis and research.content_analysis.get('research_result'):
                    research_text += research.content_analysis['research_result'] + '\n\n'
                # article_summaries 폴백
                if not research_text and research.article_summaries:
                    for item in research.article_summaries:
                        summary = item.get('summary', '')
                        if summary:
                            research_text += summary + '\n\n'
                # manual_notes도 포함
                if research.manual_notes:
                    research_text += research.manual_notes + '\n\n'

            if research_text.strip():
                ref_prompt = f"""아래 리서치 자료에서 참고자료 목록을 만들어주세요.

리서치 내용:
{research_text[:5000]}

규칙:
- 각 섹션의 제목과 출처를 한 줄로 정리
- 형식: "섹션 제목 - 출처1, 출처2, ..."
- "출처:" 라인에 있는 출처명을 그대로 사용
- 출처가 없는 섹션은 제외
- 번호 없이, 줄바꿈으로 구분
- 설명이나 부연 없이 목록만 출력

예시:
AI 도입으로 인한 생산성 향상 수치 - Klarna Press Release, Amazon Q Announcement, GitHub Blog
빅테크 기업 주가 추이 - Nasdaq, Economic Times, Forbes"""

                ref_response = self.call_gemini(ref_prompt, model_type='2.5-flash')
                info.references = ref_response.strip()
                self.log(f'참고자료: {len(info.references)}자')
            else:
                self.log('리서치 데이터 없음 - 참고자료 스킵', 'warning')
                info.references = ''
        except Exception as e:
            self.log(f'참고자료 생성 실패: {e}', 'warning')
            info.references = ''

        # ===== 4단계: 썸네일 프롬프트 생성 =====
        self.update_progress(85, '썸네일 프롬프트 생성 중...')
        self.raise_if_cancelled()

        intro_narrations = [s['narration'] for s in scene_info_list[:5]]
        intro_text = ' '.join(intro_narrations)[:500]

        # 프로젝트 설정 정보 수집
        context_notes = []
        if self.project.character:
            char = self.project.character
            char_desc = char.character_prompt or char.name
            context_notes.append(f"채널 캐릭터: {char_desc} (참조 이미지 별도 제공됨 - 썸네일에 반드시 포함)")
        if self.project.image_style and self.project.image_style.style_prompt:
            context_notes.append(f"이미지 스타일: {self.project.image_style.style_prompt}")
        if self.project.thumbnail_style:
            ts = self.project.thumbnail_style
            if ts.description:
                context_notes.append(f"썸네일 스타일: {ts.description}")

        context_section = ""
        if context_notes:
            context_section = "\n프로젝트 설정:\n" + "\n".join(f"- {n}" for n in context_notes) + "\n"

        thumb_prompt = f"""YouTube 썸네일 이미지 생성 프롬프트를 영어로 작성해주세요.

영상 제목: {info.title}
영상 시작 내용: {intro_text}
{context_section}
요구사항:
1. 클릭을 유도하는 강렬한 이미지
2. 한글 텍스트 10자 이내 포함
3. 영상 주제와 관련된 시각적 요소
4. 감정: 충격, 호기심, 긴박감 중 택1

프롬프트만 출력 (설명 없이, 색상 지정 없이)
주의: "8k resolution", "photorealistic", "hyper-realistic", "dramatic lighting", "high contrast" 같은 상투적 수식어 사용 금지. 구체적인 장면 묘사에 집중할 것.:"""

        try:
            thumb_response = self.call_gemini(thumb_prompt)
            info.thumbnail_prompt = thumb_response.strip()
            self.log(f'썸네일 프롬프트: {len(info.thumbnail_prompt)}자')
        except Exception as e:
            self.log(f'썸네일 프롬프트 생성 실패: {e}', 'warning')
            info.thumbnail_prompt = f"""YouTube thumbnail for a Korean video.

Main visual: dramatic scene related to the video topic
Korean text: '{info.title[:10] if info.title else "영상"}'
Style: clickbait youtube thumbnail, high contrast, dramatic lighting
Emotion: shock, curiosity

Technical: 1280x720, clean composition, mobile-friendly text size"""

        # ===== 저장 =====
        self.update_progress(95, '저장 중...')
        info.save()
        self.log('업로드 정보 저장 완료')
