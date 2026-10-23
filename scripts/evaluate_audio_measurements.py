"""Compare the same ten known speech samples across direct 16 kHz PCM and the 8 kHz μ-law call path."""
import argparse, asyncio, csv, json, re, subprocess, sys
from pathlib import Path
import edge_tts
import numpy as np
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.audio.resample import resample_pcm16
from app.audio.ulaw import encode_pcm16_to_ulaw, decode_ulaw_to_pcm16
from app.audio.turn_detector import TurnDetector
from app.audio.vad import EnergyVAD
from app.services.stt_service import STTService
from app.services.tts_service import TTSService
PHRASES = [
    "Yes, I am the account holder.", "No, the internet issue is not resolved.",
    "My internet keeps dropping.", "The technician arrived on time.",
    "The technician did not arrive on time.", "I would rate the technician five out of five.",
    "I would recommend SkyFiber nine out of ten.", "There are no problems now.",
    "Please call me tomorrow morning.", "No other feedback, thank you.",
]
async def pcm16_source(text, tts):
    stream = edge_tts.Communicate(text=text, voice=tts.voice)
    chunks = [item["data"] async for item in stream.stream() if item.get("type") == "audio"]
    command = [tts.ffmpeg,"-hide_banner","-loglevel","error","-i","pipe:0","-f","s16le","-acodec","pcm_s16le","-ac","1","-ar","16000","pipe:1"]
    result = await asyncio.to_thread(subprocess.run,command,input=b"".join(chunks),stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
    if result.returncode or not result.stdout: raise RuntimeError(result.stderr.decode(errors="replace"))
    return result.stdout[:-1] if len(result.stdout)%2 else result.stdout
def transcript16(stt, pcm):
    audio=np.frombuffer(pcm,dtype="<i2").astype(np.float32)/32768.0
    segments,_=stt._get_model().transcribe(audio,language="en",beam_size=1,vad_filter=False,condition_on_previous_text=False)
    return " ".join(s.text.strip() for s in segments if s.text.strip()).strip()
def wer(ref,hyp):
    numbers={str(i):word for i,word in enumerate(("zero","one","two","three","four","five","six","seven","eight","nine","ten"))}
    def words(text):
        return [numbers.get(x,x) for x in re.findall(r"[a-z0-9]+",text.lower())]
    a,b=words(ref),words(hyp); row=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        nxt=[i]
        for j,y in enumerate(b,1): nxt.append(min(nxt[-1]+1,row[j]+1,row[j-1]+(x!=y)))
        row=nxt
    return row[-1]/max(1,len(a))
def replay(pcm,silence_ms):
    pcm8=resample_pcm16(pcm,original_rate=16000,target_rate=8000); ulaw=encode_pcm16_to_ulaw(pcm8)
    vad=EnergyVAD(threshold=500); detector=TurnDetector(silence_duration_ms=silence_ms,frame_duration_ms=20); count=0
    for i in range(0,len(ulaw)-159,160):
        if detector.process_frame(vad.is_speech(decode_ulaw_to_pcm16(ulaw[i:i+160]))): count+=1
    quiet=b"\xff"*160
    for _ in range(75):
        if detector.process_frame(vad.is_speech(decode_ulaw_to_pcm16(quiet))): count+=1
    return count
async def run(out):
    tts,stt=TTSService(),STTService(); rows=[]; sources=[]
    for n,phrase in enumerate(PHRASES,1):
        pcm=await pcm16_source(phrase,tts); sources.append(pcm)
        pcm8=resample_pcm16(pcm,original_rate=16000,target_rate=8000)
        tel=stt.transcribe_ulaw(encode_pcm16_to_ulaw(pcm8),language="en")["text"]
        direct=await asyncio.to_thread(transcript16,stt,pcm)
        rows.append({"utterance":n,"reference":phrase,"transcript_8khz_ulaw":tel,"wer_8khz":round(wer(phrase,tel),4),"transcript_16khz_pcm":direct,"wer_16khz":round(wer(phrase,direct),4)})
        print(json.dumps(rows[-1],ensure_ascii=False))
    out.parent.mkdir(parents=True,exist_ok=True)
    with out.open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
    print(json.dumps({"output":str(out),"n":len(rows),"mean_wer_8khz":round(float(np.mean([r["wer_8khz"] for r in rows])),4),"mean_wer_16khz":round(float(np.mean([r["wer_16khz"] for r in rows])),4),"turn_detector_counts":{"500ms":[replay(x,500) for x in sources],"1000ms":[replay(x,1000) for x in sources]}},indent=2))
def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--output",type=Path,default=ROOT/"tmp"/"audio_measurements.csv"); asyncio.run(run(p.parse_args().output))
if __name__=="__main__": main()
