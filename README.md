# Parlatório Legis

Software livre para conduzir sessões de casas legislativas: tempo de fala, inscrições, apartes, presença, quórum, pauta e votação, ao vivo em todas as telas da rede. Visual no padrão Aggilli, com cor, tema e brasão configuráveis por casa.

## Telas

| Endereço | Para quem | O que faz |
|---|---|---|
| `/presidente` | Mesa diretora / secretaria (PIN) | Fases da sessão, fila de inscritos, cronômetro, aparte, questão de ordem, pedidos vindos do celular, pauta, votação, presença, relatórios e configurações |
| `/telao` | TV ou projetor do plenário | Orador, tempo com barra e alertas, aparte em destaque, matéria em discussão, próximos inscritos, quórum e placar de votação (nominal ou secreta) |
| `/parlamentar` | Celular de cada parlamentar (PIN pessoal) | Registrar presença, pedir inscrição, aparte, questão de ordem, pela ordem e votar |

## Rodar

Só precisa de Python 3.10 ou mais novo, sem dependências:

```bash
PIN_PRESIDENTE=1234 python3 server/servidor.py
```

Abra `http://localhost:8090`. Em outros computadores e celulares da mesma rede, use o IP da máquina: o servidor redireciona para o endereço seguro `https://192.168.0.10:8443`.

Com Docker:

```bash
PIN_PRESIDENTE=4321 docker compose up -d --build
```

Variáveis de ambiente:

- `PORTA`: porta HTTP (padrão 8090)
- `PORTA_HTTPS`: porta HTTPS (padrão 8443; se estiver ocupada, o servidor segue só em HTTP e avisa)
- `HTTPS`: `auto` (padrão) cria o certificado local se faltar; `0` desliga
- `TLS_CERT` e `TLS_KEY`: certificado próprio (por exemplo, de um domínio) no lugar do local
- `TLS_NOMES`: nomes ou IPs extras para o certificado local, separados por vírgula
- `TZ`: fuso das atas (o Docker já usa `America/Sao_Paulo`)
- `PIN_PRESIDENTE`: PIN do painel do presidente (padrão 1234; troque em produção)
- `TRANSCRICAO`: `auto` (padrão), `local`, `api`, `comando` ou `0` — veja **Transcrição dos discursos**
- `HOST`: endereço onde o servidor escuta (padrão `0.0.0.0`; use `127.0.0.1` atrás de um proxy)
- `PROXY_CONFIAVEL=1`: atrás de proxy (Caddy, nginx, Tailscale Funnel), usa o IP real do visitante no bloqueio por tentativas; `PROXY_IPS` diz de onde o proxy conecta (padrão `127.0.0.1,::1`; em Docker, o gateway da rede)
- `TROCA_PIN=0`: período de testes — deixa entrar com o PIN padrão (0000) sem obrigar a troca. Não use em produção.
- `PARLATORIO_BANCO`: caminho do banco SQLite (padrão `dados/parlatorio.db`)

Os parlamentares começam com PIN `0000` e o painel com `1234` (ou o `PIN_PRESIDENTE`). No primeiro acesso o sistema **obriga a troca** por um PIN pessoal.

## Segurança de acesso

- PINs guardados só como hash (PBKDF2-SHA256 com sal), nunca em texto. Bancos antigos são convertidos ao iniciar.
- Após 5 PINs errados na mesma conta (ou 20 do mesmo endereço), novas tentativas ficam bloqueadas por 15 minutos.
- PIN padrão ou óbvio (0000, 1234, 1111, sequências) obriga a troca antes de qualquer ação. O PIN do painel deve ter 6 dígitos ou mais.
- O login expira após 12 h sem uso no painel do presidente e 30 dias no celular, e nunca dura mais de 90 dias. Trocar o PIN encerra os outros acessos daquela conta.
- Votar ou pedir destaque na sessão virtual exige confirmar o PIN. O app pode lembrar por 10 minutos, só naquela página.
- Botão **PIN** no painel e no app para trocar quando quiser.

## Backup

- Todo dia às 03:00 (`BACKUP_HORA`; vazio desliga), e também no primeiro início, o servidor grava `dados/backups/parlatorio-AAAAMMDD-HHMMSS.db.gz` com todo o banco: sessões, votos, projetos, imagens e auditoria. Guarda as últimas 14 cópias (`BACKUP_MANTER`) e copia as gravações novas para `dados/backups/gravacoes/`.
- **Configurações → Backup** mostra o último backup e tem o botão **Fazer backup agora**.
- **Nextcloud (opcional):** defina `NEXTCLOUD_URL` (ex.: `https://nuvem.exemplo/remote.php/dav/files/usuario`), `NEXTCLOUD_USUARIO`, `NEXTCLOUD_SENHA` (use uma senha de app) e `NEXTCLOUD_PASTA`. Cada backup é enviado por WebDAV. As credenciais ficam só no ambiente do servidor.
- **Restaurar:** pare o servidor, rode `gunzip -c dados/backups/parlatorio-....db.gz > dados/parlatorio.db` e copie a pasta `gravacoes` de volta para `dados/`.

## Testes

```bash
cd tests && python3 -m unittest -v
```

Cada classe de teste sobe um servidor próprio (porta livre, banco temporário) e cobre:

- votação nominal e secreta;
- tribuna, aparte e congelar;
- mesa de som por OSC real (UDP);
- gravação, arquivamento e ata em PDF;
- sessão virtual com PIN, destaque e apuração;
- sessão remota;
- segurança (hash, troca obrigatória, bloqueio, permissões);
- transparência, recomendações, rodapé e backup.

O GitHub Actions roda tudo a cada push, em Python 3.11, 3.12 e 3.13.

## HTTPS

Na primeira execução o servidor cria, com `openssl`, uma autoridade certificadora própria da casa (`dados/tls/ca.crt`) e um certificado válido para `localhost`, o nome da máquina e os IPs da rede. Depois:

1. Em cada computador, tablet e celular, abra `http://IP-do-servidor:8090/certificado` e instale o certificado (as instruções para Android, iPhone e Windows estão na página inicial).
2. Use sempre `https://IP-do-servidor:8443`. Quem abrir pelo endereço `http://` é redirecionado (menos em `localhost`).

Com HTTPS, a gravação das falas funciona de qualquer computador da rede, não só no servidor. Se o IP do servidor mudar, rode `python3 server/servidor.py --renovar-certificado` (a autoridade é mantida, os aparelhos não precisam reinstalar nada).

Em servidor com domínio público, use um proxy com Let's Encrypt (Caddy, por exemplo: `parlatorio.camara.gov.br { reverse_proxy localhost:8090 }`) e rode o Parlatório com `HTTPS=0`.

## Sessões e ata

Ao fim de cada sessão, em **Sessões → Encerrar e arquivar sessão**, o sistema guarda presença, pauta, pronunciamentos, votações (com o voto nominal de cada um), ocorrências e gravações, e abre a próxima sessão em branco (as matérias não votadas podem seguir para ela).

A **ata em PDF** é gerada pelo próprio servidor, sem programas externos: cabeçalho com o brasão, presença e quórum, pauta com a situação final, pronunciamentos com apartes e tempos, votações, ocorrências, gravações e espaço para assinaturas. A prévia da sessão em andamento pode ser aberta a qualquer momento. O brasão enviado em Configurações também vai para a ata.

## Banco de projetos e relevância social

Cada projeto tem uma **ficha social**: áreas, pessoas beneficiadas (estimativa com fonte), abrangência territorial, grupos vulneráveis atendidos e urgência. O sistema calcula uma **nota de relevância de 0 a 100** com pesos que a casa ajusta (aba Projetos), mostrando quanto cada critério contribuiu. A nota serve para comparar e priorizar; ela não substitui a análise de mérito.

A pauta de cada sessão escolhe projetos do banco (ordenados por relevância), e votações e sessões ficam no histórico de cada projeto.

## Transparência e estatísticas

`/transparencia` é uma página pública com números das sessões encerradas:

- projetos por relevância social;
- pessoas beneficiadas por área;
- votações por mês;
- presença, falas, tempo de fala e votos de cada parlamentar;
- tempo de fala por partido;
- últimas votações, com o voto nominal de cada um;
- sessões realizadas.

O presidente abre a versão interna (`/transparencia?interno=1`), que mostra também as propostas ainda não protocoladas, o alinhamento de cada parlamentar com o resultado das votações e as ocorrências.

## Ideias de projetos (recomendações)

Na área privada do app do parlamentar, a aba **Ideias de projetos** recomenda boas práticas do Brasil e do mundo (catálogo em `server/boas_praticas.json`, com resultados relatados, força da evidência, custo, fontes e o **instrumento adequado** para propor na Câmara, considerando os limites de iniciativa do Legislativo). A nota (0 a 100) é explicável e combina:

- **afinidade (45%)**: áreas marcadas no perfil, áreas dos projetos do parlamentar, práticas que ele salvou ou descartou e **semelhança de texto** (TF-IDF em português, `server/texto.py`) entre a prática e as ementas dos seus projetos, os temas que ele escreveu no perfil e as práticas salvas;
- **evidência (25%)**: força das fontes;
- **lacuna (15%)**: áreas com poucos projetos ativos na cidade;
- **colegas (15%)**: práticas salvas ou propostas por outros parlamentares.

Perde 15 pontos se já existe projeto parecido na cidade, e a tela mostra qual. O parlamentar pode **salvar** (★), marcar **não me interessa** (some da lista, com opção de desfazer) e filtrar só as salvas.

Ao clicar em **Propor este projeto**, o parlamentar adapta a ementa e envia uma proposta (`PROP n/ano`). Ela fica visível só para o presidente, que revisa, numera e protocola na aba Projetos.

O catálogo foi pesquisado em novembro de 2026. Revise as fontes e atualize-o periodicamente.

## Atuação parlamentar e situação × oposição

- **Índice de atuação (0 a 100)** por parlamentar, com presença, participação nos debates (falas por sessão), projetos apresentados (aprovados valem 3) e participação nas votações nominais. Mede a quantidade de atividade registrada, não a qualidade do mandato.
- **Blocos**: a presidência classifica cada parlamentar como situação, oposição ou independente (um a um ou por partido, na aba Plenário). A transparência mostra por bloco: presença, tempo de fala, projetos e aprovações, **coesão** nas votações, **votações que dividiram situação e oposição** com o placar por bloco e quem venceu, e quanto cada parlamentar **votou com a situação**. Na visão interna, o sistema sugere o bloco a partir dos votos (≥70% com a situação: situação; ≤30%: oposição).

## Plenário virtual

**Sessão virtual assíncrona** (aba Plenário virtual do painel):

- O presidente cria a sessão com abertura e encerramento (prazo mínimo de 1 hora) e inclui projetos do banco, com o quórum de cada um. A pauta só muda antes da abertura.
- Durante o prazo, cada vereador, pelo app (aba Virtual):
  - vota e pode mudar o voto até o fim;
  - registra manifestações escritas, que são públicas e vão para a ata;
  - pode pedir destaque: o item sai da sessão virtual e vai para a sessão presencial.
- No fim do prazo o servidor apura sozinho e arquiva a sessão com votos nominais, presença (quem votou) e ata em PDF. Isso alimenta a transparência, os perfis e as estatísticas. A presidência pode encerrar antes do prazo, e isso fica registrado na ata.
- Na maioria simples, a votação só vale se votou a maioria absoluta dos membros; senão o item fica "sem quórum".

**Sessão remota/híbrida ao vivo:**

- O presidente liga a sessão remota e informa o servidor Jitsi Meet.
- Os vereadores entram pelo app com "Entrar na sessão remota". A presença é registrada como remota, e eles pedem a palavra e votam normalmente.
- O endereço da sala só é entregue a quem está logado, nunca às telas públicas.
- Pausar, congelar, cortar ou encerrar a palavra também silencia o microfone do vereador remoto na videoconferência.
- O telão mostra "Participação remota" quando o orador está por vídeo.
- O meet.jit.si público exige login do moderador. Para uso oficial, instale um Jitsi próprio da câmara (`docker` oficial em jitsi.github.io/handbook).

## Página pública de cada parlamentar

- `/parlamentares` lista todos (foto, partido, bloco, presença, índice de atuação), com busca e ordenação.
- `/parlamentares/<id>-<nome>` mostra o perfil:
  - índice de atuação e posição no ranking;
  - presença, falas, tempo de fala, projetos e votos, sempre comparados à média da casa;
  - presença e tempo de fala sessão a sessão;
  - projetos de autoria e votos recentes;
  - link para a ficha no SAPL, quando importado.
- Os nomes na transparência levam ao perfil. Nada interno (ocorrências, propostas não protocoladas, sugestão de bloco) aparece no perfil público.

## Integração com o SAPL

Em **Configurações → Integração com o SAPL**, informe o endereço do SAPL da câmara (ex.: `https://sapl.suacidade.uf.leg.br`) e clique em **Salvar e testar**. A integração é **somente leitura**, pela API pública do SAPL:

- **Importar parlamentares:** parlamentares ativos, com partido (filiação atual) e foto. Quem já existe é reconhecido pelo nome e atualizado; quem não está ativo no SAPL é mantido e listado.
- **Importar matérias em tramitação:** por ano e tipo (PL, PLC…), com autoria. As matérias entram no banco de projetos com link para a tramitação e o texto original. A ficha social continua a ser preenchida aqui.
- **Importar pauta da sessão:** escolha uma sessão recente do SAPL. O expediente e a ordem do dia entram na pauta, na mesma ordem.

As importações rodam em segundo plano, com barra de progresso e registro. O servidor espera um pouco entre os pedidos para não sobrecarregar o SAPL da câmara. Reimportar não duplica nada.

Testado com o SAPL público de Campina Grande (PB). Ainda **não envia** resultados de votação ao SAPL: isso exige um usuário com permissão de escrita e testes num SAPL de homologação.

## Começar com os vereadores eleitos (TSE)

Para criar o banco de uma câmara com os vereadores eleitos em 2024 (nome de urna, partido, número, votos e foto oficial do TSE) e a população do Censo 2022 (IBGE):

```bash
python3 scripts/tse_eleitos.py MA "Tuntum" dados/tuntum.db
PARLATORIO_BANCO=dados/tuntum.db python3 server/servidor.py
```

O banco nasce sem sessões, votos ou projetos: nada é inventado sobre pessoas reais. Ajuste no painel os suplentes que assumiram, as mudanças de partido e os nomes parlamentares. Se a câmara tiver SAPL, a importação de lá é mais completa e atualizada.

## Importar do portal legislativo da câmara

Muitas câmaras do Maranhão e de outros estados usam a plataforma `legislativomunicipal.com`. Para trazer os dados oficiais de lá (com o servidor parado):

```bash
python3 scripts/importar_portal_camara.py https://www.cmtuntum.legislativomunicipal.com dados/tuntum.db
```

O script importa:

- **Vereadores:** nome civil, cargo na Mesa, partido atual, presença e número de matérias oficiais. Quem já veio do TSE é reconhecido; suplentes que exerceram mandato entram como ex-membros e não alteram o quórum.
- **Matérias de autoria:** requerimentos, projetos de lei e de resolução, indicações e moções, com fase, situação e link. As áreas são sugeridas por palavras-chave e marcadas como automáticas.
- **Sessões encerradas:** a chamada nominal (presença) e a pauta com a situação de cada matéria. O portal não publica votos nominais, falas nem horários, então esses indicadores ficam como "—" e o índice de atuação usa só presença e projetos.

Atas, ofícios e pareceres ficam fora do banco de projetos. O script pode ser rodado de novo para atualizar, sem duplicar nada.

## Demonstração

Para apresentar o sistema com dados fictícios (16 projetos e 8 sessões arquivadas):

```bash
python3 scripts/dados_demo.py dados/demo.db
PARLATORIO_BANCO=dados/demo.db python3 server/servidor.py
```

## Pacote de recursos inspirado nos melhores sistemas do mundo

| Recurso | Onde | Inspiração |
|---|---|---|
| **Rascunho da ata por item da pauta** (pontos discutidos, encaminhamentos, resultado), revisado pela secretaria e incluído na ata em PDF | Painel → Relatórios | CivicPlus, PublicInput, ParlBrief (Canadá) |
| **Legendas ao vivo** da sessão transmitida, no telão e na faixa "Ao vivo" | automático com a transcrição local | tradução ao vivo de Fresno (EUA) |
| **Consulta pública artigo por artigo**, com apoio a sugestões e resposta da autoria (acatada, em parte, não acatada) | `/participe`; Painel → Projetos → Texto e consulta | Wikilegis/e-Democracia, Decidim, MAPLE |
| **Perguntas sobre os discursos** ("o que foi dito sobre…?"), com as fontes citadas | Transparência e perfis | busca por perguntas do Hansard de Ontário |
| **Vídeo da sessão com índice clicável** por orador e por votação | `/sessao/<id>`; Painel → Relatórios | vídeos com marcação de tempo (Granicus e similares) |
| **Pauta e anexos no celular do vereador**, com o item em andamento e o resultado | app do vereador → Pauta | Bosch DICENTIS |
| **Dados abertos** em JSON, CSV e **Akoma Ntoso 3.0** (projetos e debates) | `/dados` | Senado italiano, Parlamento Europeu, Senado brasileiro |
| **Acessibilidade**: modo de alto contraste e letras grandes, voto confirmado por vibração, som e leitor de tela; tradutor de Libras (VLibras) nas páginas públicas | app do vereador (botão Aa); páginas públicas | Bosch (voto acessível) |
| **Assistente do cidadão** ("Pergunte à Câmara"), respondendo com base nos registros públicos e mostrando as fontes | todas as páginas públicas | assistente virtual da Câmara dos Deputados |

**IA:** o rascunho da ata, as perguntas e o assistente usam um modelo de linguagem escolhido pela variável `IA`:
- `ollama` (recomendado): modelo aberto rodando no próprio servidor, sem custo e sem dados saindo da Câmara. Em Docker: `COMPOSE_PROFILES=ia` e `docker compose exec ollama ollama pull qwen2.5:3b`; no instalador Debian: `IA=1`.
- `claude`: API da Anthropic pelo SDK oficial (`pip install anthropic`, modelo `claude-opus-5-5`). Os textos saem do servidor.
- sem IA: cada recurso funciona em modo simples (rascunho com quem falou e o resultado; perguntas respondidas com os trechos encontrados).

As respostas automáticas sempre mostram as fontes, e o rascunho da ata só vale depois de revisado.

## Publicar na internet

Para rodar **dentro da Câmara** (on-premise, servidor Debian na rede do plenário, controlando a mesa de som), veja [deploy/local/README.md](deploy/local/README.md). Mesas suportadas: Behringer X32/M32, X Air, WING; Yamaha TF/DM3/DM7/QL/CL/Rivage; Allen & Heath Qu/dLive/Avantis/SQ; Soundcraft Ui; qualquer mesa OSC; Bitfocus Companion ou Node-RED por ponte HTTP; relés e centrais de conferência com URL por canal.

O roteiro para a AWS (EC2 em São Paulo, HTTPS automático com Caddy no domínio da câmara) está em [deploy/aws/README.md](deploy/aws/README.md), com o instalador `deploy/aws/instalar.sh`, que serve para qualquer servidor Ubuntu.

## Transmissão ao vivo (rádio)

O painel transmite o áudio da sessão para um servidor de rádio **Icecast** ou **AzuraCast** (como o do projeto [streaming_radio](https://github.com/gilliardleda2018/streaming_radio)). O computador da mesa capta a saída da mesa de som (a mesma entrada da gravação) e manda o áudio ao Parlatório, que converte para MP3 com o ffmpeg e publica no servidor de rádio pelo protocolo *source* do Icecast.

**Modo local (recomendado quando a rádio está na mesma rede ou no mesmo servidor):** em Configurações → Transmissão ao vivo, escolha "A rádio puxa do Parlatório". O Parlatório passa a servir o MP3 da sessão em `http://IP-DO-SERVIDOR:8090/aovivo` (só enquanto estiver no ar), e a estação do AzuraCast puxa de lá com uma linha na configuração avançada do Liquidsoap (Perfil da estação → Editar → Configuração personalizada, seção "antes da transmissão"):

```
camara_sessao = input.http(id="camara_sessao", "http://IP-DO-SERVIDOR:8090/aovivo")
radio = fallback(id="camara_ou_programacao", track_sensitive=false, [camara_sessao, radio])
```

Sem sessão no ar, a estação segue a programação normal. Não há senha nem porta para abrir. Se o Parlatório e o AzuraCast rodam em Docker na mesma máquina, ligue os dois na mesma rede Docker e use `http://parlatorio:8090/aovivo`.

**Modo DJ (rádio em outro lugar da internet):**

1. No AzuraCast: na estação, ative **Streamers/DJs**, crie uma conta para a câmara e anote usuário e senha. A conexão é na porta 8005, montagem `/`.
2. No painel: **Configurações → Transmissão ao vivo**, preencha o endereço do servidor, usuário, senha e o endereço público para ouvir (o link da estação, por exemplo `https://radio.exemplo.br/listen/camara/radio.mp3`).
3. Na aba **Sessão**, clique em **Transmitir a sessão ao vivo**.

Enquanto estiver no ar, a tela inicial e a transparência mostram a faixa **Ao vivo** com o player e o nome de quem está na tribuna. A senha do servidor de rádio fica só no servidor (nunca vai para as telas). Precisa do `ffmpeg` no servidor (o Docker já traz).

## Transcrição dos discursos

Cada fala gravada é transcrita automaticamente depois que termina. O texto aparece no painel (Relatórios → Gravações das falas), onde a presidência lê, corrige e decide se publica. Só os discursos publicados entram na busca pública (**Transparência → O que foi dito na tribuna**) e na página de cada parlamentar. A ata em PDF traz a íntegra e marca o que ainda é transcrição automática não revisada.

Transcrição local, no próprio servidor e sem mandar áudio para fora (faster-whisper, CPU):

```bash
sh scripts/instalar_transcricao.sh          # cria dados/whisper e baixa o modelo "small" (~460 MB)
```

Depois é só reiniciar o servidor. Outras opções: `TRANSCRICAO=api` com `TRANSCRICAO_URL` e `TRANSCRICAO_CHAVE` (serviço compatível com `/v1/audio/transcriptions`; o áudio sai do servidor), `TRANSCRICAO_CMD` (um comando seu) ou `TRANSCRICAO=0`. Sem motor, a secretaria pode digitar o texto no painel. Em Configurações dá para publicar as transcrições automáticas sem revisão.

## Como funciona

- `server/servidor.py`: servidor HTTP da biblioteca padrão do Python. É a única fonte da verdade: aplica as regras (corte de microfone, fim de aparte, apuração de votos) e guarda estado, imagens e registro de auditoria no SQLite.
- As telas recebem o estado por **Server-Sent Events** (`/eventos`) e mandam comandos por `POST /api/acao`. Os cronômetros usam o relógio do servidor, então todas as telas mostram o mesmo tempo.
- Votação secreta: o servidor guarda só a contagem e quem já votou; o voto de cada um não é registrado.

## Regras configuráveis

- Fases com tempo por orador e tempo total da fase (Pequeno Expediente, Grande Expediente, Comunicações de Liderança, Ordem do Dia, Encaminhamento, Explicação Pessoal, Tribuna Livre)
- Aviso amarelo, corte automático do microfone e sinal sonoro
- Aparte descontado ou não do tempo do orador; tempos de aparte e de questão de ordem
- Cessão de tempo entre inscritos
- Inscrição pelo celular com ou sem aprovação; exigir presença para pedir a palavra e votar
- Quórum: maioria simples (com presença mínima), absoluta ou dois terços
- Cor de destaque, tema claro/escuro, brasão, fotos dos parlamentares e cores dos partidos

## Mesa de som e microfones

Cada parlamentar tem um canal na mesa (Plenário → Editar). O servidor abre o canal de quem está com a palavra e fecha ao pausar, congelar, cortar ou esgotar o tempo. Configure em **Configurações → Mesa de som**:

- **Simulada**: sem equipamento; o painel mostra o que seria enviado.
- **OSC**: mesas digitais em rede. Há modelos prontos para Behringer X32 / Midas M32 (porta 10023) e X Air (10024); outros modelos usam o mesmo esquema com o endereço OSC do canal.
- **HTTP**: o servidor faz `POST` de `{"canais":[{"canal":3,"aberto":true}]}` para uma ponte (Node-RED, Bitfocus Companion, sistema de conferência).
- **Canal da tribuna**: se o orador fala no microfone da tribuna, informe o canal dela.

Controles do presidente:

- **Congelar orador** (tecla C): para o tempo e corta o microfone até descongelar.
- **Congelar plenário**: tumulto; para todos os cronômetros, fecha todos os microfones e o telão mostra “Sessão congelada”.
- **Cortar microfone** de qualquer parlamentar: bloqueia até ser liberado (não abre nem na tribuna nem em aparte).
- **Abrir microfone de bancada** fora da tribuna.
- **Adicionar tempo**: +30 s, +1, +2, +5 min ou valor livre (tecla T), também para aparte.
- Todo corte e congelamento vai para **Ocorrências** (com motivo), exportável em CSV.

## Gravação das falas

No painel do presidente, **Gravar falas neste computador** grava a entrada de áudio escolhida (ligue a saída da mesa de som). Cada pronunciamento vira um arquivo Opus/WebM com o nome do orador, enviado ao servidor a cada 5 s (se o computador cair, perde-se no máximo 5 s). As gravações ficam em `dados/gravacoes/` e aparecem em **Relatórios**, com player e download. O telão mostra “REC” enquanto grava.

O navegador só libera o microfone em conexão segura: grave abrindo o painel em `http://localhost:8090` no próprio servidor, ou publique o sistema com HTTPS. Com `ffmpeg` instalado (já incluído na imagem Docker), o servidor corrige a duração dos arquivos ao fim de cada fala.

## Relatórios

Tempo de fala por partido e por parlamentar, lista de pronunciamentos com apartes e cessões, e exportação em CSV da ata de tempos e das votações.
