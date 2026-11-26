# Parlatório Legis on-premise (servidor Debian na Câmara)

O servidor fica dentro da rede do plenário. Assim a sessão não depende da internet, e o painel do presidente controla a mesa de som diretamente: concessão da palavra abre o microfone, tempo esgotado, congelamento e corte por tumulto fecham, tudo sem o operador tocar na mesa.

```
 celulares dos vereadores ─┐
 telão (TV/projetor) ──────┤
 computador do presidente ─┼── rede do plenário ── servidor Parlatório (Debian)
 mesa de som (rede) ───────┘                              │
                                                          └── internet (só a transmissão ao vivo)
```

## Equipamento

- Um computador ou mini-PC com Debian 12 ou 13: 2 núcleos, 4 GB de RAM e 64 GB de disco bastam. Para a transcrição dos discursos, use 8 GB de RAM.
- Cabo de rede no mesmo switch ou roteador da mesa de som. Dê ao servidor um **IP fixo** (reserva de DHCP no roteador).
- Para a mesa: ela precisa ter porta de rede (veja a lista abaixo). Mesa analógica não aceita comando; nesse caso use uma central de relés com API HTTP.

## Instalar

No servidor Debian, como root:

```bash
apt-get install -y git
git clone https://github.com/gilliardleda2018/parlatorio_legis.git /opt/parlatorio_legis
bash /opt/parlatorio_legis/deploy/local/instalar.sh
```

O instalador:
- dá ao servidor o nome `parlatorio` na rede, para os aparelhos abrirem `https://parlatorio.local`;
- cria o serviço e o certificado HTTPS da própria Câmara;
- liga o backup diário.

Opções: `NOME=outro` muda o nome, `TRANSCRICAO=1` instala a transcrição local.

Depois, em cada aparelho (celulares dos vereadores, computador do presidente, telão), uma única vez:
1. Abra `http://IP-DO-SERVIDOR/certificado` e instale o certificado da Câmara. Na página inicial há as instruções para Android, iPhone e Windows.
2. Use `https://parlatorio.local` (ou `https://IP-DO-SERVIDOR`).

## Mesa de som

Em **Painel do presidente → Configurações → Mesa de som**, escolha o modelo, informe o IP da mesa e use **Testar canal** para abrir e fechar cada canal antes da primeira sessão. Depois, em **Plenário → Editar**, ligue cada vereador ao canal do microfone dele e indique o canal da tribuna.

| Mesa | Como o painel controla |
|---|---|
| Behringer X32 / Midas M32 | OSC (UDP 10023) |
| Behringer X Air (XR12/16/18) / Midas MR | OSC (UDP 10024) |
| Behringer WING | OSC (UDP 2223), mudo do canal |
| Yamaha TF / DM3 / DM7 / QL / CL / Rivage | protocolo remoto Yamaha (TCP 49280), botão ON do canal |
| Allen & Heath Qu / dLive / Avantis | MIDI por rede (TCP 51325; dLive 51328), mudo |
| Allen & Heath SQ | MIDI por rede (TCP 51325), mudo por NRPN |
| Soundcraft Ui12 / Ui16 / Ui24R | WebSocket (porta 80), mudo |
| Outra mesa com OSC | endereço e valores configuráveis |
| Qualquer outra (Bitfocus Companion, Node-RED) | ponte HTTP: o painel envia `{canais:[{canal, aberto}]}` |
| Relés (Shelly, ESP32) ou central de conferência com API | uma URL por canal com `{canal}` e `{acao}` |

Os protocolos de Yamaha, Allen & Heath e Soundcraft seguem a documentação dos fabricantes e foram testados com equipamentos simulados; confira no equipamento real com **Testar canal**. Se a marca não estiver na lista, o [Bitfocus Companion](https://bitfocus.io/companion) (gratuito) controla centenas de mesas e sistemas de conferência e recebe os comandos do painel pela ponte HTTP.

**Durante a sessão, o painel manda:** conceder a palavra abre o canal do orador (ou da tribuna). Aparte e questão de ordem abrem o canal de quem interrompe. Tempo esgotado, pausa, congelamento e corte por tumulto fecham. Os microfones de bancada abrem e fecham um a um, e há o botão "fechar todos". A cada 5 s o estado é reenviado, então se alguém mexer na mesa à mão o painel corrige.

## Transmissão ao vivo

O computador do presidente capta a saída da mesa e o servidor local manda o áudio para a rádio (AzuraCast ou Icecast) em **Configurações → Transmissão ao vivo**. É a única parte que usa a internet. Se a internet cair, a sessão continua normalmente; só a transmissão para.

## Atualizar

```bash
bash /opt/parlatorio_legis/deploy/local/instalar.sh
```

Baixa a versão nova e reinicia o serviço; dados e configuração são mantidos.
