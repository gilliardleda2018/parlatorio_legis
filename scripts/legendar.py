"""Legendas ao vivo: processo permanente que mantém o modelo carregado e transcreve pedaços de áudio.

Protocolo (entrada padrão, binário): 4 bytes big-endian com o tamanho + o áudio (qualquer formato que o
ffmpeg leia, ex.: MP3 da transmissão). Saída: uma linha JSON por pedaço: {"texto": "..."}.
Roda no Python do ambiente da transcrição (faster-whisper); o servidor continua só com a biblioteca padrão.
"""
import argparse
import json
import os
import shutil
import struct
import subprocess
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--modelo", default="base")
    ap.add_argument("--idioma", default="pt")
    a = ap.parse_args()
    import numpy as np
    from faster_whisper import WhisperModel

    modelo = WhisperModel(a.modelo, device="cpu", compute_type="int8", cpu_threads=max(1, (os.cpu_count() or 2) // 2))
    ffmpeg = shutil.which("ffmpeg")
    print(json.dumps({"pronto": True}), flush=True)
    entrada = sys.stdin.buffer
    while True:
        cab = entrada.read(4)
        if len(cab) < 4:
            return
        dados = entrada.read(struct.unpack(">I", cab)[0])
        try:
            pcm = subprocess.run([ffmpeg, "-v", "quiet", "-nostdin", "-i", "pipe:0", "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                                 input=dados, capture_output=True, timeout=30).stdout
            audio = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768.0
            texto = ""
            if len(audio) > 8000 and float(np.abs(audio).mean()) > 0.002:  # silêncio não gera legenda
                segs, _ = modelo.transcribe(audio, language=a.idioma, vad_filter=True, beam_size=1,
                                            condition_on_previous_text=False)
                texto = " ".join(s.text.strip() for s in segs).strip()
            print(json.dumps({"texto": texto}, ensure_ascii=False), flush=True)
        except Exception as e:  # um pedaço ruim não derruba as legendas
            print(json.dumps({"texto": "", "erro": str(e)[:200]}), flush=True)


if __name__ == "__main__":
    main()
