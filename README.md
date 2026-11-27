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
- `PIN_PRESIDENTE`: PIN inicial do painel do presidente. Sem ele, o servidor sorteia um PIN de 6 dígitos na primeira partida e o mostra no log (`PIN inicial do presidente: …`) até a primeira troca
- `TRANSCRICAO`: `auto` (padrão), `local`, `api`, `comando` ou `0` — veja **Transcrição dos discursos**
- `HOST`: endereço onde o servidor escuta (padrão `0.0.0.0`; use `127.0.0.1` atrás de um proxy)
- `PROXY_CONFIAVEL=1`: atrás de proxy (Caddy, nginx, Tailscale Funnel), usa o IP real do visitante no bloqueio por tentativas; `PROXY_IPS` diz de onde o proxy conecta: IPs ou faixas (padrão `127.0.0.1,::1`; em Docker, a rede do contêiner, ex. `172.26.0.0/16`)
- `TROCA_PIN=0`: período de testes — deixa entrar com o PIN padrão (0000) sem obrigar a troca. Não use em produção.
- `PARLATORIO_BANCO`: caminho do banco SQLite (padrão `dados/parlatorio.db`)
- `MAX_CONEXOES`: conexões simultâneas aceitas (padrão 800); `TEMPO_LIMITE_CONEXAO`: segundos até uma conexão parada cair (padrão 60)
- `RADIO_MAX_GB`: espaço máximo da programação da rádio (padrão 20)

Cada parlamentar começa com um **PIN inicial aleatório e próprio** (6 dígitos), que o presidente vê em **Plenário → PINs iniciais** e entrega a cada um. Ninguém consegue entrar na conta de um colega com um PIN conhecido de todos. O painel começa com o `PIN_PRESIDENTE` (ou o sorteado, mostrado no log). No primeiro acesso o sistema **obriga a troca** por um PIN pessoal, e o inicial some da lista. Bancos antigos em que vereadores ainda usavam `0000` recebem PINs iniciais novos na primeira partida desta versão. Com `TROCA_PIN=0` (testes), todos continuam com `0000`.

## Segurança de acesso

- PINs guardados só como hash (PBKDF2-SHA256 com sal), nunca em texto. Bancos antigos são convertidos ao iniciar.
- Após 5 PINs errados na mesma conta (ou 20 do mesmo endereço), novas tentativas ficam bloqueadas por 15 minutos. A tentativa é contada antes de o PIN ser conferido, então pedidos em paralelo não escapam do limite.
- PIN inicial, óbvio (0000, 1234, 1111, sequências) ou, no painel, com menos de 6 dígitos obriga a troca antes de qualquer outra coisa: até trocar, o servidor só responde a `/api/eu` e à troca de PIN.
- A auditoria nunca grava PIN, senha, chave ou token, e no voto secreto registra só que a pessoa votou. Os logins ficam guardados só como hash: um backup do banco não abre sessões.
- As páginas não podem ser abertas dentro de outro site (`Content-Security-Policy: frame-ancestors 'self'` e `X-Frame-Options`), e o endereço da mesa de som e a pasta do backup não vão para as telas públicas.
- Pedidos malformados, conexões paradas e excesso de conexões são recusados sem derrubar o servidor.
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

Na primeira execução o servidor cria, com `openssl`, uma autoridade certificadora própria da casa (`dados/tls/ca.crt`) e um certificado válido para `localhost`, o nome da máquina e os IPs da rede. A autoridade só pode assinar nomes e IPs da rede local (e os de `TLS_NOMES`), nunca um site da internet: mesmo que o servidor seja invadido, ela não serve para interceptar outros sites nos aparelhos. Isso vale para autoridades criadas a partir desta versão; para trocar uma antiga, apague `dados/tls/` e reinstale o certificado nos aparelhos. Depois:

1. Em cada computador, tablet e celular, abra `http://IP-do-servidor:8090/certificado`. A página reconhece o aparelho e instala o certificado do jeito mais curto: no Windows, um `.cmd` que confia na autoridade só para o usuário (sem administrador) e abre o LEGIS; no iPhone/iPad/Mac, um perfil `.mobileconfig`; no Android e no Linux, o `.crt` com o caminho de cada um. Ela testa o `https://` e mostra **Abrir o LEGIS** quando o aparelho já confia. O arquivo puro continua em `/certificado.crt` (e em `/certificado` para quem não é navegador, como o `curl`).
2. Use sempre `https://IP-do-servidor:8443`. Quem abrir pelo endereço `http://` é redirecionado (menos em `localhost`).

Com HTTPS, a gravação das falas funciona de qualquer computador da rede, não só no servidor. Se o IP ou o nome do servidor mudar, o certificado do servidor é renovado sozinho (na partida e a cada 5 minutos com o LEGIS ligado), e também 30 dias antes de vencer. A autoridade é mantida: os aparelhos não precisam reinstalar nada. Para forçar: `python3 server/servidor.py --renovar-certificado`.

Em servidor com domínio público, use um proxy com Let's Encrypt (Caddy, por exemplo: `parlatorio.camara.gov.br { reverse_proxy localhost:8090 }`) e rode o Parlatório com `HTTPS=0`.

## Passkey no voto (biometria do celular)

Inspirado no Infoleg da Câmara dos Deputados (voto pelo aplicativo com identificação biométrica) e no ponto biométrico das câmaras municipais: o vereador confirma voto e presença com a digital, o rosto ou o bloqueio de tela do próprio celular (WebAuthn / passkey), em vez de digitar o PIN.

- **Cadastro** (opcional): no app do parlamentar, cartão **🔐 Passkey** → *Cadastrar este celular*. Pede o PIN (uma sessão roubada não cadastra passkey própria). Dá para ter mais de um aparelho, ver quando cada um foi usado e remover (também com o PIN).
- **Presidência** (Configurações → *Passkey no voto*): *Desligado* (padrão, só PIN), *Passkey ou PIN* (o app usa a biometria e, se ela for cancelada, pede o PIN) ou *Exigir passkey de quem cadastrou* (voto e destaque da sessão virtual, voto e presença do plenário só com a passkey). Quem não cadastrou continua como antes. A presidência vê quem tem passkey e pode **revogar todas** de um vereador (celular perdido).
- **Endereço**: WebAuthn exige contexto seguro e não aceita IP. Abra pelo nome, com HTTPS: `https://<nome-da-maquina>.local:8443` (o certificado local já inclui o nome da máquina) ou o domínio da Câmara. Pelo IP, o app esconde o botão e explica. A passkey fica presa ao nome usado no cadastro. Atrás de proxy (Caddy etc. com `HTTPS=0`), ligue `PROXY_CONFIAVEL=1`: o servidor usa o `X-Forwarded-Proto: https` do proxy.
- **Como o servidor confere** (`server/passkey.py`, só biblioteca padrão): desafio aleatório de uso único, válido por 2 minutos, por conta; `clientDataJSON` (tipo, desafio, origem igual ao endereço do LEGIS); hash do rpId; flags de presença (UP) e de verificação do usuário (UV, exigida; `PASSKEY_UV=0` aceita sem); contador de assinaturas sempre crescente (se o aparelho usa contador); e a assinatura sobre `authenticatorData || sha256(clientDataJSON)`. Algoritmos ES256 (P-256) e RS256. O CBOR do `attestationObject` e as contas de ECDSA/RSA são feitos em Python puro: verificar usa só a chave pública, então não há segredo a vazar e não dependemos de chamar o `openssl` com arquivos temporários. Attestation "none": o fabricante do aparelho não é conferido; a confiança vem do cadastro feito pelo próprio vereador com o PIN.
- Asserções inválidas contam no mesmo bloqueio de tentativas do PIN. Cadastro, remoção, revogação, mudança de opção e recusas vão para a auditoria, sem nenhum dado da credencial.
- **Testes** (`tests/test_passkey.py`): um autenticador simulado com chave gerada pelo `openssl`. Só para testes, `PASSKEY_ORIGEM` e `PASSKEY_RP` fixam a origem e o rpId esperados (ex.: `https://legis.teste:8443` e `legis.teste`); em produção deixe vazias: vale o host HTTPS do pedido.

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

## Instalar num servidor Debian limpo (recomendado)

Num Debian 12 ou 13, ou Ubuntu 24.04, recém-instalado, um comando instala tudo, **sem perguntas**: LEGIS, rádio da Câmara, transcrição e legendas, IA local (se houver 8 GB de RAM), transmissão para rádio e YouTube. No primeiro acesso, o painel do presidente pergunta a cidade e traz do TSE os vereadores eleitos (com fotos) e do IBGE a população. Para publicar na internet pelo Tailscale, passe a chave: `sudo TS_AUTHKEY=tskey-auth-... bash instalar-legis.sh`. Para escolher tudo na instalação: `--perguntar`.

```bash
curl -fsSLO https://github.com/gilliardleda2018/parlatorio_legis/releases/latest/download/instalar-legis.sh
sudo bash instalar-legis.sh
```

Sem internet boa no local, leve o **pacote completo** `legis-completo-<versão>.tar.gz` (instalador, `.deb` e LEIA-ME) num pendrive e rode `sudo bash instalar-legis.sh` dentro da pasta. O servidor ainda precisa de internet para baixar as dependências do Debian. Instalação sem perguntas: `sudo UF=ma MUNICIPIO=Tuntum NOME=tuntum RADIO=s TRANSCRICAO=s IA=n bash instalar-legis.sh`. Para gerar o pacote: `bash packaging/construir-pacote.sh`. Para publicar uma versão nova (testes, pacote e release no GitHub): `bash packaging/publicar-versao.sh "Título"`.

## Pacote de uma Câmara (tudo em um arquivo só)

Para entregar a uma Câmara, gere o pacote dela. É **um único `.deb`** que já traz o município, o nome da Câmara, o brasão, os vereadores eleitos com foto (TSE) e a população (IBGE):

```bash
bash packaging/construir-cidade.sh MA "Tuntum" --brasao brasao.png --youtube-link https://www.youtube.com/@camaratuntum/live
```

Sai em `dist/cidades/tuntum/`: `legis-tuntum_<versão>_all.deb`, o `.sha256` e `legis-tuntum-completo-<versão>.tar.gz` (com o instalador e o LEIA-ME). No servidor da Câmara (Debian 12/13 ou Ubuntu 24.04 limpo), dois cliques no `.deb`, ou:

```bash
sudo YOUTUBE_CHAVE=xxxx-xxxx-xxxx-xxxx bash instalar-legis.sh      # dentro da pasta do .tar.gz
```

Não pergunta nada e não depende do TSE na hora. Já sai pronto:

| | |
|---|---|
| LEGIS | Câmara, brasão, vereadores com foto e PINs iniciais |
| Rádio da Câmara | sobe, reinicia e para junto com o LEGIS |
| Videoconferência (Jitsi) | servidor próprio da Câmara, só para quem está logado no LEGIS; a sessão híbrida já aponta para ele |
| YouTube ao vivo | com a chave passada na instalação (`YOUTUBE_CHAVE=…`), o botão Transmitir já manda a sessão para o canal. A chave nunca vai dentro do pacote; sem ela, cola-se no painel |
| Transcrição e legendas, IA local | transcrição sempre; IA se houver 8 GB de RAM |
| Internet | com `TS_AUTHKEY=tskey-auth-…`, publica em `https://tuntum.<tailnet>.ts.net` |

Transcrição, IA, Jitsi e internet terminam em segundo plano. Acompanhe com `sudo legis estado`.

### Painel dos pacotes

Para quem gera e entrega os pacotes, há um painel no navegador:

```bash
python3 packaging/painel.py          # abre http://127.0.0.1:8099
```

Nele dá para cadastrar uma Câmara (estado, município da lista do IBGE, nome, brasão, link do YouTube) e gerar o pacote dela. Também mostra quais pacotes estão desatualizados em relação ao código atual, gera de novo um ou todos, baixa os arquivos, copia o comando de instalação e gera e publica a versão geral no GitHub (com confirmação). Cada tarefa mostra o registro ao vivo. Para **instalar uma Câmara**, copie o comando dela no painel e rode no servidor da Câmara (`curl -fsSL http://IP-DESTE-COMPUTADOR:8098/i/<nome>/<código> | sudo bash`): ele baixa o pacote deste computador pela rede local ou pelo Tailscale, confere o SHA-256 e instala sem perguntas. O botão **Publicar** manda só a versão geral (sem cidade) ao GitHub; os pacotes das Câmaras não são publicados. A configuração das Câmaras fica em `dados/camaras/` (fora do Git) e os pacotes em `dist/`. Neste computador abre direto em http://127.0.0.1:8099. Para usar **de qualquer lugar**, rode `bash packaging/instalar-painel.sh`: o painel vira um serviço sempre ligado e é publicado pelo Tailscale Funnel em `https://<esta máquina>.<tailnet>.ts.net:10000`, pedindo a chave de acesso guardada em `dados/painel/chave` (fora do Git). As instalações pelo endereço público (`/i/…`) só funcionam com o código de cada Câmara.

## Instalador .deb (Debian 12/13, Ubuntu 24.04)

```bash
sudo apt install ./legis_<versão>_all.deb
```

O `apt` pergunta o município e já importa os vereadores do TSE, gera o PIN do presidente e o certificado e sobe o serviço. Transcrição, IA local e publicação pelo Tailscale são opcionais e instalam em segundo plano. Também aceita instalação sem perguntas (debconf). Guia: [deploy/deb/README.md](deploy/deb/README.md). Para gerar o pacote: `bash packaging/construir-deb.sh`.

## Instalar em uma nova Câmara com Docker (alternativa)

Alternativa ao pacote acima, em contêineres (sem a rádio da Câmara). Um comando num servidor Debian novo instala: LEGIS, vereadores eleitos do TSE (com fotos), população do IBGE, IA local, transcrição e legendas, e publicação na internet pelo Tailscale. Passo a passo e checklist em [deploy/cidade/README.md](deploy/cidade/README.md).

```bash
curl -fsSL https://raw.githubusercontent.com/gilliardleda2018/parlatorio_legis/main/deploy/cidade/instalar.sh -o instalar.sh
sudo bash instalar.sh
```

## Publicar na internet

Para rodar **dentro da Câmara** (on-premise, servidor Debian na rede do plenário, controlando a mesa de som), veja [deploy/local/README.md](deploy/local/README.md). Mesas suportadas: Behringer X32/M32, X Air, WING; Yamaha TF/DM3/DM7/QL/CL/Rivage; Allen & Heath Qu/dLive/Avantis/SQ; Soundcraft Ui; qualquer mesa OSC; Bitfocus Companion ou Node-RED por ponte HTTP; relés e centrais de conferência com URL por canal.

O roteiro para a AWS (EC2 em São Paulo, HTTPS automático com Caddy no domínio da câmara) está em [deploy/aws/README.md](deploy/aws/README.md), com o instalador `deploy/aws/instalar.sh`, que serve para qualquer servidor Ubuntu.

### Publicar pelo Tailscale Funnel (sem abrir portas no roteador)

Com Docker, um contêiner do Tailscale com nome próprio publica o LEGIS em `https://legis.<sua-tailnet>.ts.net`, com certificado válido e sem depender de IP fixo nem do roteador. A configuração está em `deploy/tailscale/serve.json`. Gere uma chave em https://login.tailscale.com/admin/settings/keys e suba uma vez com ela (depois o login fica salvo em `dados/tailscale-legis`):

```yaml
# docker-compose.override.yml
services:
  tailscale-legis:
    image: tailscale/tailscale:stable
    hostname: legis
    restart: unless-stopped
    environment: { TS_AUTHKEY: "${TS_AUTHKEY:-}", TS_HOSTNAME: legis, TS_STATE_DIR: /var/lib/tailscale, TS_USERSPACE: "true", TS_SERVE_CONFIG: /config/serve.json }
    volumes: ["./dados/tailscale-legis:/var/lib/tailscale", "./deploy/tailscale/serve.json:/config/serve.json:ro"]
```

Em `dados/parlatorio.env`, use `PROXY_CONFIAVEL=1` e `PROXY_IPS` com a faixa da rede Docker (ex.: `172.26.0.0/16`), para o bloqueio de tentativas usar o IP real do visitante.

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

## O que veio do Senado e da Câmara dos Deputados

Recursos inspirados nos sistemas do Congresso Nacional, adaptados à Câmara Municipal:

| No LEGIS | Inspirado em | O que faz |
|---|---|---|
| Orientação de bancada | Painel de votação da Câmara | Na votação nominal, o líder orienta a bancada (Sim, Não, Liberado, Obstrução) pelo celular; aparece no painel e no telão, e o resultado aponta quem votou diferente da orientação |
| Enquetes | Enquetes da Câmara | O cidadão diz se concorda ou discorda (totalmente ou na maior parte) de cada projeto em tramitação; resultado público, com aviso de que não tem rigor estatístico |
| Ideia Legislativa | e-Cidadania do Senado | O cidadão propõe uma ideia e outros apoiam; com a meta de apoios (0,3% da população) em 120 dias, vai à análise da Mesa, que responde publicamente |
| Perguntas ao vivo | Evento Interativo do Senado | Durante a sessão ou audiência, o público envia perguntas em /participe; a presidência aprova e mostra a escolhida no telão |
| Análise prévia da proposição | Programa Ulysses da Câmara | Comissões sugeridas e alertas jurídicos (vício de iniciativa, despesa, renúncia de receita, competência da União) por regras, e resumo em linguagem simples pela IA; sempre rascunho até a revisão humana |
| Pergunte ao Regimento | Ulysses Chat da Câmara | A Mesa e os vereadores perguntam sobre o Regimento Interno e a Lei Orgânica (cadastrados em PDF ou texto) e recebem a resposta com o artigo citado |

O LEGIS já tinha os equivalentes de outros sistemas do Congresso: presença e voto pelo celular com PIN (Infoleg), sessão híbrida e plenário virtual (Sistema de Deliberação Remota), consulta pública artigo a artigo (Wikilegis), dados abertos e Akoma Ntoso (LexML) e importação do SAPL (Interlegis).

## YouTube ao vivo

Ao clicar em **📡 Transmitir**, a sessão também vai para o canal da Câmara no YouTube, junto com a rádio ou sozinha:

- **Com câmera:** escolha a câmera ou a placa de captura no cartão de transmissão (aba **Sessão**). O computador da mesa envia a imagem com o áudio da mesa de som, e o LEGIS põe por cima uma faixa com o nome da Câmara, o selo AO VIVO, o orador (nome, partido e fase) e o cronômetro.
- **Sem câmera:** o LEGIS monta uma **tela automática** com o nome e o brasão da Câmara, a foto, o nome e o partido de quem está na tribuna, a fase, o cronômetro e a votação em curso, sempre atualizados.

Para configurar, abra o **YouTube Studio → Criar → Transmitir ao vivo → Stream**, copie a **chave de transmissão** e cole em **Configurações → YouTube ao vivo**. Na primeira vez, o YouTube pode levar até 24 h para liberar as lives do canal. Em "Link público", ponha o endereço da live (por exemplo `https://www.youtube.com/@suacamara/live`), e as páginas públicas mostram **▶ Assistir no YouTube** enquanto estiver no ar.

A chave fica só no servidor e nunca vai para as telas. O vídeo sai em H.264 720p a 30 quadros por segundo, com áudio AAC, pelo RTMPS do YouTube. Isso usa cerca de 2,5 Mbps de upload com câmera e 1,5 Mbps com a tela automática. A codificação ocupa cerca de um núcleo do processador com câmera e bem menos com a tela automática.

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

## Auditoria íntegra, recibo de voto e governança da IA

Lógica em `server/integridade.py`; telas públicas `/verificar` e `/ia`.

- **Auditoria à prova de adulteração.** Cada linha nova da tabela `eventos` guarda `hash = sha256(hash_anterior|ts|autor|acao|args)`. Na primeira partida de um banco antigo, a coluna `hash` é criada (migração idempotente) e as linhas que já existiam ficam sem hash: elas formam o **início** da cadeia e não podem ser conferidas; a partir da primeira linha com hash, alterar, apagar ou inserir uma linha no meio quebra a conferência. `GET /api/integridade` (público) recalcula tudo e devolve `{ok, total, inicio, encadeados, ultimo_hash, primeira_quebra, selos, selos_ausentes}`.
- **Selo da ata.** Ao encerrar e arquivar a sessão, o último hash da cadeia vai para o registro da sessão (`selo`) e para o fim da ata em PDF (“Selo de integridade: …”). Em `/verificar`, quem guardou a ata cola o selo e sabe se ele bate com uma sessão arquivada e se ainda está na cadeia (apagar o fim do registro e refazer a corrente também é percebido, porque o selo some).
- **Recibo do voto nominal.** Depois de votar, o app do parlamentar mostra um recibo de 10 caracteres (HMAC-SHA256 com chave secreta do servidor sobre votação + parlamentar + voto) e o código da votação (`GET /api/recibo?votacao=<id>`, só para o próprio parlamentar). Em `/verificar` qualquer pessoa confere o par votação + recibo e recebe só “confere” ou “não confere”. Se o voto mudar antes do encerramento, vale o recibo do último voto. **Voto secreto nunca tem recibo** (só “seu voto foi contado”) e a auditoria continua sem o valor. A chave fica no estado do servidor (`_chaveRecibo`, nunca vai para as telas): restaurar um backup antigo do banco mantém a chave; apagar o banco invalida os recibos antigos.
- **Uso de IA** (inspirado nas Diretrizes para IA nos Parlamentos da UIP, dez/2024, e na Portaria 227/2025 da Câmara dos Deputados). Toda chamada ao modelo — rascunho da ata, análise prévia, Pergunte ao Regimento, assistente do cidadão — fica na tabela `uso_ia` (data, recurso, modelo, tamanho e hash curto da entrada; nunca o texto das perguntas nem IP). Em **Relatórios → Rascunho da ata**, o presidente marca o rascunho como revisado, com o nome de quem revisou (ação `iaRevisarAta`); a ata em PDF e o painel mostram “Rascunho gerado por IA (modelo), revisado por … em …” ou “não revisado”. Respostas imediatas da IA (Regimento, assistente) vêm com o aviso de que não tiveram revisão humana. A página pública `/ia` mostra a política de uso de IA (texto padrão, editável em **Configurações → Uso de IA e integridade**) e as estatísticas do registro: usos por recurso e % dos conteúdos revisados.
