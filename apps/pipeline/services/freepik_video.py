"""Freepik 스톡 영상 서비스

매 N번째 씬마다 AI 생성 이미지 대신 Freepik 스톡 영상으로 교체.
나레이션/TTS/자막은 유지하고 비주얼만 스톡 영상으로 변경.

흐름:
1. LLM(Flash)으로 씬에서 검색 키워드 2개 추출
2. Freepik API로 키워드별 영상 목록 수집 (무료만)
3. LLM이 나레이션과 가장 어울리는 영상 선택
4. 다운로드 및 저장 (사용한 ID 추적하여 중복 방지)

Freepik API 응답 구조:
- 검색: data[].{id, name, duration("HH:MM:SS"), premium(0|1), quality, thumbnails, previews}
- 다운로드: data.{url, filename} (무료 영상만, premium=1은 403)
"""

import os
import re
import time
import subprocess
import tempfile
import requests
from django.core.files.base import ContentFile
from .base import BaseStepService
from apps.pipeline.models import Scene


class FreepikVideoService(BaseStepService):
    """Freepik 스톡 영상 서비스"""

    agent_name = 'freepik_video'

    FREEPIK_API_BASE = 'https://api.freepik.com/v1'
    MAX_RETRIES = 3
    MAX_CANDIDATES = 15  # LLM에 넘길 최대 후보 수

    def execute(self):
        interval = self.project.freepik_interval
        if not interval or interval <= 0:
            self.log('스톡 영상 간격이 0 - 스킵')
            self.update_progress(100, '스킵 (간격 미설정)')
            return

        # API 키 확인
        api_key = self.get_freepik_key()
        self.log('Freepik API 키 확인 완료')

        # 전체 씬 로드
        scenes = list(self.project.scenes.all().order_by('scene_number'))
        if not scenes:
            raise ValueError('씬이 없습니다.')

        total = len(scenes)
        self.log(f'총 {total}개 씬, 매 {interval}번째 씬에 스톡 영상 삽입')

        # 대상 씬 선별 (2번째부터 시작, 매 interval씬마다: 2, 2+interval, 2+2*interval...)
        target_scenes = [s for s in scenes if s.scene_number >= 2 and (s.scene_number - 2) % interval == 0]
        # 이미 stock_video 있는 씬 제외
        target_scenes = [s for s in target_scenes if not s.stock_video]

        if not target_scenes:
            self.log('처리할 씬 없음 (모두 완료되었거나 대상 없음)')
            self.update_progress(100, '완료')
            return

        self.log(f'대상 씬 {len(target_scenes)}개: {[s.scene_number for s in target_scenes]}')

        # 이미 사용한 영상 ID 수집 (이 프로젝트 내 중복 방지)
        used_ids = set(
            self.project.scenes
            .exclude(stock_video_id='')
            .values_list('stock_video_id', flat=True)
        )
        if used_ids:
            self.log(f'이미 사용된 영상 ID {len(used_ids)}개')

        success_count = 0
        error_count = 0

        for i, scene in enumerate(target_scenes):
            self.raise_if_cancelled()

            progress = int((i / len(target_scenes)) * 90) + 5
            self.update_progress(progress, f'씬 {scene.scene_number} 처리 중...')

            try:
                result = self._process_scene(scene, api_key, used_ids)
                if result:
                    success_count += 1
                    self.log(f'씬 {scene.scene_number} 스톡 영상 저장 완료')
                else:
                    error_count += 1
                    self.log(f'씬 {scene.scene_number} 적합한 영상 없음 - 스킵', 'warning')
            except Exception as e:
                error_count += 1
                self.log(f'씬 {scene.scene_number} 실패: {str(e)[:100]}', 'error')

            # API rate limit 방지
            time.sleep(0.5)

        self.log(f'완료: 성공 {success_count}, 실패 {error_count}', 'result')
        self.update_progress(100, f'완료 ({success_count}개 성공)')

        if error_count > success_count and success_count == 0:
            raise Exception(f'모든 씬 실패 ({error_count}개)')

    def _process_scene(self, scene, api_key: str, used_ids: set) -> bool:
        """단일 씬 처리: 키워드 추출 → 검색 → 선택 → 다운로드"""

        # 1. LLM으로 키워드 2개 추출
        keywords = self._extract_keywords(scene)
        if not keywords:
            self.log(f'씬 {scene.scene_number} 키워드 추출 실패', 'warning')
            return False

        self.log(f'씬 {scene.scene_number} 키워드: {keywords}')

        # 2. 키워드별 검색, 후보 수집 (무료만, 중복 제외)
        candidates = []
        seen_ids = set()
        for kw in keywords:
            results = self._search_videos(kw, api_key, used_ids)
            for v in results:
                vid = str(v['id'])
                if vid not in seen_ids:
                    seen_ids.add(vid)
                    candidates.append(v)

        if not candidates:
            self.log(f'씬 {scene.scene_number} 검색 결과 없음', 'warning')
            return False

        # LLM에 넘길 후보 수 제한
        candidates = candidates[:self.MAX_CANDIDATES]
        self.log(f'씬 {scene.scene_number} 후보 {len(candidates)}개')

        # 3. LLM으로 최적 영상 선택
        selected = self._select_best_video(scene, candidates)
        if not selected:
            selected = candidates[0]
            self.log(f'씬 {scene.scene_number} LLM 선택 실패, 첫 번째 후보 사용', 'warning')

        self.log(f'씬 {scene.scene_number} 선택: ID={selected["id"]}, "{selected["name"]}"')

        # 4. 다운로드
        video_data = self._download_video(selected['id'], api_key)
        if not video_data:
            return False

        # 5. 저장
        video_id = str(selected['id'])
        filename = f'stock_{self.project.pk}_{scene.scene_number:02d}.mp4'
        scene.stock_video.save(filename, ContentFile(video_data), save=False)
        Scene.objects.filter(pk=scene.pk).update(
            stock_video=scene.stock_video.name,
            stock_video_id=video_id
        )

        # 사용 ID 추적
        used_ids.add(video_id)
        return True

    # =============================================
    # 1. LLM 키워드 추출
    # =============================================

    def _extract_keywords(self, scene) -> list:
        """LLM으로 스톡 영상 검색 키워드 2개 추출"""
        narration = scene.narration or ''
        image_prompt = scene.image_prompt or ''

        if not narration and not image_prompt:
            return []

        prompt = f"""You are a stock video search expert.
Extract exactly 2 English search keywords for finding a stock video that visually matches this scene.

Scene narration (Korean): {narration}
Visual description: {image_prompt}

Rules:
- Each keyword should be 2-4 words
- Focus on the main visual subject (people, objects, places, actions)
- Make keywords suitable for stock video search (generic, not too specific)
- Do NOT include style/mood words (dramatic, cinematic, etc.)

Return exactly 2 lines, one keyword per line. No numbering, no explanation."""

        try:
            response = self.call_gemini(prompt, model_type='2.5-flash')
            lines = [line.strip() for line in response.strip().split('\n') if line.strip()]
            # 숫자/기호 접두사 제거
            cleaned = []
            for line in lines:
                line = re.sub(r'^[\d\.\)\-\*]+\s*', '', line).strip()
                if line:
                    cleaned.append(line)
            return cleaned[:2]
        except Exception as e:
            self.log(f'키워드 추출 오류: {str(e)[:80]}', 'error')
            return []

    # =============================================
    # 2. Freepik 검색
    # =============================================

    def _search_videos(self, keyword: str, api_key: str, used_ids: set) -> list:
        """Freepik API로 영상 검색, 무료만 필터, 사용된 ID 제외"""
        headers = {
            'x-freepik-api-key': api_key,
            'Accept': 'application/json',
        }

        params = {
            'term': keyword,
        }

        for attempt in range(self.MAX_RETRIES):
            try:
                resp = requests.get(
                    f'{self.FREEPIK_API_BASE}/videos',
                    headers=headers,
                    params=params,
                    timeout=15
                )

                if resp.status_code == 429:
                    wait = 2 * (attempt + 1)
                    self.log(f'API 429 - {wait}초 대기', 'warning')
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                data = resp.json()

                videos = data.get('data', [])
                results = []
                for v in videos:
                    vid = str(v.get('id', ''))
                    if not vid or vid in used_ids:
                        continue
                    results.append({
                        'id': vid,
                        'name': v.get('name', ''),
                        'duration': v.get('duration', ''),
                        'quality': v.get('quality', ''),
                    })

                return results

            except Exception as e:
                self.log(f'검색 오류 ({keyword}): {str(e)[:80]}', 'error')
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(1)
                    continue

        return []

    # =============================================
    # 3. LLM 영상 선택
    # =============================================

    def _select_best_video(self, scene, candidates: list) -> dict:
        """LLM으로 나레이션과 가장 어울리는 영상 선택"""
        if len(candidates) == 1:
            return candidates[0]

        # 후보 목록 텍스트
        candidate_text = ''
        for i, c in enumerate(candidates):
            candidate_text += f'{i + 1}. {c["name"]}'
            if c.get('duration'):
                candidate_text += f' ({c["duration"]})'
            candidate_text += '\n'

        prompt = f"""Choose the best stock video for this scene.

Scene narration (Korean): {scene.narration}
Visual description: {scene.image_prompt}

Available videos:
{candidate_text}
IMPORTANT: Return ONLY a single number from 1 to {len(candidates)}. Nothing else."""

        try:
            response = self.call_gemini(prompt, model_type='2.5-flash')
            # 숫자 추출
            numbers = re.findall(r'\d+', response.strip())
            if numbers:
                idx = int(numbers[0]) - 1
                if 0 <= idx < len(candidates):
                    return candidates[idx]
        except Exception as e:
            self.log(f'영상 선택 오류: {str(e)[:80]}', 'error')

        return None

    # =============================================
    # 4. 다운로드
    # =============================================

    def _download_video(self, video_id: str, api_key: str) -> bytes:
        """Freepik 영상 다운로드 (웹사이트 쿠키 우선, 실패 시 API 폴백)"""
        # 1. 웹사이트 쿠키로 무료 다운로드 시도
        cookie = self.get_freepik_cookie()
        wallet_id = self.get_freepik_wallet()
        if cookie and wallet_id:
            result = self._download_via_website(video_id, cookie, wallet_id)
            if result:
                return result
            self.log('웹사이트 다운로드 실패, API 폴백', 'warning')

        # 2. API 다운로드 (유료)
        return self._download_via_api(video_id, api_key)

    def _download_via_website(self, video_id: str, cookie: str, wallet_id: str) -> bytes:
        """Freepik 웹사이트 쿠키로 무료 다운로드 (프리미엄 구독 활용)"""
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
            'Accept': '*/*',
            'Cookie': cookie,
            'Referer': 'https://kr.freepik.com/',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
        }

        for attempt in range(self.MAX_RETRIES):
            try:
                url = f'https://kr.freepik.com/api/video/{video_id}/download?walletId={wallet_id}'
                resp = requests.get(url, headers=headers, timeout=15)

                if resp.status_code in (401, 403):
                    self.log(f'웹사이트 다운로드 {resp.status_code} (쿠키 만료?) - 설정에서 쿠키 갱신 필요', 'warning')
                    return None

                if resp.status_code == 429:
                    wait = 3 * (attempt + 1)
                    self.log(f'웹사이트 429 - {wait}초 대기', 'warning')
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                download_info = resp.json()

                download_url = download_info.get('url')
                filename = download_info.get('filename', '')
                if not download_url:
                    self.log(f'웹사이트 다운로드 URL 없음', 'warning')
                    return None

                self.log(f'웹사이트 다운로드 시작: {filename}')

                # 파일 다운로드
                file_resp = requests.get(download_url, timeout=180)
                file_resp.raise_for_status()
                raw_data = file_resp.content
                raw_mb = len(raw_data) / 1024 / 1024
                self.log(f'웹사이트 다운로드 완료: {raw_mb:.1f}MB (무료)')

                # .mov면 H.264 변환, .mp4면 그대로
                if filename.lower().endswith('.mov') or raw_mb > 50:
                    return self._convert_to_h264(raw_data)
                return raw_data

            except Exception as e:
                self.log(f'웹사이트 다운로드 오류 (시도 {attempt + 1}): {str(e)[:80]}', 'error')
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(2)
                    continue

        return None

    def _download_via_api(self, video_id: str, api_key: str) -> bytes:
        """Freepik API로 영상 다운로드 (유료)"""
        headers = {
            'x-freepik-api-key': api_key,
            'Accept': 'application/json',
        }

        for attempt in range(self.MAX_RETRIES):
            try:
                resp = requests.get(
                    f'{self.FREEPIK_API_BASE}/videos/{video_id}/download',
                    headers=headers,
                    timeout=15
                )

                if resp.status_code == 429:
                    time.sleep(2 * (attempt + 1))
                    continue

                if resp.status_code == 403:
                    self.log(f'API 다운로드 권한 없음 (ID={video_id})', 'error')
                    return None

                resp.raise_for_status()
                download_info = resp.json()

                download_url = download_info.get('data', {}).get('url')
                if not download_url:
                    self.log(f'API 다운로드 URL 없음 (ID={video_id})', 'error')
                    return None

                # 파일 다운로드
                file_resp = requests.get(download_url, timeout=180)
                file_resp.raise_for_status()
                raw_data = file_resp.content
                raw_mb = len(raw_data) / 1024 / 1024
                self.log(f'API 다운로드 완료: {raw_mb:.1f}MB (유료)')

                # H.264 변환
                file_data = self._convert_to_h264(raw_data)
                return file_data

            except Exception as e:
                self.log(f'API 다운로드 오류 (시도 {attempt + 1}): {str(e)[:80]}', 'error')
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(1)
                    continue

        return None

    def _convert_to_h264(self, raw_data: bytes) -> bytes:
        """ffmpeg로 H.264 1080p MP4 변환"""
        with tempfile.NamedTemporaryFile(suffix='.mov', delete=False) as tmp_in:
            tmp_in.write(raw_data)
            tmp_in_path = tmp_in.name

        tmp_out_path = tmp_in_path.replace('.mov', '_converted.mp4')
        try:
            cmd = [
                'ffmpeg', '-y', '-i', tmp_in_path,
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
                '-vf', 'scale=-2:1080',
                '-c:a', 'aac', '-b:a', '128k',
                '-movflags', '+faststart',
                tmp_out_path
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=120)
            if result.returncode != 0:
                self.log(f'ffmpeg 변환 실패: {result.stderr[-200:].decode(errors="ignore")}', 'error')
                return raw_data  # 변환 실패 시 원본 반환

            with open(tmp_out_path, 'rb') as f:
                converted = f.read()

            orig_mb = len(raw_data) / 1024 / 1024
            conv_mb = len(converted) / 1024 / 1024
            self.log(f'H.264 변환: {orig_mb:.1f}MB → {conv_mb:.1f}MB')
            return converted
        finally:
            for p in [tmp_in_path, tmp_out_path]:
                if os.path.exists(p):
                    os.unlink(p)
