import subprocess
import re
import base64
import zipfile
import io
from pathlib import Path
from django.conf import settings
from django.core.files.base import ContentFile
from .base import BaseStepService
from apps.pipeline.models import Scene


class VideoComposerService(BaseStepService):
    """영상 편집 서비스 (TTS + 자막 + 합성)

    핵심 원리:
    - TTS 입력: narration_tts (한글 발음)
    - 자막 텍스트: narration (원본 숫자)
    - SRT: 타이밍 정보만 사용, 텍스트는 무시!

    매핑:
      narration:     "10조원."  "2025년"       (2단어)
      narration_tts: "십조원."  "이천이십오년"   (2단어)
      SRT:           [0.0-0.6]  [0.86-1.56]    (2개 타이밍)
    """

    agent_name = 'video_composer'

    # ASS 자막 헤더
    ASS_HEADER = """[Script Info]
Title: Scene Subtitles
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,AppleSDGothicNeo-Bold,75,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,4,4,10,2,10,10,50,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    def execute(self):
        self.update_progress(5, '씬 로딩 중...')
        self.log('영상 편집 시작')

        # DB에서 씬 가져오기
        scenes = list(self.project.scenes.all().order_by('scene_number'))

        if not scenes:
            raise ValueError('씬이 없습니다.')

        total = len(scenes)
        self.log(f'총 {total}개 씬 로드')

        # 음성 프리셋 확인
        voice = self.project.voice
        if voice:
            self.log(f'TTS 음성: {voice.name}')
        else:
            self.log('TTS 음성 설정 없음 - 기본 설정 사용')

        # 1. ASS 자막 생성 (SRT 타이밍 + narration 원본)
        self.update_progress(10, 'ASS 자막 생성 중...')
        self._generate_ass_subtitles(scenes)

        # 2. 클립 생성 (이미지/영상 + 오디오 + 자막)
        self.update_progress(30, '씬 클립 생성 중...')
        self._generate_clips(scenes)

        # 3. 최종 합치기
        self.update_progress(80, '최종 영상 합성 중...')
        self._concat_clips(scenes)

        # 4. 전체 자막 SRT 생성
        self.update_progress(95, '전체 자막 파일 생성 중...')
        self._merge_full_subtitles(scenes)

        self.log('영상 편집 완료', 'result')
        self.update_progress(100, '완료')

    # ===========================================
    # SRT 파싱 및 자막 생성
    # ===========================================

    def _parse_srt_timings(self, srt_content: str) -> list:
        """SRT에서 타이밍만 추출 (텍스트 무시!)"""
        pattern = r'(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})'
        timings = []

        for match in re.finditer(pattern, srt_content):
            start = self._srt_time_to_seconds(match.group(1))
            end = self._srt_time_to_seconds(match.group(2))
            timings.append({'start': start, 'end': end})

        return timings

    def _srt_time_to_seconds(self, time_str: str) -> float:
        """SRT 시간 문자열을 초로 변환"""
        time_str = time_str.replace(',', '.')
        h, m, s = time_str.split(':')
        return int(h) * 3600 + int(m) * 60 + float(s)

    def _map_timings_to_narration(self, srt_timings: list, narration: str) -> list:
        """narration 단어에 SRT 타이밍 1:1 매핑"""
        words = narration.split()
        result = []

        for i, word in enumerate(words):
            if i < len(srt_timings):
                result.append({
                    'word': word,
                    'start': srt_timings[i]['start'],
                    'end': srt_timings[i]['end']
                })
            else:
                # 타이밍이 부족하면 마지막 타이밍 재사용
                if result:
                    last_end = result[-1]['end']
                    result.append({
                        'word': word,
                        'start': last_end,
                        'end': last_end + 0.5
                    })

        return result

    def _highlight_numbers(self, text: str) -> str:
        """숫자 하이라이팅 (노란색)"""
        patterns = [
            (r'(\d+\.?\d*만\s*명)', r'{\\c&H00FFFF&}\1{\\c&HFFFFFF&}'),
            (r'(\d+\.?\d*만\s*원)', r'{\\c&H00FFFF&}\1{\\c&HFFFFFF&}'),
            (r'(\d+,?\d*억)', r'{\\c&H00FFFF&}\1{\\c&HFFFFFF&}'),
            (r'(\d+\.?\d*조\s*원?)', r'{\\c&H00FFFF&}\1{\\c&HFFFFFF&}'),
            (r'(\d+\.?\d*%)', r'{\\c&H00FFFF&}\1{\\c&HFFFFFF&}'),
            (r'(\d{4}년)', r'{\\c&H00FFFF&}\1{\\c&HFFFFFF&}'),
            (r'(\d+년)', r'{\\c&H00FFFF&}\1{\\c&HFFFFFF&}'),
        ]
        for pattern, replacement in patterns:
            text = re.sub(pattern, replacement, text)
        return text

    def _format_ass_time(self, seconds: float) -> str:
        """초를 ASS 시간 형식으로 변환"""
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = seconds % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    def _generate_ass_subtitles(self, scenes: list):
        """씬별 ASS 자막 생성 (SRT 타이밍 + narration 원본)"""
        for scene in scenes:
            scene_num = scene.scene_number

            # SRT 파일이 없으면 스킵
            if not scene.subtitle_file:
                self.log(f'씬 {scene_num} SRT 없음 - 자막 생성 스킵')
                continue

            narration = scene.narration
            if not narration:
                continue

            try:
                # SRT 읽기
                with open(scene.subtitle_file.path, 'r', encoding='utf-8') as f:
                    srt_content = f.read()

                # 타이밍 추출
                timings = self._parse_srt_timings(srt_content)

                if not timings:
                    self.log(f'씬 {scene_num} SRT 타이밍 없음', 'warning')
                    continue

                # narration 단어에 타이밍 매핑
                word_timings = self._map_timings_to_narration(timings, narration)

                # 문장 단위로 그룹화 (마침표/물음표/느낌표에서 끊기)
                sentences = self._group_words_to_sentences(word_timings)

                # ASS 파일 생성
                ass_content = self.ASS_HEADER
                for s in sentences:
                    highlighted = self._highlight_numbers(s['text'])
                    start = self._format_ass_time(s['start'])
                    end = self._format_ass_time(s['end'])
                    ass_content += f"Dialogue: 0,{start},{end},Default,,0,0,0,,{{\\pos(960,980)}}{highlighted}\n"

                # 저장
                ass_path = Path(settings.MEDIA_ROOT) / 'projects' / 'subtitles' / f'{self.project.pk}'
                ass_path.mkdir(parents=True, exist_ok=True)
                ass_file = ass_path / f'scene_{scene_num:02d}.ass'

                with open(ass_file, 'w', encoding='utf-8') as f:
                    f.write(ass_content)

                self.log(f'씬 {scene_num} ASS 자막 생성 완료')

            except Exception as e:
                self.log(f'씬 {scene_num} ASS 생성 실패: {str(e)[:50]}', 'error')

    def _group_words_to_sentences(self, word_timings: list, max_chars: int = 25) -> list:
        """단어를 자막 단위로 그룹화 (최대 글자수 제한)

        규칙:
        1. 최대 max_chars 글자 이하로 유지
        2. 마침표/물음표/느낌표에서 우선 끊기
        3. 쉼표에서 차선으로 끊기
        4. 글자수 초과 시 강제로 끊기
        """
        sentences = []
        current = []
        current_len = 0

        for wt in word_timings:
            word = wt['word']
            word_len = len(word)

            # 현재 그룹에 추가했을 때 길이 (공백 포함)
            new_len = current_len + word_len + (1 if current else 0)

            # 글자수 초과 시 현재 그룹 저장
            if current and new_len > max_chars:
                text = ' '.join(w['word'] for w in current)
                sentences.append({
                    'text': text,
                    'start': current[0]['start'],
                    'end': current[-1]['end']
                })
                current = []
                current_len = 0

            # 단어 추가
            current.append(wt)
            current_len += word_len + (1 if current_len > 0 else 0)

            # 마침표/물음표/느낌표로 끝나면 끊기 (소수점 제외)
            if re.search(r'(?<!\d)[.?!]$', word):
                text = ' '.join(w['word'] for w in current)
                sentences.append({
                    'text': text,
                    'start': current[0]['start'],
                    'end': current[-1]['end']
                })
                current = []
                current_len = 0
            # 쉼표로 끝나고 이미 15자 이상이면 끊기
            elif word.endswith(',') and current_len >= 15:
                text = ' '.join(w['word'] for w in current)
                sentences.append({
                    'text': text,
                    'start': current[0]['start'],
                    'end': current[-1]['end']
                })
                current = []
                current_len = 0

        # 마지막 남은 단어들
        if current:
            text = ' '.join(w['word'] for w in current)
            sentences.append({
                'text': text,
                'start': current[0]['start'],
                'end': current[-1]['end']
            })

        return sentences

    # ===========================================
    # 클립 생성
    # ===========================================

    def _generate_clips(self, scenes: list):
        """씬별 클립 생성 (이미지/영상 + 오디오 + ASS 자막)"""
        total = len(scenes)

        for i, scene in enumerate(scenes):
            scene_num = scene.scene_number

            progress = 30 + int((i / total) * 50)
            self.update_progress(progress, f'클립 생성 중 ({scene_num}/{total})...')

            # 오디오 없으면 스킵
            if not scene.audio:
                self.log(f'씬 {scene_num} 오디오 없음 - 스킵')
                continue

            # 입력 파일 결정 (동영상 or 이미지)
            if scene.video:
                input_file = scene.video.path
                is_video = True
            elif scene.image:
                input_file = scene.image.path
                is_video = False
            else:
                self.log(f'씬 {scene_num} 이미지/영상 없음 - 스킵')
                continue

            # 클립 경로
            clip_path = Path(settings.MEDIA_ROOT) / 'temp_clips' / f'{self.project.pk}_{scene_num:02d}.mp4'
            clip_path.parent.mkdir(parents=True, exist_ok=True)

            # 이미 있으면 스킵
            if clip_path.exists():
                continue

            # ASS 자막 경로
            ass_path = Path(settings.MEDIA_ROOT) / 'projects' / 'subtitles' / f'{self.project.pk}' / f'scene_{scene_num:02d}.ass'

            # 오디오 길이 확인
            audio_duration = self._get_audio_duration(scene.audio.path)

            try:
                if is_video:
                    # 동영상 씬: 480p → 1080p 업스케일
                    self._create_clip_from_video(
                        input_file, scene.audio.path, ass_path if ass_path.exists() else None,
                        clip_path, audio_duration
                    )
                else:
                    # 이미지 씬: zoompan 효과
                    self._create_clip_from_image(
                        input_file, scene.audio.path, ass_path if ass_path.exists() else None,
                        clip_path, audio_duration, scene_num
                    )

                self.log(f'씬 {scene_num} 클립 생성 완료')

            except Exception as e:
                self.log(f'씬 {scene_num} 클립 생성 실패: {str(e)[:50]}', 'error')

    def _get_audio_duration(self, audio_path: str) -> float:
        """오디오 길이 확인"""
        try:
            result = subprocess.run(
                ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                 '-of', 'csv=p=0', audio_path],
                capture_output=True, text=True, timeout=10
            )
            return float(result.stdout.strip())
        except:
            return 10.0

    def _create_clip_from_video(self, video_path, audio_path, ass_path, output_path, duration):
        """동영상 씬 클립 생성 (480p → 1080p 업스케일)"""
        filter_complex = "scale=1920:1080:flags=lanczos,format=yuv420p"
        if ass_path:
            filter_complex += f",ass={ass_path}"

        cmd = [
            'ffmpeg', '-y',
            '-i', video_path,
            '-i', audio_path,
            '-filter_complex', f'[0:v]{filter_complex}[v]',
            '-map', '[v]', '-map', '1:a',
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '18',
            '-c:a', 'aac', '-b:a', '192k',
            '-t', str(duration),
            str(output_path)
        ]

        subprocess.run(cmd, capture_output=True, timeout=120)

    def _create_clip_from_image(self, image_path, audio_path, ass_path, output_path, duration, scene_num):
        """이미지 씬 클립 생성 (zoompan 효과)"""
        import math
        frames = math.ceil(duration * 30)

        # zoompan 효과 (씬 번호에 따라 다른 효과)
        effects = [
            "z='min(zoom+0.0003,1.15)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",  # zoom in
            "z='if(lte(zoom,1.0),1.15,max(1.001,zoom-0.0003))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",  # zoom out
            "z='1.1':x='if(lte(on,1),0,min(x+1,iw-iw/zoom))':y='ih/2-(ih/zoom/2)'",  # pan left
            "z='1.1':x='if(lte(on,1),iw-iw/zoom,max(x-1,0))':y='ih/2-(ih/zoom/2)'"   # pan right
        ]
        effect = effects[scene_num % len(effects)]

        filter_complex = f"scale=8000:-1,zoompan={effect}:d={frames}:s=1920x1080:fps=30,format=yuv420p"
        if ass_path:
            filter_complex += f",ass={ass_path}"

        cmd = [
            'ffmpeg', '-y',
            '-loop', '1', '-t', str(duration), '-i', image_path,
            '-i', audio_path,
            '-filter_complex', f'[0:v]{filter_complex}[v]',
            '-map', '[v]', '-map', '1:a',
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '18',
            '-c:a', 'aac', '-b:a', '192k',
            '-t', str(duration),
            str(output_path)
        ]

        subprocess.run(cmd, capture_output=True, timeout=180)

    # ===========================================
    # 최종 합치기
    # ===========================================

    def _concat_clips(self, scenes: list):
        """클립들을 하나로 합치기"""
        clips_dir = Path(settings.MEDIA_ROOT) / 'temp_clips'

        # concat 리스트 파일 생성 (scene 순서대로!)
        list_file = clips_dir / f'{self.project.pk}_concat.txt'
        clip_count = 0

        with open(list_file, 'w') as f:
            for scene in scenes:
                clip_path = clips_dir / f'{self.project.pk}_{scene.scene_number:02d}.mp4'
                if clip_path.exists():
                    f.write(f"file '{clip_path}'\n")
                    clip_count += 1

        if clip_count == 0:
            self.log('합칠 클립이 없습니다', 'error')
            return

        # 출력 경로
        output_path = clips_dir / f'{self.project.pk}_final.mp4'

        # FFmpeg concat (오디오 재인코딩 필수!)
        cmd = [
            'ffmpeg', '-y',
            '-f', 'concat',
            '-safe', '0',
            '-i', str(list_file),
            '-c:v', 'copy',
            '-c:a', 'aac', '-b:a', '192k',
            '-movflags', '+faststart',
            str(output_path)
        ]

        try:
            subprocess.run(cmd, capture_output=True, timeout=600)

            # 프로젝트에 저장
            with open(output_path, 'rb') as f:
                self.project.final_video.save('final.mp4', ContentFile(f.read()), save=True)

            self.log(f'최종 영상 생성 완료 ({clip_count}개 클립)')
        except Exception as e:
            self.log(f'최종 영상 합성 실패: {str(e)[:50]}', 'error')

    def _merge_full_subtitles(self, scenes: list):
        """전체 자막 SRT 파일 생성 (YouTube 업로드용)"""
        total_time = 0
        srt_index = 1
        srt_content = []

        for scene in scenes:
            narration = scene.narration
            duration = self._get_audio_duration(scene.audio.path) if scene.audio else (scene.duration or 10)

            if not narration:
                total_time += duration
                continue

            # 씬별 SRT가 있으면 그걸 사용 (타이밍 정확)
            if scene.subtitle_file:
                try:
                    with open(scene.subtitle_file.path, 'r', encoding='utf-8') as f:
                        srt_data = f.read()

                    timings = self._parse_srt_timings(srt_data)
                    word_timings = self._map_timings_to_narration(timings, narration)
                    sentences = self._group_words_to_sentences(word_timings)

                    for s in sentences:
                        start_time = self._format_srt_full_time(total_time + s['start'])
                        end_time = self._format_srt_full_time(total_time + s['end'])

                        srt_content.append(f"{srt_index}")
                        srt_content.append(f"{start_time} --> {end_time}")
                        srt_content.append(s['text'])
                        srt_content.append("")
                        srt_index += 1

                except Exception as e:
                    # 실패하면 씬 전체를 하나의 자막으로
                    start_time = self._format_srt_full_time(total_time)
                    end_time = self._format_srt_full_time(total_time + duration)
                    srt_content.append(f"{srt_index}")
                    srt_content.append(f"{start_time} --> {end_time}")
                    srt_content.append(narration)
                    srt_content.append("")
                    srt_index += 1
            else:
                # SRT 없으면 씬 전체를 하나의 자막으로
                start_time = self._format_srt_full_time(total_time)
                end_time = self._format_srt_full_time(total_time + duration)
                srt_content.append(f"{srt_index}")
                srt_content.append(f"{start_time} --> {end_time}")
                srt_content.append(narration)
                srt_content.append("")
                srt_index += 1

            total_time += duration

        # 파일로 저장
        srt_text = '\n'.join(srt_content)
        self.project.full_subtitles.save('full_subtitles.srt', ContentFile(srt_text.encode('utf-8')), save=True)
        self.log(f'전체 자막 생성 완료 ({srt_index - 1}개 항목)')

    def _format_srt_full_time(self, seconds: float) -> str:
        """초를 SRT 시간 형식으로 변환"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
