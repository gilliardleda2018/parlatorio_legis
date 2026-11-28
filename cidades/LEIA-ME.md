# Modelo de cidade

O LEGIS é o mesmo para qualquer Câmara do Brasil. Tudo o que muda de uma cidade para outra fica num arquivo só:
`cidades/<nome>/camara.json`, com o brasão ao lado. Todo pacote já vem com rádio, certificado HTTPS, plenário
virtual e sessão remota. A instalação não pergunta nada.

| Pasta | O que é |
|---|---|
| `modelo/` | Ponto de partida. Copie para criar uma cidade nova. |
| `tuntum/` | Câmara Municipal de Tuntum (MA). Pacote já em uso e **congelado**: as versões novas do LEGIS não regeneram o pacote dela sozinhas. |

## Uma cidade nova em 3 passos

1. Copie a pasta e preencha o modelo. Só `uf` e `municipio` são obrigatórios:

   ```bash
   cp -r cidades/modelo cidades/barra-do-corda
   ```

2. Ponha o brasão ao lado (`brasao.png`, PNG, JPEG ou WebP de até 1,5 MB) e confira o modelo:

   ```bash
   python3 packaging/modelo.py validar cidades/barra-do-corda/camara.json
   ```

3. Escolha uma das três formas de instalar:

   - **Pelo GitHub.** Faça commit da pasta na `main`. O fluxo "Versão" publica a release `camara-barra-do-corda` com um
     comando de instalação que nunca muda: `curl -fsSL …/releases/download/camara-barra-do-corda/instalar.sh | sudo bash`.
     Também dá para usar o formulário **Actions → Pacote de uma Câmara**, com o campo *modelo* =
     `cidades/barra-do-corda/camara.json`.
   - **Direto no servidor, sem gerar pacote:**
     `sudo bash instalar-legis.sh --modelo https://raw.githubusercontent.com/<repo>/main/cidades/barra-do-corda/camara.json`.
     Também serve um arquivo local: `--modelo camara.json`.
   - **Pacote para levar num pendrive:** `bash packaging/construir-cidade.sh --modelo cidades/barra-do-corda/camara.json`.
     O resultado sai em `dist/cidades/barra-do-corda/`.

## Campos

| Campo | Padrão | Para quê |
|---|---|---|
| `uf`, `municipio` | (obrigatórios) | Os vereadores eleitos (com foto) vêm do TSE e a população do IBGE |
| `nome` | o município sem acento | Endereço `https://<nome>.local` na rede da Câmara (é também o nome da máquina) |
| `camara` | Câmara Municipal de \<município\> | Nome nas telas, na ata e na rádio |
| `brasao` | o do Wikidata | Arquivo ao lado do `camara.json`. Em branco, o LEGIS busca o brasão do município no Wikidata/Wikimedia Commons pelo código do IBGE (só domínio público ou CC0) |
| `congelada` | `false` | `true`: as versões novas não regeneram o pacote. Para atualizar de propósito, use `--forcar` |
| `visual.destaque`, `visual.tema` | `#1f8a4c`, `escuro` | Cor de destaque e tema das telas |
| `contatos.*` | — | Rodapé público: endereço, telefone, WhatsApp, e-mail, site e redes |
| `radio.ligada`, `radio.nome`, `radio.bitrate` | `true`, \<Câmara\> — Rádio, 128 | A rádio própria da Câmara (programação + sessões ao vivo em `/radio.mp3`) |
| `plenarioVirtual.ligado` | `true` | Plenário virtual (votação online com prazo) e sessão remota já prontos |
| `plenarioVirtual.videoconferencia` | `propria` | `propria` (Jitsi da Câmara, no próprio servidor), `publica` (meet.jit.si) ou `nenhuma` |
| `certificado.nomesExtras` | `[]` | Domínios ou IPs a mais no certificado (ex.: `camara.barradocorda.ma.leg.br`) |
| `portal.url` | descoberto pelo nome | Portal da Câmara na plataforma legislativomunicipal.com (ex.: `https://www.cmtuntum.legislativomunicipal.com`). Em branco, o LEGIS procura `cm<município>`. Na instalação, ele importa de lá as sessões, as matérias, os ex-vereadores, o nome civil e a presença, como em Tuntum |
| `youtube.link` | — | Link público da live. A chave de transmissão **nunca** vai no modelo: entra na instalação (`YOUTUBE_CHAVE=…`) ou no painel |
| `recursos.transcricao`, `recursos.ia` | `auto` | `auto` (decide pela memória da máquina), `sim` ou `nao` |
| `regimento.tempos` | alerta 60, aparte 60, ordem 180 | Segundos do aviso de fim de fala, do aparte e da questão de ordem |
| `regimento.fases` | — | Tempo por fase, ex.: `{"ge": {"min": 20, "totalMin": 80}}`. As fases são `pe`, `ge`, `cl`, `od`, `ev`, `ep` e `tl` |

Campos começados com `_` (como `_comentario`) são só anotações. Um campo com nome errado faz a validação falhar, em
vez de ser ignorado sem aviso.

O modelo é aplicado **uma vez por banco**, na primeira partida. Depois vale o que a Câmara mudar no painel. Para
aplicar de novo: `sudo legis modelo --reaplicar`.

## O que já vem em todo pacote

- **Rádio da Câmara:** programação contínua e as sessões ao vivo, servidas pelo próprio LEGIS.
- **Certificado HTTPS:** é criado na instalação. Cada Câmara tem a sua autoridade, que só vale para a rede local, e a
  chave nunca sai do servidor. O kit para os aparelhos fica pronto em `/var/lib/legis/certificado-aparelhos`
  (`sudo legis certificado /media/<pendrive>`). Com rede, basta abrir `http://<nome>.local/certificado` em cada
  aparelho.

  > Por que o certificado não vem dentro do pacote: o mesmo pacote é público e serve várias cidades. Uma chave
  > embutida seria a mesma em todas as Câmaras e estaria à vista de qualquer pessoa.
- **Plenário virtual e sessão remota:** prontos no primeiro acesso, com a videoconferência própria (Jitsi) quando a
  máquina tem 4 GB de RAM.
- **Instalação eficiente:**
  - antes de começar, confere disco, internet e portas;
  - não atualiza a lista de pacotes se ela tem menos de 6 horas;
  - baixa a transcrição, a IA e a videoconferência **em paralelo**, em segundo plano;
  - no fim, mostra quanto tempo levou.
