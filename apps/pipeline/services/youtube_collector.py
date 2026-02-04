"""YouTube 자막/댓글 수집 서비스

yt-dlp를 사용하여 YouTube 영상의 자막과 댓글을 수집합니다.
API 키 불필요, 할당량 제한 없음.
"""

import re
import json
import tempfile
from datetime import datetime
from pathlib import Path
from .base import BaseStepService
from apps.pipeline.models import Research, YouTubeComment


class YouTubeCollectorService(BaseStepService):
    """YouTube 자막/댓글 수집 서비스

    yt-dlp 기반:
    - 자막: 한국어 우선, 자동생성 자막 지원
    - 댓글: 인기순(top) 200개
    """

    agent_name = 'youtube_collector'

    def execute(self):
        self.update_progress(5, 'YouTube URL 확인 중...')

        # Topic에서 URL 가져오기
        if not hasattr(self.project, 'topic') or not self.project.topic:
            raise ValueError('주제가 설정되지 않았습니다.')

        url = self.project.topic.url
        if not url:
            raise ValueError('YouTube URL이 없습니다. 주제에 YouTube URL을 입력해주세요.')

        # URL 검증
        video_id = self._extract_video_id(url)
        if not video_id:
            raise ValueError(f'유효한 YouTube URL이 아닙니다: {url}')

        self.log(f'YouTube 영상 ID: {video_id}')

        # Topic에 video_id 저장
        self.project.topic.video_id = video_id
        self.project.topic.save(update_fields=['video_id'])

        # 1. 자막 수집
        self.update_progress(10, '자막 수집 중...')
        transcript, lang, is_auto = self._fetch_subtitles(url)

        if transcript:
            self.log(f'자막 수집 완료: {len(transcript)}자 ({lang}, 자동생성: {is_auto})')
        else:
            self.log('자막 없음', 'warning')

        # 2. 댓글 수집
        self.update_progress(50, '댓글 수집 중...')
        comments = self._fetch_comments(url)
        self.log(f'댓글 수집 완료: {len(comments)}개')

        # 3. Research에 저장
        self.update_progress(90, '저장 중...')
        self._save_to_research(transcript, lang, is_auto, comments)

        self.update_progress(100, f'수집 완료: 자막 {len(transcript) if transcript else 0}자, 댓글 {len(comments)}개')

    def _extract_video_id(self, url: str) -> str:
        """URL에서 video_id 추출"""
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

    def _fetch_subtitles(self, url: str) -> tuple:
        """yt-dlp로 자막 수집

        Returns:
            tuple: (transcript_text, language, is_auto_generated)
        """
        import yt_dlp

        # 임시 디렉토리 사용
        with tempfile.TemporaryDirectory() as tmpdir:
            ydl_opts = {
                'skip_download': True,
                'writesubtitles': True,
                'writeautomaticsub': True,
                'subtitleslangs': ['ko', 'en'],
                'subtitlesformat': 'json3',
                'outtmpl': f'{tmpdir}/%(id)s.%(ext)s',
                'quiet': True,
                'no_warnings': True,
            }

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)

                    # 자막 정보 확인
                    subtitles = info.get('subtitles', {})
                    auto_captions = info.get('automatic_captions', {})

                    # 한국어 우선, 없으면 영어
                    transcript_text = ''
                    language = ''
                    is_auto = False

                    # 수동 자막 먼저 시도
                    for lang_code in ['ko', 'en']:
                        if lang_code in subtitles:
                            # 수동 자막 다운로드
                            ydl_opts['subtitleslangs'] = [lang_code]
                            with yt_dlp.YoutubeDL(ydl_opts) as ydl2:
                                ydl2.download([url])

                            # json3 파일 읽기
                            sub_files = list(Path(tmpdir).glob(f'*.{lang_code}.json3'))
                            if sub_files:
                                transcript_text = self._parse_json3_subtitle(sub_files[0])
                                language = lang_code
                                is_auto = False
                                break

                    # 수동 자막 없으면 자동 자막 시도
                    if not transcript_text:
                        for lang_code in ['ko', 'en']:
                            if lang_code in auto_captions:
                                ydl_opts['subtitleslangs'] = [lang_code]
                                ydl_opts['writesubtitles'] = False
                                ydl_opts['writeautomaticsub'] = True
                                with yt_dlp.YoutubeDL(ydl_opts) as ydl2:
                                    ydl2.download([url])

                                sub_files = list(Path(tmpdir).glob(f'*.{lang_code}.json3'))
                                if sub_files:
                                    transcript_text = self._parse_json3_subtitle(sub_files[0])
                                    language = lang_code
                                    is_auto = True
                                    break

                    return transcript_text, language, is_auto

            except Exception as e:
                self.log(f'자막 수집 오류: {str(e)}', 'error')
                return '', '', False

    def _parse_json3_subtitle(self, filepath: Path) -> str:
        """json3 형식 자막 파일 파싱"""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)

            texts = []
            events = data.get('events', [])

            for event in events:
                # 각 이벤트에서 텍스트 추출
                segs = event.get('segs', [])
                for seg in segs:
                    text = seg.get('utf8', '').strip()
                    if text and text != '\n':
                        texts.append(text)

            # 중복 제거 및 정리
            full_text = ' '.join(texts)
            # 연속 공백 정리
            full_text = re.sub(r'\s+', ' ', full_text).strip()
            return full_text

        except Exception as e:
            self.log(f'자막 파싱 오류: {str(e)}', 'warning')
            return ''

    def _fetch_comments(self, url: str, max_comments: int = 200) -> list:
        """yt-dlp로 댓글 수집 (인기순 200개)

        Returns:
            list: 댓글 목록 [{id, author, text, like_count, reply_count, timestamp}, ...]
        """
        import yt_dlp

        ydl_opts = {
            'skip_download': True,
            'getcomments': True,
            'extractor_args': {
                'youtube': {
                    'comment_sort': ['top'],  # 인기순
                    'max_comments': [str(max_comments), str(max_comments), '0', '0'],  # 댓글 200개, 답글 0개
                }
            },
            'quiet': True,
            'no_warnings': True,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                raw_comments = info.get('comments', [])

            comments = []
            for c in raw_comments[:max_comments]:
                comments.append({
                    'id': c.get('id', ''),
                    'author': c.get('author', 'Unknown'),
                    'text': c.get('text', ''),
                    'like_count': c.get('like_count', 0) or 0,
                    'reply_count': 0,  # yt-dlp에서 직접 답글 수 제공 안함
                    'timestamp': c.get('timestamp'),
                })

            return comments

        except Exception as e:
            self.log(f'댓글 수집 오류: {str(e)}', 'warning')
            return []

    def _save_to_research(self, transcript: str, lang: str, is_auto: bool, comments: list):
        """Research 모델에 저장"""
        # Research 생성/업데이트
        research, created = Research.objects.update_or_create(
            project=self.project,
            defaults={
                'source_url': self.project.topic.url,
                'topic': self.project.topic.title,
                'transcript': transcript,
                'transcript_language': lang,
                'transcript_is_auto': is_auto,
            }
        )

        # 기존 댓글 삭제 후 새로 저장
        research.youtube_comments.all().delete()

        comment_objs = []
        for c in comments:
            published_at = None
            if c.get('timestamp'):
                try:
                    published_at = datetime.fromtimestamp(c['timestamp'])
                except:
                    pass

            comment_objs.append(YouTubeComment(
                research=research,
                comment_id=c.get('id', ''),
                author=c.get('author', 'Unknown'),
                text=c.get('text', ''),
                like_count=c.get('like_count', 0),
                reply_count=c.get('reply_count', 0),
                published_at=published_at,
            ))

        if comment_objs:
            YouTubeComment.objects.bulk_create(comment_objs)

        self.log(f'저장 완료: Research ID={research.pk}, 댓글 {len(comment_objs)}개')
