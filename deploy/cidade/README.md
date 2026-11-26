# LEGIS em uma nova Câmara Municipal (com Docker)

> **Recomendado:** o pacote completo para Debian limpo, com a rádio da Câmara e sem Docker: `curl -fsSLO https://github.com/gilliardleda2018/parlatorio_legis/releases/latest/download/instalar-legis.sh && sudo bash instalar-legis.sh`. Este roteiro é a alternativa em contêineres.

Instalação completa em um servidor Debian, com um comando. Leva de 15 a 40 minutos, a maior parte baixando imagens e modelos.

## O que você precisa

| Item | Mínimo | Recomendado |
|---|---|---|
| Servidor (mini-PC ou computador dedicado) | Debian 12 ou 13, 4 GB de RAM, 64 GB de disco | 8 GB de RAM (para IA e legendas), SSD de 128 GB |
| Rede | cabo no mesmo switch da mesa de som e do plenário | IP fixo (reserva de DHCP no roteador) |
| Internet | só para instalar, transmitir e publicar | 10 Mbps de upload |
| Tailscale (opcional, para publicar na internet sem abrir portas) | uma chave em https://login.tailscale.com/admin/settings/keys (não reutilizável, não efêmera, pré-aprovada) | — |

## Instalar

No servidor, como root:

```bash
curl -fsSL https://raw.githubusercontent.com/gilliardleda2018/parlatorio_legis/main/deploy/cidade/instalar.sh -o instalar.sh
sudo bash instalar.sh
```

O instalador pergunta o estado, o município, o nome curto do endereço, se instala a IA local e a chave do Tailscale (opcional). Para instalar sem perguntas:

```bash
sudo UF=ma MUNICIPIO="Barra do Corda" NOME=barradocorda IA=1 TRANSCRICAO=1 TS_AUTHKEY=tskey-auth-... bash instalar.sh
```

## O que ele faz

1. Instala o Docker.
2. Baixa o LEGIS em `/opt/legis`.
3. Dá ao servidor o nome escolhido na rede: `https://barradocorda.local`.
4. Busca no **TSE** os vereadores eleitos em 2024, com nome de urna, partido e foto, e no **IBGE** a população do município. Cada vereador recebe um canal da mesa de som (1, 2, 3…) e o PIN inicial `0000`.
5. Cria o certificado HTTPS da Câmara e um PIN aleatório para o presidente.
6. Sobe o LEGIS e, se escolhidos:
   - a **IA local**, com o modelo `qwen2.5:3b`, baixado uma vez (cerca de 2 GB);
   - a **transcrição e as legendas** (Whisper);
   - o **Tailscale**, que publica em `https://<nome>.<sua-tailnet>.ts.net` com certificado válido.
7. Mostra os endereços, o PIN do presidente e os próximos passos.

Nada é inventado: o banco nasce sem sessões, projetos ou votos. Esses dados chegam conforme a Câmara usa o sistema, ou são importados do SAPL / portal.

## Depois de instalar (checklist)

- [ ] **Certificado nos aparelhos da rede** (uma vez em cada um): abra `http://IP-DO-SERVIDOR/certificado` e instale. A página inicial explica como fazer no Android, iPhone e Windows.
- [ ] **Painel do presidente → Trocar PIN**: troque o PIN aleatório por um de 6 dígitos ou mais.
- [ ] **Configurações → Identidade**: envie o brasão; nome e título da sessão já vêm preenchidos.
- [ ] **Configurações → Rodapé**: endereço, telefone, e-mail e redes sociais da Câmara.
- [ ] **Plenário → Editar**: confira os nomes (o TSE usa o nome de urna), inclua suplentes que assumiram e marque os líderes e o bloco de cada vereador (situação/oposição).
- [ ] **Mesa de som**: modelo e IP em Configurações → Mesa de som. Use **Testar canal** em cada vereador e na tribuna. Mesas suportadas: veja `deploy/local/README.md`.
- [ ] **Transmissão** (se a Câmara tiver rádio): Configurações → Transmissão ao vivo.
- [ ] **SAPL** (se a Câmara tiver): Configurações → Integração com o SAPL, para importar matérias e sessões.
- [ ] **Vereadores**: cada um entra pelo celular em `/parlamentar` com `0000` e cria o próprio PIN. Combine para todos trocarem no mesmo dia.
- [ ] **Teste geral**: abra uma sessão de teste, chame um orador, abra e corte o microfone, faça uma votação nominal e encerre. Depois, apague a sessão de teste em Sessões.

## Endereços

| Uso | Endereço |
|---|---|
| Painel do presidente | `https://<nome>.local/presidente` |
| Telão (TV do plenário; duplo clique para tela cheia) | `https://<nome>.local/telao` |
| Celular do vereador | `https://<nome>.local/parlamentar` |
| Público (transparência, consulta, dados abertos) | `https://<nome>.<tailnet>.ts.net` |

Durante a sessão, use os endereços **locais**: eles funcionam mesmo se a internet cair. O endereço `ts.net` é para o público e para quem participa de fora.

## Manutenção

- **Atualizar** (faz backup antes): `sudo bash /opt/legis/deploy/cidade/atualizar.sh`
- **Backup automático**: todo dia às 03:00, em `/opt/legis/dados/backups`. Copie essa pasta para fora do servidor regularmente, ou configure o Nextcloud (veja o README principal).
- **Ver o estado**: `cd /opt/legis && docker compose ps`
- **Registro**: `cd /opt/legis && docker compose logs -f parlatorio`
- **Mudar configuração**: edite `/opt/legis/dados/parlatorio.env` e rode `cd /opt/legis && docker compose up -d`.

## Várias câmaras

Cada Câmara tem o próprio servidor e o próprio nome no Tailscale (`tuntum`, `barradocorda`…). Todas podem ficar na mesma rede do Tailscale da empresa, cada uma com o seu endereço `https://<nome>.<tailnet>.ts.net`.
