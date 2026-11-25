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

Abra `http://localhost:8090`. Em outros computadores e celulares da mesma rede, use o IP da máquina (por exemplo `http://192.168.0.10:8090`).

Com Docker:

```bash
PIN_PRESIDENTE=4321 docker compose up -d --build
```

Variáveis de ambiente:

- `PORTA`: porta HTTP (padrão 8090)
- `PIN_PRESIDENTE`: PIN do painel do presidente (padrão 1234; troque em produção)
- `PARLATORIO_BANCO`: caminho do banco SQLite (padrão `dados/parlatorio.db`)

Os parlamentares de exemplo começam com PIN `0000`. Troque em **Plenário → Editar**.

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
