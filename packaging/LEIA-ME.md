# LEGIS — pacote completo de instalação

Instala o LEGIS (Sistema de Gestão Parlamentar) num servidor **Debian 12 ou 13** limpo, ou no **Ubuntu 24.04**. O servidor precisa de internet durante a instalação.

## Instalar

No servidor, como administrador, dentro desta pasta:

```bash
sudo bash instalar-legis.sh
```

É automático: não pergunta nada. Instala o LEGIS, a rádio da Câmara, a transcrição e as legendas, e a IA local se a máquina tiver 8 GB de RAM. A cidade é escolhida no primeiro acesso ao painel do presidente, e os vereadores vêm do TSE.

Para escolher tudo na instalação, rode `sudo bash instalar-legis.sh --perguntar`. Também dá para passar as respostas, por exemplo `sudo UF=ma MUNICIPIO=Tuntum NOME=tuntum TS_AUTHKEY=tskey-auth-... bash instalar-legis.sh`:

| Opção | Para quê |
|---|---|
| Estado e município | o LEGIS busca no TSE os vereadores eleitos em 2024 (nome, partido e foto) e no IBGE a população. Pode deixar em branco: o painel do presidente pergunta a cidade no primeiro acesso |
| Nome curto | endereço `https://NOME.local` na rede da Câmara (o nome da máquina passa a ser esse) |
| Rádio da Câmara | rádio própria: toca a programação e entra sozinha com a sessão ao vivo |
| Transcrição e legendas | transcreve os discursos e legenda a transmissão (cerca de 600 MB) |
| IA local | rascunho da ata, perguntas e assistente do cidadão, no próprio servidor (8 GB de RAM) |
| Chave do Tailscale | opcional: publica na internet em `https://NOME.<sua-tailnet>.ts.net` sem abrir portas |

Leva alguns minutos. Transcrição, IA e publicação na internet terminam em segundo plano: acompanhe com `sudo legis estado`.

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
