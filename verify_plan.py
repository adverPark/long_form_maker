import os, re
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings.production"
import django; django.setup()
from django.apps import apps

Scene = apps.get_model("pipeline", "Scene")

# 씬 1로 검증
s = Scene.objects.get(project_id=62, scene_number=1)
with open(s.subtitle_file.path, "r") as f:
    srt = f.read()

# SRT에서 단어+타이밍 추출
pattern = r'(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n(.+?)(?=\n|$)'
words = []
for m in re.finditer(pattern, srt):
    def pt(t):
        h,mi,r = t.split(':'); s,ms = r.split(',')
        return int(h)*3600+int(mi)*60+int(s)+int(ms)/1000
    words.append({'start': pt(m.group(1)), 'end': pt(m.group(2)), 'text': m.group(3).strip()})

# SRT 문장 경계 (마침표로 끝나는 단어)
srt_sents = []
current = []
for w in words:
    current.append(w)
    if re.search(r'[.?!]$', w['text']):
        srt_sents.append({
            'start': current[0]['start'],
            'end': current[-1]['end'],
            'text': ' '.join(x['text'] for x in current)
        })
        current = []
if current:
    srt_sents.append({
        'start': current[0]['start'],
        'end': current[-1]['end'],
        'text': ' '.join(x['text'] for x in current)
    })

# narration 문장 분리
narr_sents = [x.strip() for x in re.split(r'(?<=[.?!])\s+', s.narration.strip()) if x.strip()]

print(f"SRT 문장 수: {len(srt_sents)}")
print(f"narration 문장 수: {len(narr_sents)}")
print(f"일치: {len(srt_sents) == len(narr_sents)}")
print()

for i in range(max(len(srt_sents), len(narr_sents))):
    srt_s = srt_sents[i] if i < len(srt_sents) else None
    narr_s = narr_sents[i] if i < len(narr_sents) else None
    print(f"--- 문장 {i+1} ---")
    if srt_s:
        print(f"  SRT [{srt_s['start']:.2f}-{srt_s['end']:.2f}]: {srt_s['text']}")
    if narr_s:
        print(f"  표시: {narr_s}")
    print()
