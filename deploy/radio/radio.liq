# Rádio da Câmara (LEGIS): toca a programação e entra com a sessão ao vivo quando o presidente transmite.
# Gerado pelo pacote legis; para mudar a programação use o painel (Configurações → Rádio da Câmara).
settings.log.stdout := true
settings.log.file := false
settings.harbor.bind_addrs := ["127.0.0.1"]

# fora da sessão: vinhetas e músicas enviadas pelo painel, em ordem aleatória (recarrega quando a pasta muda)
programacao = playlist(id="programacao", mode="randomize", reload_mode="watch", "@DIR@")

# sessão ao vivo: o próprio LEGIS serve o áudio em /aovivo enquanto a transmissão está ligada
sessao = input.http(id="sessao", "http://127.0.0.1:@PORTA@/aovivo")

radio = fallback(id="sessao_ou_programacao", track_sensitive=false, [sessao, programacao, blank()])

# os ouvintes entram pelo LEGIS (https://<endereço>/radio.mp3), que repassa esta saída
output.harbor(%mp3(bitrate=128), id="radio", mount="radio.mp3", port=@RADIO_PORTA@, mksafe(radio))
