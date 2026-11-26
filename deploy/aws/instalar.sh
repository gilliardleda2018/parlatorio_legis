#!/bin/bash
# Instala o Parlatório Legis num servidor Debian 12 ou 13 novo (AWS EC2 ou qualquer VPS; também
# funciona no Ubuntu 24.04), com HTTPS automático (Caddy + Let's Encrypt) no domínio informado e a
# estação própria das sessões (Icecast), ouvida em https://DOMINIO/aovivo.
#
# Uso, como root, na máquina nova:
#   DOMINIO=legis.aggillintelligence.com.br bash instalar.sh
# Também serve como "user data" da EC2 (cole o arquivo inteiro e ajuste o DOMINIO abaixo).
# Pode ser rodado de novo para atualizar: mantém o banco, o PIN, as senhas já criadas e lembra o
# DOMINIO e o CERTIFICADO da primeira instalação.
#
# Variáveis opcionais:
#   TRANSCRICAO=1        instala a transcrição local dos discursos (precisa de 4 GB de RAM)
#   CERTIFICADO=alb      o HTTPS fica num Load Balancer da AWS com certificado do ACM: o Caddy
#                        atende só HTTP na porta 80 (padrão: letsencrypt, o Caddy emite e renova sozinho)
#   ESTACAO=0            não instala a estação própria das sessões (Icecast)
#   ESTACAO_OUVINTES     limite de ouvintes ao mesmo tempo (padrão 300)
#   REPO, RAMO           de onde baixar o código (padrão: GitHub, ramo main)
#   SSM=0                não instala o agente do Session Manager da AWS (padrão: instala quando a
#                        máquina é uma EC2; SSM=1 avisa se não conseguir)
set -euo pipefail

# numa atualização, vale o que foi usado na instalação (guardado em /etc/parlatorio.env)
salvo() { [ -f /etc/parlatorio.env ] && grep "^$1=" /etc/parlatorio.env | tail -1 | cut -d= -f2- || true; }
DOMINIO="${DOMINIO:-$(salvo INSTALACAO_DOMINIO)}"
DOMINIO="${DOMINIO:-legis.aggillintelligence.com.br}"
REPO="${REPO:-https://github.com/gilliardleda2018/parlatorio_legis.git}"
RAMO="${RAMO:-main}"
TRANSCRICAO="${TRANSCRICAO:-0}"
CERTIFICADO="${CERTIFICADO:-$(salvo INSTALACAO_CERTIFICADO)}"
CERTIFICADO="${CERTIFICADO:-letsencrypt}"
case "$CERTIFICADO" in letsencrypt|alb) ;; *) echo "CERTIFICADO deve ser letsencrypt ou alb"; exit 1 ;; esac
ESTACAO="${ESTACAO:-1}"
ESTACAO_OUVINTES="${ESTACAO_OUVINTES:-300}"
SSM="${SSM:-auto}"
APP=/opt/parlatorio_legis
DADOS=/var/lib/parlatorio

export DEBIAN_FRONTEND=noninteractive

# acesso pelo Session Manager da AWS (sem SSH): a imagem oficial do Debian não traz o agente SSM.
# Vem antes do resto para a máquina ficar acessível mesmo se outra etapa falhar. Fora da EC2
# (o serviço de metadados não responde) não faz nada; se falhar, só avisa e a instalação segue.
instalar_agente_ssm() {
  systemctl is-active -q amazon-ssm-agent && return 0
  command -v curl >/dev/null || { apt-get update -q && apt-get install -y -q curl; } || return 1
  local imds=http://169.254.169.254/latest token regiao arquitetura
  token="$(curl -fsS -m 3 -X PUT "$imds/api/token" -H 'X-aws-ec2-metadata-token-ttl-seconds: 60')" || {
    [ "$SSM" = "1" ] && return 1  # pedido explicitamente, mas não é uma EC2
    return 0
  }
  regiao="$(curl -fsS -m 3 -H "X-aws-ec2-metadata-token: $token" "$imds/meta-data/placement/region")" || return 1
  case "$(dpkg --print-architecture)" in
    amd64) arquitetura=debian_amd64 ;;
    arm64) arquitetura=debian_arm64 ;;
    *) return 1 ;;
  esac
  curl -fsSL --retry 5 -o /tmp/amazon-ssm-agent.deb \
      "https://s3.$regiao.amazonaws.com/amazon-ssm-$regiao/latest/$arquitetura/amazon-ssm-agent.deb" \
    && dpkg -i /tmp/amazon-ssm-agent.deb \
    && rm -f /tmp/amazon-ssm-agent.deb \
    && systemctl enable --now amazon-ssm-agent
}
if [ "$SSM" != "0" ]; then
  instalar_agente_ssm || echo "AVISO: o agente do Session Manager (SSM) não foi instalado; a instalação continua"
fi

apt-get update -q
apt-get install -y -q python3 python3-venv ffmpeg git caddy tzdata unattended-upgrades
if [ "$ESTACAO" = "1" ]; then
  apt-get install -y -q icecast2
fi
timedatectl set-timezone America/Sao_Paulo || true

id parlatorio >/dev/null 2>&1 || useradd --system --home "$DADOS" --shell /usr/sbin/nologin parlatorio
mkdir -p "$DADOS"
if [ -d "$APP/.git" ]; then
  git -C "$APP" fetch -q origin "$RAMO" && git -C "$APP" reset -q --hard "origin/$RAMO"
else
  git clone -q --branch "$RAMO" "$REPO" "$APP"
fi
chown -R parlatorio:parlatorio "$DADOS"

# configuração do serviço: edite /etc/parlatorio.env e rode "systemctl restart parlatorio"
if [ ! -f /etc/parlatorio.env ]; then
  cat > /etc/parlatorio.env <<FIM
PARLATORIO_BANCO=$DADOS/parlatorio.db
HOST=127.0.0.1
PORTA=8090
HTTPS=0
PROXY_CONFIAVEL=1
TZ=America/Sao_Paulo
# PIN inicial do painel do presidente (depois da primeira troca vale o PIN salvo):
PIN_PRESIDENTE=$(python3 -c 'import secrets; print(f"{secrets.randbelow(10**8):08d}")')
# Só em período de testes: TROCA_PIN=0 deixa entrar com o PIN padrão sem obrigar a troca.
# Em servidor aberto na internet, deixe a troca obrigatória (padrão).
FIM
  chmod 600 /etc/parlatorio.env
fi

segredo() { python3 -c 'import secrets; print(secrets.token_hex(16))'; }
garantir() { grep -q "^$1=" /etc/parlatorio.env || echo "$1=$2" >> /etc/parlatorio.env; }
valor() { grep "^$1=" /etc/parlatorio.env | tail -1 | cut -d= -f2-; }
definir() { grep -v "^$1=" /etc/parlatorio.env > /etc/parlatorio.env.novo || true; echo "$1=$2" >> /etc/parlatorio.env.novo
            chmod 600 /etc/parlatorio.env.novo; mv /etc/parlatorio.env.novo /etc/parlatorio.env; }
definir INSTALACAO_DOMINIO "$DOMINIO"
definir INSTALACAO_CERTIFICADO "$CERTIFICADO"

# estação própria das sessões: Icecast só em 127.0.0.1; o Caddy publica o áudio em https://DOMINIO/aovivo.
# A senha de envio é criada uma vez e mantida (fica em /etc/parlatorio.env e na configuração do Icecast).
if [ "$ESTACAO" = "1" ]; then
  garantir ESTACAO_SENHA "$(segredo)"
  garantir ESTACAO_PORTA 8000
  garantir ESTACAO_MOUNT /sessao
  garantir ESTACAO_PUBLICO "https://$DOMINIO/aovivo"
  ESTACAO_PORTA="$(valor ESTACAO_PORTA)"
  ESTACAO_MOUNT="$(valor ESTACAO_MOUNT)"
  ADMIN_SENHA=""
  if [ -f /etc/icecast2/parlatorio.xml ]; then  # mantém a senha de administração já criada
    ADMIN_SENHA="$(sed -n 's:.*<admin-password>\(.*\)</admin-password>.*:\1:p' /etc/icecast2/parlatorio.xml)"
  fi
  ADMIN_SENHA="${ADMIN_SENHA:-$(segredo)}"
  cat > /etc/icecast2/parlatorio.xml <<FIM
<icecast>
  <location>Brasil</location>
  <admin>contato@$DOMINIO</admin>
  <hostname>$DOMINIO</hostname>
  <limits>
    <clients>$ESTACAO_OUVINTES</clients>
    <sources>2</sources>
    <queue-size>524288</queue-size>
    <client-timeout>30</client-timeout>
    <header-timeout>15</header-timeout>
    <source-timeout>10</source-timeout>
    <burst-size>65535</burst-size>
  </limits>
  <authentication>
    <source-password>$(valor ESTACAO_SENHA)</source-password>
    <relay-password>$(segredo)</relay-password>
    <admin-user>admin</admin-user>
    <admin-password>$ADMIN_SENHA</admin-password>
  </authentication>
  <listen-socket>
    <port>$ESTACAO_PORTA</port>
    <bind-address>127.0.0.1</bind-address>
  </listen-socket>
  <mount type="normal">
    <mount-name>$ESTACAO_MOUNT</mount-name>
    <public>0</public>
  </mount>
  <fileserve>1</fileserve>
  <paths>
    <basedir>/usr/share/icecast2</basedir>
    <logdir>/var/log/icecast2</logdir>
    <webroot>/usr/share/icecast2/web</webroot>
    <adminroot>/usr/share/icecast2/admin</adminroot>
  </paths>
  <logging>
    <accesslog>access.log</accesslog>
    <errorlog>error.log</errorlog>
    <loglevel>2</loglevel>
    <logsize>10000</logsize>
  </logging>
</icecast>
FIM
  ICE_USUARIO="$(stat -c %U /var/log/icecast2)"  # o pacote cria o usuário (icecast2) dono dos logs
  ICE_GRUPO="$(id -gn "$ICE_USUARIO")"            # grupo do usuário; a pasta de logs é do grupo adm
  chown "root:$ICE_GRUPO" /etc/icecast2/parlatorio.xml
  chmod 640 /etc/icecast2/parlatorio.xml
  cat > /etc/systemd/system/parlatorio-estacao.service <<FIM
[Unit]
Description=Estação das sessões (Icecast) do Parlatório Legis
After=network.target

[Service]
User=$ICE_USUARIO
Group=$ICE_GRUPO
ExecStart=/usr/bin/icecast2 -c /etc/icecast2/parlatorio.xml
Restart=always
RestartSec=3
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/log/icecast2
PrivateTmp=true

[Install]
WantedBy=multi-user.target
FIM
  # o serviço padrão do pacote usa outra configuração (senhas padrão, porta aberta): fica desligado
  systemctl disable --now icecast2 2>/dev/null || true
fi

cat > /etc/systemd/system/parlatorio.service <<FIM
[Unit]
Description=Parlatório Legis
After=network-online.target
Wants=network-online.target

[Service]
User=parlatorio
Group=parlatorio
WorkingDirectory=$APP
EnvironmentFile=/etc/parlatorio.env
ExecStart=/usr/bin/python3 $APP/server/servidor.py
Restart=always
RestartSec=3
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$DADOS
PrivateTmp=true

[Install]
WantedBy=multi-user.target
FIM

# /aovivo vai para a estação (Icecast); o resto, para o Parlatório
if [ "$CERTIFICADO" = "alb" ]; then
  # atrás do ALB: só HTTP na porta 80 (o grupo de segurança deve liberar a porta 80 só para o ALB).
  # O ALB acrescenta o IP do visitante ao fim do X-Forwarded-For; o Caddy repassa o cabeçalho como veio
  # (por padrão ele o trocaria por 127.0.0.1 e o bloqueio de PIN veria todos como um só endereço).
  SITE=":80"
  XFF="
			header_up X-Forwarded-For {http.request.header.X-Forwarded-For}"
else
  SITE="$DOMINIO"
  XFF=""
fi
AOVIVO=""
if [ "$ESTACAO" = "1" ]; then
  AOVIVO="	handle /aovivo {
		rewrite * $ESTACAO_MOUNT
		reverse_proxy 127.0.0.1:$ESTACAO_PORTA {
			flush_interval -1$XFF
		}
	}
"
fi
cat > /etc/caddy/Caddyfile <<FIM
$SITE {
$AOVIVO	handle {
		encode gzip
		request_body {
			max_size 64MB
		}
		reverse_proxy 127.0.0.1:8090 {
			flush_interval -1$XFF
		}
	}
}
FIM

systemctl daemon-reload
systemctl enable --now parlatorio
systemctl restart parlatorio  # relê /etc/parlatorio.env (estação, PIN etc.)
if [ "$ESTACAO" = "1" ]; then
  systemctl enable --now parlatorio-estacao
  systemctl restart parlatorio-estacao
fi
systemctl restart caddy

if [ "$TRANSCRICAO" = "1" ]; then
  runuser -u parlatorio -- env PARLATORIO_DADOS="$DADOS" HOME="$DADOS" sh "$APP/scripts/instalar_transcricao.sh" \
    && echo "TRANSCRICAO_PYTHON=$DADOS/whisper/bin/python" >> /etc/parlatorio.env \
    && echo "HF_HOME=$DADOS/.cache/huggingface" >> /etc/parlatorio.env \
    && systemctl restart parlatorio
fi

echo
if [ "$CERTIFICADO" = "alb" ]; then
  echo "Pronto. Registre esta instância no grupo de destino do Load Balancer (HTTP, porta 80) e abra https://$DOMINIO"
else
  echo "Pronto. Aponte o DNS de $DOMINIO para o IP desta máquina e abra https://$DOMINIO"
fi
echo "PIN inicial do presidente: grep PIN_PRESIDENTE /etc/parlatorio.env"
if [ "$ESTACAO" = "1" ]; then
  echo "Estação das sessões: https://$DOMINIO/aovivo (no ar enquanto o painel estiver transmitindo)"
fi
