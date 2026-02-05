from .base import BaseStepService
from apps.pipeline.models import Scene


class TTSConverterService(BaseStepService):
    """TTS 텍스트 변환 서비스 (숫자→한글)"""

    agent_name = 'tts_converter'

    def execute(self):
        self.update_progress(5, '씬 로딩 중...')
        self.log('TTS 텍스트 변환 시작')

        # narration_tts가 비어있는 씬만 처리
        all_scenes = self.project.scenes.filter(
            narration__isnull=False
        ).exclude(narration='').order_by('scene_number')

        scenes = list(all_scenes.filter(narration_tts='') | all_scenes.filter(narration_tts__isnull=True))
        scenes.sort(key=lambda x: x.scene_number)

        if not scenes:
            self.log('변환할 씬이 없습니다 (모든 씬에 TTS 텍스트 존재)')
            self.update_progress(100, '완료: 변환할 씬 없음')
            return

        total = len(scenes)
        all_count = all_scenes.count()
        self.log(f'변환 대상: {total}개 / 전체: {all_count}개')

        batch_size = 10
        processed = 0

        for i in range(0, total, batch_size):
            batch = scenes[i:i + batch_size]
            batch_end = min(i + batch_size, total)

            progress = 10 + int((i / total) * 85)
            self.update_progress(progress, f'변환 중 ({i+1}-{batch_end}/{total})...')

            results = self._convert_batch(batch)

            for j, scene in enumerate(batch):
                if j < len(results):
                    scene.narration_tts = results[j]
                    scene.save(update_fields=['narration_tts'])
                    processed += 1

        self.log(f'TTS 변환 완료', 'result', {'processed': processed})
        self.update_progress(100, f'완료: {processed}개 변환')

    def _convert_batch(self, batch: list) -> list:
        """배치로 TTS 변환"""
        scenes_text = ""
        for scene in batch:
            scenes_text += f"[씬 {scene.scene_number}]\n{scene.narration}\n\n"

        prompt = f"""다음 텍스트들을 TTS(음성 합성)용으로 변환해주세요.

## 핵심 규칙
**원본 띄어쓰기를 그대로 유지! 단어 개수가 절대 변하면 안 됨!**

## 변환 예시
- 원본에 띄어쓰기 없으면 → 변환 후에도 없음: "100달러" → "백달러", "1시간" → "한시간"
- 원본에 띄어쓰기 있으면 → 변환 후에도 있음: "100 달러" → "백 달러", "1 시간" → "한 시간"
- 연도: "2030년" → "이천삼십년"
- 금액: "2천만원" → "이천만원", "2천만 원" → "이천만 원"
- 퍼센트: "50%" → "오십퍼센트"
- 날짜: "1월1일" → "일월일일", "1월 1일" → "일월 일일"

## 금지사항
- 띄어쓰기 추가 금지
- 띄어쓰기 삭제 금지
- 단어 분리/병합 금지

## 원문
{scenes_text}

## 출력 형식
[씬 N]
변환된 텍스트

[씬 N]
변환된 텍스트
..."""

        response = self.call_gemini(prompt)
        return self._parse_results(response, len(batch), batch)

    def _parse_results(self, response: str, expected: int, batch: list) -> list:
        """응답 파싱"""
        import re
        results = []

        pattern = r'\[씬\s*\d+\]\s*\n'
        parts = re.split(pattern, response)

        for part in parts[1:]:
            text = part.strip()
            if '\n\n' in text:
                text = text.split('\n\n')[0]
            text = text.strip()
            if text:
                results.append(text)

        # 부족하면 원본으로 채움
        while len(results) < expected:
            idx = len(results)
            if idx < len(batch):
                results.append(batch[idx].narration)

        return results[:expected]
