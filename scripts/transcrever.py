"""Transcreve um áudio com faster-whisper e imprime o resultado em JSON (uma linha).

Roda no Python do ambiente criado por scripts/instalar_transcricao.sh; o servidor chama este script
num processo separado para continuar só com a biblioteca padrão.

    dados/whisper/bin/python scripts/transcrever.py --modelo small fala.webm
"""
import argparse
import json
import os
import shutil
import subprocess


def carregar_audio(arquivo: str):
    """Decodifica com o ffmpeg do sistema (16 kHz, mono). Evita incompatibilidades do PyAV com WebM do navegador."""
    import numpy as np
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return arquivo  # sem ffmpeg, o faster-whisper tenta decodificar sozinho
    bruto = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-i", arquivo, "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                           capture_output=True, check=True).stdout
    return np.frombuffer(bruto, np.int16).astype(np.float32) / 32768.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("arquivo")
    ap.add_argument("--modelo", default="small")
    ap.add_argument("--idioma", default="pt")
    ap.add_argument("--threads", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    a = ap.parse_args()
    from faster_whisper import WhisperModel  # só existe no ambiente da transcrição

    modelo = WhisperModel(a.modelo, device="cpu", compute_type="int8", cpu_threads=a.threads)
    segs, info = modelo.transcribe(carregar_audio(a.arquivo), language=a.idioma or None, vad_filter=True, beam_size=5,
                                   initial_prompt="Sessão da Câmara Municipal. Senhor presidente, nobres vereadores.")
    saida = [{"ini": round(s.start, 2), "fim": round(s.end, 2), "texto": s.text.strip()} for s in segs]
    print(json.dumps({"texto": " ".join(s["texto"] for s in saida), "segmentos": saida, "idioma": info.language,
                      "motor": f"faster-whisper {a.modelo}"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
