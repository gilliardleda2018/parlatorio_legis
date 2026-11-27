# LEGIS — pacote completo de instalação

Instala o LEGIS (Sistema de Gestão Parlamentar) num servidor **Debian 12 ou 13** limpo, ou no **Ubuntu 24.04**. O servidor precisa de internet durante a instalação.

## Instalar

No servidor, como administrador, dentro desta pasta:

```bash
sudo python3 instalar-legis.py
```

O instalador verifica Docker e Compose, instala os componentes ausentes pelo APT e confirma que o Docker local responde. Depois instala o Legis e a rádio, inicia o serviço e verifica a resposta local. No final, informe a UF e o município para importar os vereadores eleitos de 2024 do TSE. O instalador também tenta obter as fotos e a população do IBGE. Jitsi, transcrição/legendas e IA local são provisionados em segundo plano conforme as opções e os recursos do servidor.

Se o Debian ainda não tiver Python, execute `sudo bash instalar-legis.sh`. As duas entradas aceitam `--sem-perguntar` para instalação em lote e `--perguntar` para configurar os componentes opcionais. Para preencher a cidade de antemão, use `sudo UF=MA MUNICIPIO=Tuntum NOME=tuntum python3 instalar-legis.py --sem-perguntar`.

| Opção | Para quê |
|---|---|
| Estado e município | o LEGIS busca no TSE os vereadores eleitos em 2024 (nome, partido e foto) e no IBGE a população. Pode preencher no final da instalação ou no primeiro acesso ao painel |
| Nome curto | endereço `https://NOME.local` na rede da Câmara (o nome da máquina passa a ser esse) |
| Transcrição e legendas | transcreve os discursos e legenda a transmissão (cerca de 600 MB) |
| IA local | rascunho da ata, perguntas e assistente do cidadão, no próprio servidor (8 GB de RAM) |
| Videoconferência (Jitsi) | servidor próprio da Câmara para a sessão híbrida (`JITSI=s`, padrão com 4 GB de RAM) |
| Chave do YouTube | `YOUTUBE_CHAVE=…`: o botão Transmitir já manda a sessão para o canal da Câmara |
| AzuraCast | instala o servidor de rádio, cria a estação Rádio da Câmara e configura o operador de transmissão do Legis. `AZURACAST=n` mantém a rádio Liquidsoap |
| Acesso remoto | informe `TS_AUTHKEY` ou autorize pelo link exibido. O instalador aplica o proxy automaticamente após a autorização |
| Publicação aberta | `TAILSCALE_PUBLICO=s` ativa o Funnel para o Legis quando a rede permite. O painel do AzuraCast fica privado no Tailscale |

Leva alguns minutos. Transcrição, IA e publicação na internet terminam em segundo plano: acompanhe com `sudo legis estado`.

## Rádio e acesso remoto

O painel do AzuraCast fica em `https://NOME.<sua-tailnet>.ts.net:8443`. O Legis fica no mesmo domínio, na porta HTTPS padrão. Para consultar os endereços use `sudo legis estado`. A senha inicial do AzuraCast é gerada por instalação. O administrador do servidor pode consultá-la com `sudo cat /etc/legis/azuracast/provisionamento.json`. Use a administração do AzuraCast para carregar programação. A rádio local do Legis continua disponível na rede da Câmara.

## Depois de instalar

1. `sudo legis`: mostra o endereço e o **PIN inicial do presidente**.
2. Em cada computador e celular da Câmara, abra `http://IP-DO-SERVIDOR/certificado` e instale o certificado (tira o aviso de conexão não segura).
3. Abra `https://NOME.local/presidente`, entre com o PIN inicial e crie um PIN próprio. Se a cidade não foi escolhida na instalação, aparece a caixa **De qual cidade é esta Câmara?**.
4. Em **Configurações**: brasão, contatos, mesa de som, rádio e **YouTube ao vivo** (cole a chave de transmissão do YouTube Studio).
5. Vereadores entram em `/parlamentar` com o PIN `0000` e trocam no primeiro acesso.

## Comandos

| Comando | O que faz |
|---|---|
| `sudo legis` | endereços, PIN inicial e o que já está pronto |
| `sudo legis estado` | serviços, rádio, transcrição, IA e internet |
| `sudo legis logs` | registro do serviço |
| `sudo legis reiniciar` | reinicia o LEGIS e a rádio |
| `sudo dpkg-reconfigure legis` | refaz as perguntas (por exemplo, para publicar na internet depois) |

**Atualizar:** rode o `instalar-legis.sh` de uma versão nova, ou `sudo apt install ./legis_<nova>_all.deb`. Dados e configuração são mantidos. Os dados ficam em `/var/lib/legis`, com backup diário.

Guia completo: https://github.com/gilliardleda2018/parlatorio_legis/blob/main/deploy/deb/README.md
