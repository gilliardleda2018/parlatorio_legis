# LEGIS — instalador .deb

Pacote para Debian 12/13 (e Ubuntu 24.04). Instala o LEGIS como serviço do sistema, já configurado para a Câmara.

## Instalar

```bash
sudo apt install ./legis_<versão>_all.deb
```

Instale **pelo terminal**, como acima. Abrir o arquivo com clique duplo (instalador gráfico) instala sem fazer as perguntas: o LEGIS começa com dados de exemplo e avisa. Nesse caso, rode depois `sudo dpkg-reconfigure legis`. As perguntas são feitas e os vereadores do município entram no lugar dos de exemplo; o banco de exemplo fica guardado em `/var/lib/legis/legis.db.exemplo-<data>`.

O `apt` pergunta:

| Pergunta | Para quê |
|---|---|
| Estado e município | busca no TSE os vereadores eleitos em 2024 (nome de urna, partido e foto) e no IBGE a população |
| Nome curto | endereço na internet: `https://<nome>.<sua-tailnet>.ts.net` |
| Rádio da Câmara | cada Câmara ganha a própria rádio no servidor: toca a programação e entra sozinha com a sessão ao vivo |
| Transcrição e legendas | transcreve os discursos e legenda a transmissão ao vivo (cerca de 600 MB) |
| IA local | rascunho da ata, perguntas sobre os discursos e assistente do cidadão, no próprio servidor (precisa do Docker e de 8 GB de RAM) |
| Chave do Tailscale (opcional) | publica na internet sem abrir portas no roteador; a chave é usada uma vez e não fica guardada |

Para instalar **sem perguntas**, por exemplo num script, numa imagem de máquina ou em várias câmaras:

```bash
cat <<'EOF' | sudo debconf-set-selections
legis legis/uf string ma
legis legis/municipio string Tuntum
legis legis/nome string tuntum
legis legis/radio boolean true
legis legis/transcricao boolean true
legis legis/ia boolean true
legis legis/tailscale_chave password tskey-auth-...
EOF
sudo DEBIAN_FRONTEND=noninteractive apt install -y ./legis_<versão>_all.deb
```

O `apt` termina em poucos minutos. Transcrição, IA e publicação na internet são instaladas **em segundo plano**. Acompanhe com `sudo legis estado`.

## Depois de instalar

```bash
sudo legis            # endereços, PIN inicial do presidente e o que já está pronto
```

Siga o checklist de [deploy/cidade/README.md](../cidade/README.md#depois-de-instalar-checklist): certificado nos aparelhos, troca do PIN do presidente, brasão e contatos, mesa de som, transmissão e troca dos PINs dos vereadores.

## Rádio da Câmara

Cada instalação tem a **própria rádio**, sem servidor central nem custo mensal:

- **Programação:** em Configurações → Rádio da Câmara, envie vinhetas, avisos e músicas (MP3, OGG, M4A, WAV ou FLAC). A rádio toca em ordem aleatória. Sem arquivos, fica em silêncio fora das sessões.
- **Sessão ao vivo:** quando o presidente clica em 📡 Transmitir, a rádio entra com a sessão em poucos segundos e volta à programação ao encerrar. Não é preciso configurar nada.
- **Para ouvir:** `https://<endereço do LEGIS>/radio.mp3`, na rede da Câmara e, com o Tailscale, na internet. A faixa "Rádio da Câmara" aparece nas páginas públicas.
- **Por dentro:** o Liquidsoap (serviço `legis-radio`) monta a rádio, e o LEGIS a entrega aos ouvintes no mesmo endereço e certificado do site.
- **Capacidade:** cada ouvinte usa 128 kbps de upload. Uma Câmara com 10 Mbps de upload atende cerca de 50 a 60 ouvintes ao mesmo tempo. Para mais, a rádio pode ser retransmitida a um servidor maior, como o AzuraCast.

## Comandos

| Comando | O que faz |
|---|---|
| `sudo legis` | estado, endereços e PIN inicial |
| `sudo legis logs` | registro do serviço |
| `sudo legis reiniciar` | reinicia o serviço |
| `sudo legis reconfigurar` | refaz as perguntas (ex.: para publicar na internet depois) |
| `sudo legis importar-tse ma "Tuntum"` | importa os vereadores (só num banco vazio) |

## Onde fica cada coisa

| Caminho | Conteúdo |
|---|---|
| `/opt/legis` | programa (substituído a cada atualização do pacote) |
| `/var/lib/legis` | dados da Câmara: banco, gravações, backups diários, modelos |
| `/etc/legis/legis.env` | configuração (portas, PIN inicial, IA…) |

Se as portas 80 e 443 já estiverem ocupadas na máquina, o pacote usa 8090 e 8443.

**Atualizar:** instale o `.deb` novo por cima (`sudo apt install ./legis_<nova>_all.deb`); dados e configuração são mantidos. **Remover:** `sudo apt remove legis`. `apt purge` apaga a configuração, mas os dados em `/var/lib/legis` sempre ficam.

## Gerar o pacote

```bash
bash packaging/construir-deb.sh      # cria dist/legis_<data>+<commit>_all.deb
```
