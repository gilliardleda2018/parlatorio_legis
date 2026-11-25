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

## Relatórios

Tempo de fala por partido e por parlamentar, lista de pronunciamentos com apartes e cessões, e exportação em CSV da ata de tempos e das votações.
