# Publicar na AWS (EC2) com o domínio legis.aggillintelligence.com.br

Um servidor Ubuntu na região de São Paulo, com o Parlatório atrás do Caddy, que emite e renova sozinho o certificado HTTPS (Let's Encrypt). O certificado atual do domínio é da Hostinger e fica lá: na AWS o Caddy cria outro assim que o DNS apontar para a máquina nova.

## 1. Criar a máquina (console da AWS)

- **Região:** América do Sul (São Paulo), `sa-east-1`.
- **Imagem:** Ubuntu Server 24.04 LTS.
- **Tipo:** `t3.small` (2 GB) basta para o plenário e a transmissão. Com a transcrição local dos discursos, use `t3.medium` (4 GB).
- **Disco:** 30 GB gp3 (gravações das falas ocupam ~0,7 MB por minuto).
- **Grupo de segurança:** entrada 80 e 443 de qualquer lugar; 22 (SSH) só do seu IP.
- **Dados do usuário (user data):** cole o arquivo `instalar.sh` desta pasta (com `t3.medium`, troque `TRANSCRICAO:-0` por `TRANSCRICAO:-1` no arquivo para instalar também a transcrição).
- Depois de criada, associe um **IP elástico** à instância (o IP não muda ao reiniciar).

Confira o preço no [AWS Pricing Calculator](https://calculator.aws/) antes de criar: a cobrança é por hora da máquina, disco, IP público e tráfego de saída. A transmissão ao vivo sai do servidor de rádio, não desta máquina.

## 2. Apontar o domínio (painel da Hostinger → DNS)

- Registro **A** `legis` → IP elástico da AWS.
- **Apague** os registros **AAAA** de `legis` (hoje apontam para a Hostinger em IPv6; se ficarem, parte dos visitantes e a validação do certificado vão para o lugar errado).
- Se o subdomínio estiver ligado a um site/hospedagem da Hostinger, desligue essa ligação para o DNS valer.

Em alguns minutos `https://legis.aggillintelligence.com.br` abre o Parlatório com cadeado.

## 3. Levar os dados de Tuntum

No computador onde o Parlatório roda hoje:

```bash
python3 -c "import sqlite3; s=sqlite3.connect('dados/tuntum.db'); d=sqlite3.connect('/tmp/parlatorio.db'); s.backup(d)"
scp /tmp/parlatorio.db ubuntu@IP-ELASTICO:/tmp/
scp -r dados/gravacoes ubuntu@IP-ELASTICO:/tmp/
```

No servidor:

```bash
sudo systemctl stop parlatorio
sudo install -o parlatorio -g parlatorio -m 600 /tmp/parlatorio.db /var/lib/parlatorio/parlatorio.db
sudo cp -r /tmp/gravacoes /var/lib/parlatorio/ && sudo chown -R parlatorio: /var/lib/parlatorio/gravacoes
sudo systemctl start parlatorio
```

O banco leva os PINs salvos. Se o PIN do presidente já foi trocado no banco, é ele que vale (e não o `PIN_PRESIDENTE` de `/etc/parlatorio.env`).

## Segurança na internet

- Com o servidor aberto ao público, **não use `TROCA_PIN=0`**: qualquer pessoa que saiba o PIN padrão `0000` entraria como vereador. Deixe a troca obrigatória (padrão) e faça a sessão de troca de PINs antes de divulgar o endereço.
- O Parlatório escuta só em `127.0.0.1`; o Caddy é a única porta de entrada. O bloqueio por tentativas usa o IP real do visitante (`PROXY_CONFIAVEL=1`).
- Backups diários ficam em `/var/lib/parlatorio/backups`; para mandar cópias para fora (Nextcloud), veja o README principal.

## Atualizar

```bash
sudo DOMINIO=legis.aggillintelligence.com.br bash /opt/parlatorio_legis/deploy/aws/instalar.sh
```

Baixa a versão nova do GitHub e reinicia o serviço; dados e configuração são mantidos.

## Comandos úteis

```bash
systemctl status parlatorio caddy
journalctl -u parlatorio -f          # registro do Parlatório
sudo nano /etc/parlatorio.env && sudo systemctl restart parlatorio
```
