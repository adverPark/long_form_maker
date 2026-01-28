#!/usr/bin/env python3
"""
연속으로 비정상적으로 빠른 자막 감지
- 30번 씬처럼 오디오가 잘리고 WhisperX가 환각을 일으킨 경우 탐지
- 개별 짧은 단어가 아닌, 연속으로 빠른 단어들을 찾음
"""
import os
import re
from pathlib import Path

def parse_srt_time(time_str):
    """SRT 시간을 초 단위로 변환"""
    time_str = time_str.replace(',', '.')
    match = re.match(r'(\d{2}):(\d{2}):(\d{2})\.(\d{3})', time_str)
    if match:
        h, m, s, ms = match.groups()
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
    return 0

def analyze_srt(filepath):
    """SRT 파일 분석 - 연속 빠른 자막 감지"""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # SRT 블록 파싱
    blocks = re.split(r'\n\n+', content.strip())
    entries = []
    
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) >= 3:
            timing_match = re.match(
                r'(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})',
                lines[1]
            )
            if timing_match:
                start = parse_srt_time(timing_match.group(1))
                end = parse_srt_time(timing_match.group(2))
                text = ' '.join(lines[2:]).strip()
                duration = end - start
                entries.append({
                    'start': start,
                    'end': end,
                    'duration': duration,
                    'text': text
                })
    
    if not entries:
        return None
    
    # 연속 빠른 자막 찾기 (3개 이상 연속으로 0.15초 미만)
    consecutive_fast = []
    current_streak = []
    
    for i, entry in enumerate(entries):
        if entry['duration'] < 0.15:  # 150ms 미만
            current_streak.append((i + 1, entry))
        else:
            if len(current_streak) >= 3:  # 3개 이상 연속
                consecutive_fast.append(current_streak.copy())
            current_streak = []
    
    # 마지막 스트릭 체크
    if len(current_streak) >= 3:
        consecutive_fast.append(current_streak)
    
    # 후반부에 집중된 빠른 자막 (오디오 잘림 징후)
    # 마지막 30%에서 평균 속도가 전반부보다 3배 이상 빠르면 의심
    if len(entries) >= 5:
        split_point = int(len(entries) * 0.7)
        first_part = entries[:split_point]
        last_part = entries[split_point:]
        
        if first_part and last_part:
            first_avg = sum(e['duration'] for e in first_part) / len(first_part)
            last_avg = sum(e['duration'] for e in last_part) / len(last_part)
            
            if first_avg > 0 and last_avg > 0 and first_avg / last_avg > 3:
                return {
                    'type': 'tail_compression',
                    'total_entries': len(entries),
                    'first_avg_ms': int(first_avg * 1000),
                    'last_avg_ms': int(last_avg * 1000),
                    'ratio': round(first_avg / last_avg, 1),
                    'last_entries': [(i + split_point + 1, e['text'], int(e['duration'] * 1000)) 
                                     for i, e in enumerate(last_part)]
                }
    
    if consecutive_fast:
        return {
            'type': 'consecutive_fast',
            'streaks': [
                {
                    'count': len(streak),
                    'entries': [(idx, e['text'], int(e['duration'] * 1000)) for idx, e in streak]
                }
                for streak in consecutive_fast
            ]
        }
    
    return None

# 미디어 디렉토리 스캔
media_root = Path('/home/adver/long_form_site/media/projects')
srt_files = list(media_root.glob('**/subtitles/*.srt'))

print(f"총 {len(srt_files)}개 SRT 파일 분석 중...\n")

problematic = []

for srt_path in sorted(srt_files):
    result = analyze_srt(srt_path)
    if result:
        # 프로젝트 번호와 씬 번호 추출
        parts = str(srt_path).split('/')
        project_id = parts[-3] if len(parts) >= 3 else 'unknown'
        scene_name = srt_path.stem
        
        problematic.append({
            'path': str(srt_path),
            'project': project_id,
            'scene': scene_name,
            'result': result
        })

print(f"🚨 문제 발견: {len(problematic)}개 파일\n")
print("=" * 80)

for item in problematic:
    print(f"\n📁 프로젝트 {item['project']} / {item['scene']}")
    print(f"   경로: {item['path']}")
    
    result = item['result']
    if result['type'] == 'tail_compression':
        print(f"   ⚠️  후반부 압축 감지!")
        print(f"      전체 {result['total_entries']}개 단어")
        print(f"      전반부 평균: {result['first_avg_ms']}ms")
        print(f"      후반부 평균: {result['last_avg_ms']}ms (전반부의 1/{result['ratio']})")
        print(f"      후반부 단어:")
        for idx, text, dur in result['last_entries'][:5]:
            print(f"         #{idx}: {text} → {dur}ms")
        if len(result['last_entries']) > 5:
            print(f"         ... 외 {len(result['last_entries']) - 5}개")
    
    elif result['type'] == 'consecutive_fast':
        for streak in result['streaks']:
            print(f"   ⚠️  연속 {streak['count']}개 빠른 자막:")
            for idx, text, dur in streak['entries'][:5]:
                print(f"      #{idx}: {text} → {dur}ms")
            if len(streak['entries']) > 5:
                print(f"      ... 외 {len(streak['entries']) - 5}개")

print("\n" + "=" * 80)
