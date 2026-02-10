"""Freepik 스톡 영상 서비스 (Playwright 브라우저 자동화)

매 N번째 씬마다 AI 생성 이미지 대신 Freepik 스톡 영상으로 교체.
나레이션/TTS/자막은 유지하고 비주얼만 스톡 영상으로 변경.

흐름:
1. LLM(Flash)으로 씬에서 검색 키워드 2개 추출
2. Playwright 브라우저로 kr.freepik.com 검색 페이지 스크래핑
3. LLM이 나레이션과 가장 어울리는 영상 선택
4. 브라우저 컨텍스트에서 fetch()로 다운로드 (Firebase 토큰 자동 갱신)

기존 쿠키 기반 requests 방식 대비 장점:
- GR_TOKEN 만료 시 브라우저의 Firebase SDK가 자동 갱신
- API key 불필요 (검색도 브라우저로 수행)
"""

import os
import re
import time
import subprocess
import tempfile
import requests
from urllib.parse import quote_plus
from django.core.files.base import ContentFile
from .base import BaseStepService
from apps.pipeline.models import Scene


class FreepikVideoService(BaseStepService):
    """Freepik 스톡 영상 서비스"""

    agent_name = 'freepik_video'

    MAX_RETRIES = 3

    def execute(self):
        # Playwright sync API가 asyncio 루프를 생성하므로 Django ORM 호출 허용
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"

        interval = self.project.freepik_interval
        if not interval or interval <= 0:
            self.log('스톡 영상 간격이 0 - 스킵')
            self.update_progress(100, '스킵 (간격 미설정)')
            return

        # 전체 씬 로드
        scenes = list(self.project.scenes.all().order_by('scene_number'))
        if not scenes:
            raise ValueError('씬이 없습니다.')

        total = len(scenes)

        # 대상 씬 선별
        if interval == 1:
            # 100% 스톡: 모든 씬
            target_scenes = list(scenes)
            self.log(f'총 {total}개 씬, 100% 스톡 영상 모드')
        else:
            # N씬 간격: 2번째부터 시작 (2, 2+interval, 2+2*interval...)
            target_scenes = [s for s in scenes if s.scene_number >= 2 and (s.scene_number - 2) % interval == 0]
            self.log(f'총 {total}개 씬, 매 {interval}번째 씬에 스톡 영상 삽입')
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

        # 브라우저 시작
        pw, browser, context, page = self._start_browser()
        if not page:
            raise Exception('브라우저 시작 실패')

        success_count = 0
        error_count = 0

        try:
            for i, scene in enumerate(target_scenes):
                self.raise_if_cancelled()

                progress = int((i / len(target_scenes)) * 90) + 5
                self.update_progress(progress, f'씬 {scene.scene_number} 처리 중...')

                try:
                    result = self._process_scene(scene, page, used_ids)
                    if result:
                        success_count += 1
                        self.log(f'씬 {scene.scene_number} 스톡 영상 저장 완료')
                    else:
                        error_count += 1
                        self.log(f'씬 {scene.scene_number} 적합한 영상 없음 - 스킵', 'warning')
                except Exception as e:
                    error_count += 1
                    self.log(f'씬 {scene.scene_number} 실패: {str(e)[:100]}', 'error')

                # rate limit 방지 (다운로드 API 429 회피)
                time.sleep(3)
        finally:
            browser.close()
            pw.stop()
            self.log('브라우저 종료')

        self.log(f'완료: 성공 {success_count}, 실패 {error_count}', 'result')
        self.update_progress(100, f'완료 ({success_count}개 성공)')

        if error_count > success_count and success_count == 0:
            raise Exception(f'모든 씬 실패 ({error_count}개)')

    # =============================================
    # 브라우저 관리
    # =============================================

    def _start_browser(self):
        """Playwright 브라우저 시작 + 로그인 (이메일/비번 우선, 쿠키 fallback)"""
        from playwright.sync_api import sync_playwright

        wallet_id = self.get_freepik_wallet()
        if not wallet_id:
            self.log('Freepik walletId 미설정 - 설정에서 추가하세요', 'error')
            return None, None, None, None

        cookie_str = self.get_freepik_cookie()

        if not cookie_str:
            self.log('Freepik 쿠키 미설정 - 설정에서 추가하세요', 'error')
            return None, None, None, None

        self.log('브라우저 시작 중...')
        pw = sync_playwright().start()
        browser = pw.chromium.launch(
            headless=True,
            channel='chrome',
            args=['--disable-blink-features=AutomationControlled'],
        )

        context = browser.new_context(
            user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080},
            locale='ko-KR',
        )

        page = context.new_page()
        page.add_init_script('Object.defineProperty(navigator, "webdriver", {get: () => undefined});')

        # 쿠키 주입
        if cookie_str:
            cookies = self._parse_cookies(cookie_str)
            if cookies:
                context.add_cookies(cookies)
                self.log(f'쿠키 {len(cookies)}개 주입')

            try:
                page.goto('https://kr.freepik.com/', wait_until='domcontentloaded', timeout=20000)
                page.wait_for_timeout(2000)
                self.log('쿠키 기반 세션 활성화 완료')
            except Exception as e:
                self.log(f'세션 활성화 경고: {str(e)[:80]}', 'warning')

            return pw, browser, context, page

    def _login_with_credentials(self, page, email: str, password: str) -> bool:
        """Freepik 이메일/비밀번호 로그인"""
        try:
            self.log('Freepik 이메일 로그인 시도...')

            # 로그인 페이지 이동
            page.goto(
                'https://www.freepik.com/log-in?client_id=freepik&lang=en',
                wait_until='networkidle', timeout=20000
            )
            page.wait_for_timeout(2000)

            # "Continue with email" 클릭
            email_btn = page.get_by_text('Continue with email')
            if email_btn.count() == 0:
                self.log('Continue with email 버튼 없음', 'error')
                return False
            email_btn.click()
            page.wait_for_timeout(2000)

            # 이메일/비밀번호 입력
            email_input = page.locator('input[name="email"]')
            password_input = page.locator('input[name="password"]')

            if email_input.count() == 0 or password_input.count() == 0:
                self.log('이메일/비밀번호 입력란 없음', 'error')
                return False

            email_input.fill(email)
            password_input.fill(password)

            # "Stay logged in" 체크
            keep_signed = page.locator('input[name="keep-signed"]')
            if keep_signed.count() > 0 and not keep_signed.is_checked():
                keep_signed.check()

            # 로그인 버튼 클릭
            login_btn = page.get_by_role('button', name='Log in')
            if login_btn.count() == 0:
                # fallback: 텍스트로 찾기
                login_btn = page.locator('button:has-text("Log in")')
            login_btn.click()

            # 로그인 완료 대기 (URL 변경 또는 프로필 요소 등장)
            page.wait_for_timeout(5000)

            # 로그인 성공 확인: 로그인 페이지에서 벗어났는지
            current_url = page.url
            if 'log-in' in current_url:
                # 아직 로그인 페이지 → 에러 메시지 확인
                error_el = page.locator('[class*="error"], [class*="alert"], [role="alert"]')
                if error_el.count() > 0:
                    error_text = error_el.first.text_content().strip()[:100]
                    self.log(f'로그인 실패: {error_text}', 'error')
                else:
                    self.log('로그인 실패: 이메일/비밀번호 확인 필요', 'error')
                return False

            self.log('Freepik 로그인 성공')

            # kr.freepik.com으로 이동 (검색/다운로드용)
            page.goto('https://kr.freepik.com/', wait_until='domcontentloaded', timeout=20000)
            page.wait_for_timeout(2000)

            return True

        except Exception as e:
            self.log(f'로그인 오류: {str(e)[:100]}', 'error')
            return False

    def _parse_cookies(self, cookie_str: str) -> list:
        """'name=value; name2=value2' 형식의 쿠키를 Playwright 형식으로 변환"""
        cookies = []
        for pair in cookie_str.split(';'):
            pair = pair.strip()
            if not pair or '=' not in pair:
                continue
            name, value = pair.split('=', 1)
            name = name.strip()
            value = value.strip()
            if not name:
                continue
            cookies.append({
                'name': name,
                'value': value,
                'domain': '.freepik.com',
                'path': '/',
            })
        return cookies

    # =============================================
    # 씬 처리
    # =============================================

    def _process_scene(self, scene, page, used_ids: set) -> bool:
        """단일 씬 처리: 키워드 추출 → 검색 → 선택 → 다운로드"""

        # 1. LLM으로 키워드 2개 추출
        keywords = self._extract_keywords(scene)
        if not keywords:
            self.log(f'씬 {scene.scene_number} 키워드 추출 실패', 'warning')
            return False

        self.log(f'씬 {scene.scene_number} 키워드: {keywords}')

        # 2. 키워드별 검색, 후보 수집 (중복 제외)
        candidates = []
        seen_ids = set()
        for kw in keywords:
            results = self._search_videos_browser(page, kw, used_ids)
            for v in results:
                vid = str(v['id'])
                if vid not in seen_ids:
                    seen_ids.add(vid)
                    candidates.append(v)

        if not candidates:
            self.log(f'씬 {scene.scene_number} 검색 결과 없음', 'warning')
            return False

        self.log(f'씬 {scene.scene_number} 후보 {len(candidates)}개')

        # 3. LLM으로 최적 영상 선택
        selected = self._select_best_video(scene, candidates)
        if not selected:
            selected = candidates[0]
            self.log(f'씬 {scene.scene_number} LLM 선택 실패, 첫 번째 후보 사용', 'warning')

        self.log(f'씬 {scene.scene_number} 선택: ID={selected["id"]}, "{selected["name"]}"')

        # 4. 다운로드 (실패 시 다른 후보로 재시도)
        video_data = self._download_video_browser(page, selected['id'])
        if not video_data:
            # 선택된 영상 제외하고 나머지 후보로 순차 시도
            remaining = [c for c in candidates if c['id'] != selected['id']]
            for fallback in remaining[:3]:
                self.log(f'씬 {scene.scene_number} 대체 후보 시도: ID={fallback["id"]}, "{fallback["name"]}"')
                video_data = self._download_video_browser(page, fallback['id'])
                if video_data:
                    selected = fallback
                    break
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
    # 2. Playwright 브라우저 검색
    # =============================================

    def _search_videos_browser(self, page, keyword: str, used_ids: set) -> list:
        """Playwright로 kr.freepik.com 검색 페이지 스크래핑 (2페이지)"""
        encoded = quote_plus(keyword)
        all_videos = []

        for pg in range(1, 3):  # 1페이지, 2페이지
            url = f'https://kr.freepik.com/search?format=search&orientation=landscape&page={pg}&query={encoded}&type=video'

            try:
                page.goto(url, wait_until='networkidle', timeout=20000)
                page.wait_for_timeout(2000)

                videos = page.evaluate('''(usedIds) => {
                    const results = [];
                    const figures = document.querySelectorAll('figure');
                    for (const fig of figures) {
                        const link = fig.parentElement;
                        if (!link || link.tagName !== 'A') continue;
                        const href = link.href || '';

                        const idMatch = href.match(/_(\\d+)(?:#|$)/);
                        if (!idMatch) continue;
                        const videoId = idMatch[1];

                        if (usedIds.includes(videoId)) continue;

                        // 세로 영상 제외 (poster/src URL에 vertical 포함)
                        const vid = fig.querySelector('video');
                        const poster = vid ? (vid.poster || '') : '';
                        const src = vid ? (vid.querySelector('source')?.getAttribute('data-src') || '') : '';
                        if (poster.includes('/vertical/') || src.includes('/vertical/')) continue;

                        const img = fig.querySelector('img');
                        const name = img ? (img.alt || '') : '';

                        const header = fig.querySelector('header');
                        const durationDiv = header ? header.querySelector('div') : null;
                        const duration = durationDiv ? durationDiv.textContent.trim() : '';

                        results.push({
                            id: videoId,
                            name: name.substring(0, 100),
                            duration: duration,
                        });
                    }
                    return results;
                }''', list(used_ids))

                all_videos.extend(videos)

                if not videos:
                    break  # 결과 없으면 다음 페이지 스킵

            except Exception as e:
                self.log(f'검색 오류 ({keyword} p{pg}): {str(e)[:80]}', 'error')
                break

        if all_videos:
            self.log(f'검색 "{keyword}": {len(all_videos)}개 결과')
        else:
            self.log(f'검색 "{keyword}": 결과 없음', 'warning')

        return all_videos

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
    # 4. 브라우저 다운로드
    # =============================================

    def _download_video_browser(self, page, video_id: str) -> bytes:
        """브라우저 컨텍스트에서 fetch()로 다운로드 (Firebase 토큰 자동 갱신)"""
        wallet_id = self.get_freepik_wallet()
        if not wallet_id:
            self.log('Freepik walletId 미설정', 'error')
            return None

        for attempt in range(self.MAX_RETRIES):
            try:
                # 브라우저 내에서 fetch() 호출 - 쿠키/인증 자동 포함
                result = page.evaluate('''async ({videoId, walletId}) => {
                    try {
                        const resp = await fetch(
                            `/api/video/${videoId}/download?walletId=${walletId}`,
                            {credentials: 'include'}
                        );
                        if (!resp.ok) {
                            return {error: resp.status, message: resp.statusText};
                        }
                        const data = await resp.json();
                        return {url: data.url, filename: data.filename || ''};
                    } catch (e) {
                        return {error: -1, message: e.message};
                    }
                }''', {'videoId': video_id, 'walletId': wallet_id})

                if result.get('error'):
                    error_code = result['error']
                    error_msg = result.get('message', '')
                    self.log(f'다운로드 API 오류 {error_code}: {error_msg} (시도 {attempt + 1})', 'warning')

                    if error_code in (401, 403):
                        # 토큰 만료 → 페이지 새로고침으로 Firebase 토큰 갱신
                        self.log('토큰 만료 추정 - 페이지 새로고침으로 갱신 시도', 'warning')
                        page.goto('https://kr.freepik.com/', wait_until='domcontentloaded', timeout=15000)
                        page.wait_for_timeout(3000)
                        continue

                    if error_code == 429:
                        wait = 10 * (attempt + 1)
                        self.log(f'429 Rate limit - {wait}초 대기', 'warning')
                        time.sleep(wait)
                        continue

                    if attempt < self.MAX_RETRIES - 1:
                        time.sleep(2)
                        continue
                    return None

                download_url = result.get('url')
                filename = result.get('filename', '')
                if not download_url:
                    self.log('다운로드 URL 없음', 'warning')
                    return None

                self.log(f'다운로드 시작: {filename}')

                # CDN URL은 인증 불필요 - requests로 직접 다운로드
                file_resp = requests.get(download_url, timeout=180)
                file_resp.raise_for_status()
                raw_data = file_resp.content
                raw_mb = len(raw_data) / 1024 / 1024
                self.log(f'다운로드 완료: {raw_mb:.1f}MB')

                # .mov면 H.264 변환, .mp4면 그대로
                if filename.lower().endswith('.mov') or raw_mb > 50:
                    return self._convert_to_h264(raw_data)
                return raw_data

            except Exception as e:
                self.log(f'다운로드 오류 (시도 {attempt + 1}): {str(e)[:80]}', 'error')
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(2)
                    continue

        return None

    # =============================================
    # 5. 파일 변환
    # =============================================

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
