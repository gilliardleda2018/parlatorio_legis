# Parlatório Legis

Controle do tempo de fala em plenário: painel do presidente/secretaria + telão público, sincronizados em tempo real.

## Como usar

Abra `index.html` (ou sirva a pasta, por exemplo `python3 -m http.server`).

- `index.html`: painel do presidente
- `index.html#telao`: telão do plenário (botão **Abrir telão**; duplo clique deixa em tela cheia)

Painel e telão precisam estar no mesmo navegador/computador (sincronização via `BroadcastChannel` + `localStorage`).

## Funcionalidades

- Fases configuráveis com tempo regimental por orador (Pequeno Expediente, Grande Expediente, Comunicações de Liderança, Ordem do Dia, Encaminhamento, Explicação Pessoal, Tribuna Livre)
- Lista de inscrição por fase, com reordenação e palavra concedida fora da ordem
- Cronômetro com aviso amarelo, vermelho piscando e sinal sonoro
- Corte automático do microfone (simulado) ao fim do tempo, e concessão de tempo extra
- Aparte (opcionalmente descontado do tempo do orador), questão de ordem e pela ordem, cada um com cronômetro próprio
- Histórico e exportação da ata de tempos em CSV
- Atalhos: Espaço (iniciar/pausar), N (próximo), E (encerrar), + (1 minuto)
