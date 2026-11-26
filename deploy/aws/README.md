# Publicar na AWS (EC2) com o domínio legis.aggillintelligence.com.br

Uma instância **Debian** na região de São Paulo com o Parlatório e a **estação própria das sessões** (Icecast), atrás do Caddy. O HTTPS fica num **Application Load Balancer (ALB)** com o certificado do domínio que já está no **AWS Certificate Manager (ACM)**:

```
visitantes ──HTTPS (certificado ACM)──▶ ALB ──HTTP:80──▶ Caddy na EC2 ─┬─ /aovivo → estação das sessões (Icecast, 127.0.0.1:8000)
                                                                       └─ resto   → Parlatório (127.0.0.1:8090)
```

A estação é só das sessões, separada de qualquer outra rádio: quem quiser ouvir abre `https://legis.aggillintelligence.com.br/aovivo` (ou o player da tela inicial e da transparência) enquanto o painel estiver transmitindo.

> Sem ALB: o instalador também sabe pedir sozinho um certificado ao Let's Encrypt (`CERTIFICADO=letsencrypt`, o padrão). Aí o DNS aponta direto para a instância, com IP elástico e as portas 80 e 443 abertas nela.

## 1. Grupos de segurança (EC2 → Security Groups)

- **`parlatorio-alb`** (do balanceador): entrada **80** e **443** de qualquer lugar (`0.0.0.0/0`).
- **`parlatorio-ec2`** (da instância): entrada **80 só do grupo `parlatorio-alb`** (escolha o grupo como origem, não um IP) e **22** (SSH) só do seu IP.

A porta 80 da instância não pode ficar aberta para a internet: é por ela que o ALB informa o IP real de cada visitante, que o bloqueio de PIN usa.

## 2. Criar a instância

- **Região:** América do Sul (São Paulo), `sa-east-1`.
- **Imagem:** Debian 13 (trixie) ou Debian 12 (bookworm), 64 bits (x86). O usuário de acesso por SSH é `admin`.
- **Tipo:** `t3.small` (2 GB) basta para o plenário e a estação. Com a transcrição local dos discursos, use `t3.medium` (4 GB).
- **Disco:** 30 GB gp3 (gravações das falas ocupam ~0,7 MB por minuto).
- **Rede:** sub-rede pública com IP público automático (a instância precisa sair para a internet para instalar pacotes, atualizar e mandar backups); grupo de segurança `parlatorio-ec2`.
- **Dados do usuário (user data):** cole o arquivo `instalar.sh` desta pasta e, logo abaixo da linha `set -euo pipefail`, acrescente:

  ```bash
  CERTIFICADO=alb
  DOMINIO=legis.aggillintelligence.com.br
  ```

  (com `t3.medium`, acrescente também `TRANSCRICAO=1` para instalar a transcrição.)

Ou, numa instância já criada, por SSH:

```bash
sudo CERTIFICADO=alb DOMINIO=legis.aggillintelligence.com.br bash instalar.sh
```

## 3. Load Balancer com o certificado do ACM

1. **Certificado:** em Certificate Manager, confira que o certificado do domínio está **emitido** e na região **sa-east-1** (o ALB só usa certificados da própria região; se ele estiver em outra, peça um novo em sa-east-1, é gratuito).
2. **Grupo de destino** (EC2 → Target Groups → Create): tipo *Instances*, protocolo **HTTP**, porta **80**; verificação de saúde em `/` (resposta 200). Registre a instância.
3. **Load Balancer** (EC2 → Load Balancers → Create → *Application Load Balancer*): *Internet-facing*, IPv4, pelo menos duas zonas de disponibilidade, grupo de segurança `parlatorio-alb`.
   - Ouvinte **HTTPS:443** → encaminha para o grupo de destino, com o certificado do ACM.
   - Ouvinte **HTTP:80** → *Redirect* para HTTPS 443 (código 301).
   - O tempo de inatividade padrão (60 s) serve: o painel recebe um sinal a cada 10 s e o áudio da estação não para.

## 4. Apontar o domínio (painel da Hostinger → DNS)

- Registro **CNAME** `legis` → nome DNS do Load Balancer (algo como `parlatorio-alb-123.sa-east-1.elb.amazonaws.com`). O ALB não tem IP fixo, por isso não use registro A.
- **Apague** os registros **A** e **AAAA** antigos de `legis` (se ficarem, parte dos visitantes vai para o lugar errado).
- Se o subdomínio estiver ligado a um site/hospedagem da Hostinger, desligue essa ligação para o DNS valer.
- Mantenha o CNAME de validação que o ACM pediu (`_algumacoisa.legis`): é ele que renova o certificado sozinho.

Em alguns minutos `https://legis.aggillintelligence.com.br` abre o Parlatório com cadeado.

## 5. Levar os dados de Tuntum

No computador onde o Parlatório roda hoje:

```bash
python3 -c "import sqlite3; s=sqlite3.connect('dados/tuntum.db'); d=sqlite3.connect('/tmp/parlatorio.db'); s.backup(d)"
scp /tmp/parlatorio.db admin@IP-PUBLICO-DA-INSTANCIA:/tmp/
scp -r dados/gravacoes admin@IP-PUBLICO-DA-INSTANCIA:/tmp/
```

No servidor:

```bash
sudo systemctl stop parlatorio
sudo install -o parlatorio -g parlatorio -m 600 /tmp/parlatorio.db /var/lib/parlatorio/parlatorio.db
sudo cp -r /tmp/gravacoes /var/lib/parlatorio/ && sudo chown -R parlatorio: /var/lib/parlatorio/gravacoes
sudo systemctl start parlatorio
```

O banco leva os PINs salvos. Se o PIN do presidente já foi trocado no banco, é ele que vale (e não o `PIN_PRESIDENTE` de `/etc/parlatorio.env`).

## Estação das sessões

O instalador põe o Icecast na própria instância, só em `127.0.0.1` (não há porta de rádio aberta), com uma senha de envio aleatória guardada em `/etc/parlatorio.env` (`ESTACAO_SENHA`). O Parlatório já sai configurado para ela: em **Configurações → Transmissão ao vivo** aparece “Estação própria das sessões”, sem endereço nem senha para digitar. Para pôr no ar, a mesa clica em **Transmitir a sessão ao vivo** na aba Sessão.

- Endereço para ouvir: `https://legis.aggillintelligence.com.br/aovivo` (MP3; abre em qualquer navegador, celular ou aplicativo de rádio).
- Qualidade: 96 kbps vem por padrão e basta para voz; dá para escolher outra no painel.
- Limite de ouvintes ao mesmo tempo: 300 (`ESTACAO_OUVINTES` no instalador).
- A outra rádio continua disponível como destino alternativo no mesmo painel, se um dia for preciso.

## Custos

Confira no [AWS Pricing Calculator](https://calculator.aws/) antes de criar. A cobrança é por hora da instância, disco, **Load Balancer** (valor fixo por hora mais o uso), IP público e **tráfego de saída**. O áudio da estação sai desta conta: a 96 kbps, cada ouvinte consome cerca de **43 MB por hora** (50 ouvintes numa sessão de 3 horas ≈ 6,5 GB).

## Segurança na internet

- Com o servidor aberto ao público, **não use `TROCA_PIN=0`**: qualquer pessoa que saiba o PIN padrão `0000` entraria como vereador. Deixe a troca obrigatória (padrão) e faça a sessão de troca de PINs antes de divulgar o endereço.
- O Parlatório e a estação escutam só em `127.0.0.1`; o Caddy é a única porta de entrada, e só o ALB chega até ele. O bloqueio por tentativas usa o IP real do visitante, que o ALB acrescenta ao `X-Forwarded-For` e o Caddy repassa sem alterar (`PROXY_CONFIAVEL=1`).
- O painel de administração do Icecast não é publicado: pelo domínio só existe `/aovivo`.
- Backups diários ficam em `/var/lib/parlatorio/backups`; para mandar cópias para fora (Nextcloud), veja o README principal.

## Atualizar

```bash
sudo bash /opt/parlatorio_legis/deploy/aws/instalar.sh
```

Baixa a versão nova do GitHub e reinicia os serviços. Dados, PINs, senhas da estação, domínio e modo do certificado são mantidos (o instalador lembra o que foi usado na instalação).

## Comandos úteis

```bash
systemctl status parlatorio parlatorio-estacao caddy
journalctl -u parlatorio -f                      # registro do Parlatório
sudo tail -f /var/log/icecast2/error.log         # registro da estação
sudo nano /etc/parlatorio.env && sudo systemctl restart parlatorio
```
