/* Painel do Presidente */
const A = (nome, args) => PL.acao('presidente', nome, args).catch(() => {});
const $ = id => document.getElementById(id);
const { esc, avatar, sigla, parl, fase, tempo, hora } = PL;
let abaAtual = 'sessao';
let selInterr = '';
let som = localStorage.getItem('parlatorio.som') !== '0';
const Cidade = { pronto: false, aberto: false, municipios: [] };  // primeira configuração (presidente2.js)

/* ---------------- entrada ---------------- */
async function iniciar() {
  PL.conectar();
  const eu = await PL.quemSou('presidente');
  $(eu ? 'app' : 'login').classList.remove('oculto');
  if (!eu) $('pin').focus();
  if (eu?.deveTrocar) PL.exigirTrocaPin('presidente');
  else if (eu) { Cidade.pronto = true; if (PL.estado) verificarCidade(PL.estado); }
}
async function entrar() {
  try { await PL.entrar('presidente', $('pin').value); location.reload(); }
  catch (e) { PL.avisar(e.message, true); $('pin').select(); }
}

/* ---------------- abas ---------------- */
document.querySelectorAll('[data-aba]').forEach(b => b.onclick = () => {
  abaAtual = b.dataset.aba;
  document.querySelectorAll('[data-aba]').forEach(x => x.classList.toggle('ativo', x === b));
  document.querySelectorAll('.aba').forEach(s => s.classList.toggle('ativa', s.id === 'aba-' + abaAtual));
  if (abaAtual === 'config') carregarConfig();
  if (abaAtual === 'sessoes') carregarSessoes();
  if (abaAtual === 'projetos') carregarProjetos();
  desenhar();
});

/* ---------------- desenho ---------------- */
PL.aoMudar(() => desenhar());
let projetosRev = null;
PL.aoMudar(S => { if (abaAtual === 'projetos' && S.projetosRev !== projetosRev) carregarProjetos(); });
PL.aoSinal = tipo => { if (som) PL.bipe(tipo); };
function desenhar() {
  const S = PL.estado; if (!S) return;
  $('bolhaPresenca').textContent = Object.keys(S.presenca).length + '/' + S.parls.length;
  $('btSom').textContent = som ? '🔔' : '🔕';
  if (abaAtual === 'sessao') desenharSessao();
  if (abaAtual === 'pauta') desenharPauta();
  if (abaAtual === 'plenario') desenharPlenario();
  if (abaAtual === 'relatorios') desenharRelatorios();
  if (abaAtual === 'sessoes') desenharSessaoAtual();
  if (abaAtual === 'virtual') desenharVirtual();
  relogios();
}
$('btSom').onclick = () => { som = !som; localStorage.setItem('parlatorio.som', som ? '1' : '0'); desenhar(); if (som) PL.bipe('alerta'); };

function fila() { return PL.estado.filas[PL.estado.faseAtual] || []; }

function desenharSessao() {
  const S = PL.estado;
  $('fases').innerHTML = S.fases.map(f => {
    const n = (S.filas[f.id] || []).length;
    const usado = PL.usadoNaFase(f.id), total = f.totalMin * 60000;
    return `<div class="fase ${f.id === S.faseAtual ? 'ativa' : ''}" onclick="A('setFase',{id:'${f.id}'})">
      <div class="t"><span>${esc(f.nome)} <small class="suave">· ${f.min} min</small></span><span class="conta">${n}</span></div>
      ${total ? `<div class="barra ${usado > total ? 'n-fim' : usado > total * .85 ? 'n-alerta' : ''}" data-fase="${f.id}"><i style="width:${Math.min(100, usado / total * 100)}%"></i></div>
      <small class="suave tnum" data-fase-txt="${f.id}">${tempo(usado)} de ${tempo(total)}</small>` : ''}</div>`;
  }).join('');

  const f = fila();
  $('filaTitulo').textContent = 'Inscritos — ' + fase(S.faseAtual).nome;
  $('filaConta').textContent = f.length;
  $('fila').innerHTML = f.length ? f.map((e, k) => {
    const p = parl(e.pid);
    return `<li><span class="ord">${k + 1}</span>${avatar(p)}
      <span class="nm"><b>${esc(p.nome)}</b>${sigla(p.partido)}${e.bonus ? ` <small>+${Math.round(e.bonus / 60)} min cedidos</small>` : ''}</span>
      <span class="acoes">
        <button class="botao pq" title="Subir" onclick="A('mover',{pid:'${e.pid}',d:-1})">↑</button>
        <button class="botao pq" title="Descer" onclick="A('mover',{pid:'${e.pid}',d:1})">↓</button>
        <button class="botao pq" title="Ceder o tempo a outro inscrito" onclick="ceder('${e.pid}')">Ceder</button>
        <button class="botao pq ok" onclick="A('chamar',{pid:'${e.pid}'})">Chamar</button>
        <button class="botao pq fantasma" title="Retirar inscrição" onclick="A('desinscrever',{pid:'${e.pid}'})">✕</button>
      </span></li>`;
  }).join('') : '<li class="vazio" style="background:none">Ninguém inscrito nesta fase.</li>';

  $('faixaCongelado').innerHTML = S.congelado
    ? `<div class="congelado-faixa"><span style="font-size:28px">❄</span><div><b>Plenário congelado</b><br>
        <span class="suave">${S.congelado.motivo ? esc(S.congelado.motivo) + ' · ' : ''}desde ${hora(S.congelado.inicio)} · todos os microfones fechados e cronômetros parados</span></div>
        <span class="esp"></span><button class="botao ok gr" onclick="A('descongelarPlenario')">▶ Retomar a sessão</button></div>`
    : `<div class="controle-plenario">
        <button class="botao pq fantasma" onclick="A('fecharTodos')" title="Fecha os microfones de bancada liberados">Fechar microfones de bancada</button>
        <button class="botao pq azul" onclick="congelarPlenario()" title="Tumulto: para todos os tempos e fecha todos os microfones">❄ Congelar plenário</button></div>`;

  desenharTribuna();
  desenharPedidos();
  $('quorum').innerHTML = quorumHtml();
  desenharParlsRapido();
  desenharMesaEGravacao();
}

function chipEstado(o) {
  const S = PL.estado;
  if (S.congelado) return `<span class="chip interr"><span class="ponto"></span>Plenário congelado · microfone fechado</span>`;
  if (o.congelado) return `<span class="chip interr"><span class="ponto"></span>❄ Tempo congelado · microfone cortado</span>`;
  if (!o.inicio) return `<span class="chip"><span class="ponto"></span>Aguardando início</span>`;
  if (o.esgotou && !o.start) return `<span class="chip corte"><span class="ponto"></span>Tempo esgotado · microfone cortado</span>`;
  if (S.interr) return `<span class="chip interr"><span class="ponto"></span>${PL.TIPOS[S.interr.tipo]} em curso</span>`;
  if (o.start) return `<span class="chip vivo"><span class="ponto"></span>Com a palavra · microfone aberto</span>`;
  return `<span class="chip"><span class="ponto"></span>Pausado · microfone fechado</span>`;
}

function desenharTribuna() {
  const S = PL.estado, o = S.orador, i = S.interr;
  const m = S.pauta.find(x => x.id === S.materiaAtual);
  $('faixaMateria').innerHTML = m ? `<div class="materia-faixa"><span class="chip">${esc(PL.SITUACOES[m.situacao])}</span>
    <span class="nm"><b>${esc(m.numero)}</b> — ${esc(m.ementa)}</span><button class="botao pq fantasma" onclick="A('materiaAtual',{})">Tirar da tela</button></div>` : '';

  const opcoes = S.parls.filter(p => p.id !== o?.pid).map(p => `<option value="${p.id}" ${p.id === selInterr ? 'selected' : ''}>${esc(p.nome)} (${esc(p.partido)})${S.presenca[p.id] ? '' : ' — ausente'}</option>`).join('');
  const blocoInterr = i ? `<div class="interrupcao"><span class="chip interr"><span class="ponto"></span>${PL.TIPOS[i.tipo]}</span>
      ${avatar(parl(i.pid))}<b>${esc(parl(i.pid).nome)}</b><span class="t tnum" id="tInterr"></span>
      <button class="botao pq" onclick="A('addTempoInterr',{seg:30})">+30 s</button>
      <button class="botao pq" onclick="A('encerrarInterr')">Encerrar ${PL.TIPOS[i.tipo].toLowerCase()}</button>
      <button class="botao pq perigo" onclick="cortar('${i.pid}')" title="Encerra e corta o microfone">Cortar microfone</button></div>`
    : `<div class="pedir"><select class="campo" id="selInterr" onchange="selInterr=this.value"><option value="">Parlamentar solicitante…</option>${opcoes}</select>
      <button class="botao" onclick="interromper('aparte')" ${o?.start ? '' : 'disabled'} title="Exige orador com a palavra">Aparte</button>
      <button class="botao" onclick="interromper('ordem')">Questão de ordem</button>
      <button class="botao" onclick="interromper('pelaordem')">Pela ordem</button></div>`;

  if (!o) {
    $('tribuna').innerHTML = `<p class="suave" style="font-size:20px;margin:26px 0 18px">Tribuna livre — nenhum orador.</p>
      <button class="botao principal gr" onclick="A('proximo')" ${fila().length ? '' : 'disabled'}>Chamar próximo inscrito</button>
      ${blocoInterr}`;
    return;
  }
  const p = parl(o.pid), f = fase(o.fase);
  const extras = [o.bonus ? `+${Math.round(o.bonus / 60)} min cedidos` : '', o.extra ? `${o.extra > 0 ? '+' : ''}${Math.round(o.extra)} s concedidos` : ''].filter(Boolean).join(' · ');
  $('tribuna').innerHTML = `
    <div class="contexto">${esc(f.nome)} · tempo regimental ${f.min} min${extras ? ' · ' + extras : ''}</div>
    <div class="quem">${avatar(p)}<div class="nm"><b>${esc(p.nome)}</b>${sigla(p.partido)}</div></div>
    ${chipEstado(o)}
    <div class="grande tnum" id="tOrador"></div>
    <div class="barra" id="bOrador"><i></i></div>
    <div class="grupo">
      ${o.congelado
        ? `<button class="botao azul gr" onclick="A('descongelar')" ${S.congelado ? 'disabled' : ''}>▶ Descongelar</button>`
        : `<button class="botao ${o.start ? '' : 'ok'} gr" onclick="alternar()" ${i?.pausouOrador || S.congelado ? 'disabled' : ''}>${o.start ? '⏸ Pausar' : o.inicio ? '▶ Retomar' : '▶ Iniciar'}</button>
           <button class="botao azul gr" onclick="congelarOrador()" title="Para o tempo e corta o microfone (tumulto, excesso)">❄ Congelar</button>`}
      <button class="botao perigo gr" onclick="A('encerrar')">■ Encerrar</button>
      <button class="botao principal gr" onclick="A('proximo')">Próximo ⏭</button>
    </div>
    <div class="grupo" style="margin-top:8px">
      <span class="detalhe" style="align-self:center">Adicionar tempo:</span>
      <button class="botao" onclick="A('addTempo',{seg:30})">+30 s</button>
      <button class="botao" onclick="A('addTempo',{seg:60})">+1 min</button>
      <button class="botao" onclick="A('addTempo',{seg:120})">+2 min</button>
      <button class="botao" onclick="A('addTempo',{seg:300})">+5 min</button>
      <button class="botao fantasma" onclick="tempoPersonalizado()">Outro…</button>
    </div>
    ${blocoInterr}
    <div class="atalhos"><kbd>Espaço</kbd> iniciar/pausar · <kbd>C</kbd> congelar · <kbd>N</kbd> próximo · <kbd>E</kbd> encerrar · <kbd>+</kbd> 1 minuto · <kbd>T</kbd> tempo</div>`;
}
function alternar() { const o = PL.estado.orador; if (o && !o.congelado) A(o.start ? 'pausar' : 'iniciar'); }

/* congelar, cortar, liberar */
function pedirMotivo(titulo, texto, rotulo, aoConfirmar) {
  dialogo(titulo, `<p style="margin:0">${texto}</p>
    <label class="rot">Motivo (vai para a ata)<input class="campo" id="dMotivo" list="dlMotivos" placeholder="Opcional"></label>
    <datalist id="dlMotivos"><option value="Tumulto no plenário"><option value="Excedeu o tempo regimental"><option value="Linguagem ofensiva"><option value="Fala fora do tema em debate"><option value="Desrespeito à presidência"></datalist>`,
    () => aoConfirmar($('dMotivo').value), rotulo);
  setTimeout(() => $('dMotivo').focus(), 50);
}
function congelarOrador() {
  const o = PL.estado.orador; if (!o) return;
  pedirMotivo('Congelar orador', `O tempo de <b>${esc(parl(o.pid).nome)}</b> para e o microfone é cortado até você descongelar.`, '❄ Congelar',
    motivo => A('congelar', { motivo }));
}
function congelarPlenario() {
  pedirMotivo('Congelar plenário', 'Todos os cronômetros param e <b>todos os microfones</b> são fechados até você retomar a sessão.', '❄ Congelar plenário',
    motivo => A('congelarPlenario', { motivo }));
}
function cortar(pid) {
  pedirMotivo('Cortar microfone', `O microfone de <b>${esc(parl(pid).nome)}</b> fica bloqueado: não abre nem na tribuna, nem em aparte, até você liberar.`, 'Cortar microfone',
    motivo => A('cortarMic', { pid, cortar: true, motivo }));
}
const liberar = pid => A('cortarMic', { pid, cortar: false });
function micBotoes(pid) {
  const m = PL.mic(pid);
  if (m === 'cortado') return `<button class="botao pq mic-bt cortado" title="Microfone cortado — clique para liberar" onclick="liberar('${pid}')">🔇</button>`;
  return `<button class="botao pq mic-bt ${m === 'aberto' || m === 'tribuna' ? 'aberto' : ''}" title="${m === 'aberto' ? 'Fechar microfone de bancada' : 'Abrir microfone de bancada'}"
      onclick="A('micManual',{pid:'${pid}',aberto:${m !== 'aberto'}})">🎙</button>
    <button class="botao pq mic-bt" title="Cortar microfone (bloquear)" onclick="cortar('${pid}')">✂</button>`;
}
function tempoPersonalizado() {
  const alvo = PL.estado.interr ? 'interr' : 'orador';
  dialogo('Adicionar tempo', `
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
      <label class="rot">Minutos<input class="campo" type="number" id="dMin" value="3" min="0"></label>
      <label class="rot">Segundos<input class="campo" type="number" id="dSeg" value="0" min="0" max="59"></label>
    </div>
    <label class="rot">Para<select class="campo" id="dAlvo"><option value="orador">Orador na tribuna</option>${PL.estado.interr ? `<option value="interr" selected>${PL.TIPOS[PL.estado.interr.tipo]} em curso</option>` : ''}</select></label>
    <label class="linha">Retirar tempo em vez de adicionar<input type="checkbox" id="dTirar"></label>`,
    () => {
      const seg = ((+$('dMin').value || 0) * 60 + (+$('dSeg').value || 0)) * ($('dTirar').checked ? -1 : 1);
      if (!seg) return;
      return A($('dAlvo').value === 'interr' ? 'addTempoInterr' : 'addTempo', { seg });
    }, 'Aplicar');
  setTimeout(() => $('dMin').select(), 50);
}
function interromper(tipo) {
  if (!selInterr) { $('selInterr').focus(); PL.avisar('Escolha o parlamentar solicitante.', true); return; }
  A('interromper', { tipo, pid: selInterr }).then(() => { selInterr = ''; });
}

function desenharPedidos() {
  const S = PL.estado;
  $('pedidosConta').textContent = S.pedidos.length;
  $('pedidos').innerHTML = S.pedidos.length ? S.pedidos.map(x => {
    const p = parl(x.pid);
    return `<li>${avatar(p)}<span class="nm"><b>${esc(p.nome)}</b><small>${PL.TIPOS[x.tipo]}${x.tipo === 'inscricao' ? ' · ' + esc(fase(x.fase).nome) : ''} · ${hora(x.ts)}</small></span>
      <span class="acoes"><button class="botao pq ok" onclick="A('atenderPedido',{id:'${x.id}',aprovar:true})">${x.tipo === 'inscricao' ? 'Inscrever' : 'Conceder'}</button>
      <button class="botao pq fantasma" title="Recusar" onclick="A('atenderPedido',{id:'${x.id}',aprovar:false})">✕</button></span></li>`;
  }).join('') : '<li class="vazio" style="background:none">Nenhum pedido pendente.</li>';
}

function quorumHtml() {
  const q = PL.quorum();
  return `<div class="quorum"><span class="num tnum ${q.ok ? 'n-ok' : 'n-alerta'}">${q.presentes}<small class="suave" style="font-size:16px">/${q.n}</small></span>
    <div style="flex:1"><div class="barra ${q.ok ? '' : 'n-alerta'}"><i style="width:${q.presentes / q.n * 100}%"></i></div>
    <small class="suave">${q.ok ? 'Quórum atingido' : `Faltam ${q.minimo - q.presentes} para o quórum`} · mínimo ${q.minimo}</small></div></div>`;
}

$('busca').oninput = () => desenharParlsRapido();
function desenharParlsRapido() {
  const S = PL.estado, q = $('busca').value.toLowerCase(), f = fila();
  $('parlsRapido').innerHTML = S.parls.filter(p => (p.nome + ' ' + p.partido).toLowerCase().includes(q)).map(p => {
    const inscrito = f.some(e => e.pid === p.id), tribuna = S.orador?.pid === p.id, pres = !!S.presenca[p.id];
    return `<li style="${pres ? '' : 'opacity:.55'}">${avatar(p)}<span class="nm"><b>${esc(p.nome)}</b>${sigla(p.partido)} <small>${pres ? 'presente' : 'ausente'}</small></span>
      <span class="acoes">${tribuna ? '<span class="chip vivo"><span class="ponto"></span>Tribuna</span>'
        : inscrito ? `<button class="botao pq fantasma" onclick="A('desinscrever',{pid:'${p.id}'})">Inscrito ✓</button>`
        : `<button class="botao pq" onclick="A('inscrever',{pid:'${p.id}'})">+ Inscrever</button>`}
      ${tribuna ? '' : `<button class="botao pq" title="Conceder a palavra agora (chamar à tribuna)" onclick="A('chamar',{pid:'${p.id}'})">Tribuna</button>`}
      ${micBotoes(p.id)}</span></li>`;
  }).join('');
}

function ceder(de) {
  const outros = fila().filter(e => e.pid !== de);
  if (!outros.length) return PL.avisar('Não há outro inscrito para receber o tempo.', true);
  dialogo(`${esc(parl(de).nome)} cede o tempo para…`,
    `<select class="campo" id="dCeder">${outros.map(e => `<option value="${e.pid}">${esc(parl(e.pid).nome)}</option>`).join('')}</select>
     <p class="detalhe" style="margin:0">O cedente sai da fila e o tempo dele (${fase(PL.estado.faseAtual).min} min) passa para quem recebe.</p>`,
    () => A('ceder', { de, para: $('dCeder').value }), 'Ceder tempo');
}

/* ---------------- pauta e votação ---------------- */
function desenharPauta() {
  const S = PL.estado, v = S.votacao;
  $('pauta').innerHTML = S.pauta.length ? S.pauta.map((m, k) => `<li style="align-items:flex-start;${m.id === S.materiaAtual ? 'outline:2px solid var(--ciano)' : ''}">
    <span class="ord" style="padding-top:2px">${k + 1}</span>
    <span class="nm"><b>${esc(m.numero)}</b><span style="display:block;font-size:14px;margin:2px 0 4px">${esc(m.ementa)}</span>
      <small>${m.autor ? 'Autoria: ' + esc(m.autor) + ' · ' : ''}</small><span class="chip ${m.situacao === 'aprovada' ? 'sim' : m.situacao === 'rejeitada' ? 'nao' : m.situacao === 'em_votacao' ? 'ambar' : ''}">${PL.SITUACOES[m.situacao] || m.situacao}</span></span>
    <span class="acoes" style="max-width:250px">
      <button class="botao pq" onclick="A('materiaMover',{id:'${m.id}',d:-1})">↑</button><button class="botao pq" onclick="A('materiaMover',{id:'${m.id}',d:1})">↓</button>
      <button class="botao pq" onclick="A('materiaAtual',{id:'${m.id}'})" title="Mostrar no telão">Em discussão</button>
      <button class="botao pq principal" onclick="abrirVotacao('${m.id}')" ${v?.aberta ? 'disabled' : ''}>Votar</button>
      <button class="botao pq fantasma" onclick="editarMateria('${m.id}')">Editar</button>
      <button class="botao pq fantasma" onclick="if(confirm('Remover esta matéria da pauta?'))A('materiaRemover',{id:'${m.id}'})">✕</button>
    </span></li>`).join('') : '<li class="vazio" style="background:none">Pauta vazia.</li>';

  if (!v) {
    $('votacao').innerHTML = `<h2>Votação</h2><p class="vazio">Nenhuma votação em andamento. Use “Votar” em uma matéria da pauta.</p>`;
  } else {
    const c = v.contagem, presentes = S.presenca;
    const placar = c ? `<div class="placar"><div class="sim"><b>${c.sim}</b>Sim</div><div class="nao"><b>${c.nao}</b>Não</div><div class="abst"><b>${c.abst}</b>Abstenção</div></div>`
      : `<div class="placar"><div style="grid-column:1/-1"><b>${v.votantes.length}</b>votos registrados (votação secreta)</div></div>`;
    let corpo = '';
    if (v.tipo === 'nominal') {
      corpo = `<div class="votos">${S.parls.map(p => {
        const voto = v.votos[p.id], aus = !presentes[p.id];
        return `<div class="voto ${aus ? 'ausente' : ''}">${avatar(p)}<span class="nm" title="${esc(p.nome)}">${esc(p.nome)}</span>
          ${v.aberta && !aus ? `<span class="bts">${['sim', 'nao', 'abst'].map(x => `<button class="${x} ${voto === x ? 'on' : ''}" title="${PL.VOTOS[x]}" onclick="A('votarPor',{pid:'${p.id}',valor:'${x}'})">${x === 'abst' ? 'A' : x === 'sim' ? 'S' : 'N'}</button>`).join('')}</span>`
            : `<span class="chip ${voto || ''}">${voto ? PL.VOTOS[voto] : aus ? 'Ausente' : '—'}</span>`}</div>`;
      }).join('')}</div>`;
    } else if (v.tipo === 'secreta') {
      corpo = `<p class="detalhe">Já votaram: ${v.votantes.map(id => esc(parl(id).nome)).join(', ') || 'ninguém'}.</p>`;
    }
    $('votacao').innerHTML = `<h2>Votação ${v.aberta ? 'aberta' : 'encerrada'}<span class="esp"></span><span class="chip">${v.tipo}</span><span class="chip">${PL.QUORUNS[v.quorum]}</span></h2>
      <div><b>${esc(v.numero)}</b> — ${esc(v.ementa)}</div>
      ${v.tipo === 'simbolica' ? '' : placar}
      ${v.resultado ? `<div class="resultado ${v.resultado}">${PL.SITUACOES[v.resultado].toUpperCase()}</div>` : ''}
      ${corpo}
      <div class="grupo" style="margin-top:14px;justify-content:flex-end">
        ${v.aberta ? (v.tipo === 'simbolica'
          ? `<span class="detalhe" style="margin-right:auto">“Os que aprovam permaneçam como se encontram.”</span>
             <button class="botao ok" onclick="A('votacaoEncerrar',{resultado:'aprovada'})">Declarar aprovada</button>
             <button class="botao perigo" onclick="A('votacaoEncerrar',{resultado:'rejeitada'})">Declarar rejeitada</button>`
          : `<button class="botao principal" onclick="if(confirm('Encerrar a votação e apurar o resultado?'))A('votacaoEncerrar')">Encerrar e apurar</button>`)
          + `<button class="botao fantasma" onclick="if(confirm('Cancelar esta votação? Os votos serão descartados.'))A('votacaoCancelar')">Cancelar</button>`
        : `<button class="botao" onclick="A('votacaoLimpar')">Tirar resultado do telão</button>`}
      </div>`;
  }
  $('votacoes').innerHTML = S.votacoes.length ? `<table class="tab"><tr><th>Matéria</th><th>Tipo</th><th>Sim</th><th>Não</th><th>Abst.</th><th>Resultado</th></tr>${S.votacoes.map(x => `<tr><td><b>${esc(x.numero)}</b></td><td>${x.tipo}</td>
    <td class="num">${x.contagem?.sim ?? '—'}</td><td class="num">${x.contagem?.nao ?? '—'}</td><td class="num">${x.contagem?.abst ?? '—'}</td>
    <td><span class="chip ${x.resultado === 'aprovada' ? 'sim' : x.resultado === 'rejeitada' ? 'nao' : 'ambar'}">${PL.SITUACOES[x.resultado]}</span></td></tr>`).join('')}</table>`
    : '<p class="vazio">Nenhuma votação concluída.</p>';
}

function abrirVotacao(mid) {
  const m = PL.estado.pauta.find(x => x.id === mid), q = PL.quorum();
  dialogo('Abrir votação', `<div><b>${esc(m.numero)}</b> — ${esc(m.ementa)}</div>
    <label class="rot">Processo<select class="campo" id="dTipo"><option value="nominal">Nominal (painel, voto aberto)</option><option value="simbolica">Simbólica (declarada pelo presidente)</option><option value="secreta">Secreta (painel, voto oculto)</option></select></label>
    <label class="rot">Quórum de aprovação<select class="campo" id="dQuorum"><option value="simples">Maioria simples (dos presentes)</option><option value="absoluta">Maioria absoluta (${Math.floor(q.n / 2) + 1} votos)</option><option value="dois_tercos">Dois terços (${Math.ceil(2 * q.n / 3)} votos)</option></select></label>
    ${q.ok ? '' : `<p class="chip ambar" style="margin:0">Atenção: presença abaixo do quórum (${q.presentes}/${q.minimo})</p>`}`,
    () => A('votacaoAbrir', { materia_id: mid, tipo: $('dTipo').value, quorum: $('dQuorum').value }), 'Abrir votação');
}

function editarMateria(id) {
  const m = id ? PL.estado.pauta.find(x => x.id === id) : { numero: '', ementa: '', autor: '' };
  dialogo(id ? 'Editar matéria' : 'Nova matéria', `
    <label class="rot">Número<input class="campo" id="dNum" value="${esc(m.numero)}" placeholder="PL 123/2026"></label>
    <label class="rot">Ementa<textarea class="campo" id="dEmenta" rows="3">${esc(m.ementa)}</textarea></label>
    <label class="rot">Autoria<input class="campo" id="dAutor" value="${esc(m.autor)}" list="dlParls"></label>
    <datalist id="dlParls">${PL.estado.parls.map(p => `<option value="${esc(p.nome)}">`).join('')}<option value="Executivo Municipal"><option value="Mesa Diretora"></datalist>`,
    () => A('materiaSalvar', { id, numero: $('dNum').value, ementa: $('dEmenta').value, autor: $('dAutor').value }));
}

/* ---------------- plenário ---------------- */
let sugestoesBloco = null;
async function carregarSugestoesBloco() {
  try {
    const d = await (await apiGet('/api/transparencia?interno=1')).json();
    sugestoesBloco = Object.fromEntries(d.parlamentares.filter(p => p.blocoSugerido).map(p => [p.id, { bloco: p.blocoSugerido, pct: p.comSituacaoPct }]));
    desenharPlenario();
  } catch {}
}
const NOMES_BLOCO = { situacao: 'Situação', oposicao: 'Oposição', independente: 'Independente', '': 'Sem bloco' };
function desenharPlenario() {
  const S = PL.estado;
  if (sugestoesBloco === null) { sugestoesBloco = {}; carregarSugestoesBloco(); }
  const partidos = [...new Set(S.parls.map(p => p.partido))].sort();
  if ($('bpPartido').options.length !== partidos.length) $('bpPartido').innerHTML = partidos.map(x => `<option>${esc(x)}</option>`).join('');
  $('quorumGrande').innerHTML = quorumHtml();
  $('tabParls').innerHTML = S.parls.map(p => {
    const pres = S.presenca[p.id];
    return `<tr><td style="width:52px">${avatar(p)}</td><td><b>${esc(p.nome)}</b>${p.lider ? ' <span class="chip" style="font-size:10px">Líder</span>' : ''}</td><td>${sigla(p.partido)}</td>
      <td><select class="campo" style="width:auto;padding:4px 6px;font-size:13px" onchange="A('parlBloco',{id:'${p.id}',bloco:this.value})">${Object.entries(NOMES_BLOCO).map(([k, v]) => `<option value="${k}" ${(p.bloco || '') === k ? 'selected' : ''}>${v}</option>`).join('')}</select>
        ${sugestoesBloco?.[p.id] && sugestoesBloco[p.id].bloco !== (p.bloco || '') ? `<br><small class="suave" title="Votou com a maioria da situação em ${sugestoesBloco[p.id].pct}% das votações nominais">pelo voto: ${NOMES_BLOCO[sugestoesBloco[p.id].bloco]} (${sugestoesBloco[p.id].pct}%)</small>` : ''}</td>
      <td class="tnum">${p.canal || '—'}</td><td><span class="grupo" style="gap:4px;flex-wrap:nowrap;align-items:center">${PL.micChip(p.id)}${micBotoes(p.id)}</span></td>
      <td><label class="linha" style="justify-content:flex-start"><input type="checkbox" ${pres ? 'checked' : ''} onchange="A('presenca',{pid:'${p.id}',presente:this.checked})"> ${pres ? 'Presente' : 'Ausente'}</label></td>
      <td class="suave tnum">${pres ? hora(pres) : '—'}</td>
      <td style="text-align:right"><button class="botao pq" onclick="editarParl('${p.id}')">Editar</button>
      <button class="botao pq fantasma" onclick="if(confirm('Remover ${esc(p.nome)}?'))A('parlRemover',{id:'${p.id}'})">✕</button></td></tr>`;
  }).join('');
}
async function todosPresentes(v) {
  for (const p of PL.estado.parls) if (!!PL.estado.presenca[p.id] !== v) await A('presenca', { pid: p.id, presente: v });
}
function editarParl(id) {
  const p = id ? parl(id) : { nome: '', partido: '', lider: false, canal: 0, bloco: '' };
  dialogo(id ? 'Editar parlamentar' : 'Novo parlamentar', `
    ${id ? `<div style="display:flex;gap:12px;align-items:center">${avatar(p, '', 'width:72px;height:72px;font-size:24px')}
      <label class="rot">Foto<input type="file" accept="image/*" id="dFoto"></label>
      ${p.fotoV ? `<button class="botao pq fantasma" onclick="A('imagem',{alvo:'${id}'}).then(()=>dlg.close())">Remover foto</button>` : ''}</div>` : ''}
    <label class="rot">Nome parlamentar<input class="campo" id="dNome" value="${esc(p.nome)}"></label>
    <label class="rot">Partido<input class="campo" id="dPartido" value="${esc(p.partido)}" list="dlPartidos"></label>
    <datalist id="dlPartidos">${Object.keys(PL.estado.partidos).map(s => `<option value="${esc(s)}">`).join('')}</datalist>
    <label class="linha">Líder de bancada<input type="checkbox" id="dLider" ${p.lider ? 'checked' : ''}></label>
    <label class="linha">Bloco<select class="campo" id="dBloco" style="width:auto">${Object.entries(NOMES_BLOCO).map(([k, v]) => `<option value="${k}" ${(p.bloco || '') === k ? 'selected' : ''}>${v}</option>`).join('')}</select></label>
    <label class="linha">Canal do microfone na mesa de som (0 = sem canal)<input class="campo" type="number" min="0" max="256" id="dCanal" value="${p.canal || 0}" style="width:100px"></label>
    <label class="rot">PIN de acesso pelo celular (4 a 8 dígitos)<input class="campo" id="dPin" inputmode="numeric" placeholder="${id ? 'deixe em branco para manter' : 'padrão: 0000'}"></label>`,
    async () => {
      await PL.acao('presidente', 'parlSalvar', { id, nome: $('dNome').value, partido: $('dPartido').value, lider: $('dLider').checked, pin: $('dPin').value, canal: +$('dCanal').value, bloco: $('dBloco').value });
      const arq = $('dFoto')?.files[0];
      if (arq) A('imagem', { alvo: id, dataUrl: await PL.lerImagem(arq) });
    });
}

/* ---------------- relatórios ---------------- */
function desenharRelatorios() {
  const S = PL.estado;
  const porPartido = {}, porParl = {};
  for (const h of S.hist) {
    porPartido[h.partido] = (porPartido[h.partido] || 0) + h.usado;
    const k = porParl[h.pid] ||= { nome: h.nome, partido: h.partido, n: 0, s: 0 };
    k.n++; k.s += h.usado;
  }
  const max = Math.max(1, ...Object.values(porPartido));
  $('porPartido').innerHTML = Object.keys(porPartido).length ? Object.entries(porPartido).sort((a, b) => b[1] - a[1]).map(([s, v]) =>
    `<div class="l">${sigla(s)}<div class="b" style="--cor-partido:${PL.corPartido(s)};width:${v / max * 100}%"></div><span class="tnum" style="text-align:right">${tempo(v * 1000)}</span></div>`).join('')
    : '<p class="vazio">Sem pronunciamentos ainda.</p>';
  const lin = Object.values(porParl).sort((a, b) => b.s - a.s);
  $('porParl').innerHTML = lin.length ? `<tr><th>Parlamentar</th><th>Partido</th><th>Falas</th><th>Tempo</th></tr>` + lin.map(x =>
    `<tr><td>${esc(x.nome)}</td><td>${sigla(x.partido)}</td><td class="num">${x.n}</td><td class="num tnum">${tempo(x.s * 1000)}</td></tr>`).join('') : '';
  $('pronunciamentos').innerHTML = S.hist.length ? `<tr><th>Início</th><th>Parlamentar</th><th>Fase</th><th>Matéria</th><th>Usado / disponível</th><th>Apartes e questões</th></tr>` + S.hist.map(h => {
    const disp = h.previsto + h.bonus + h.extra, m = S.pauta.find(x => x.id === h.materia);
    return `<tr><td class="tnum">${hora(h.inicio)}</td><td>${esc(h.nome)} ${sigla(h.partido)}</td><td>${esc(h.faseNome)}</td><td>${m ? esc(m.numero) : '—'}</td>
      <td class="tnum ${h.usado > disp ? 'n-fim' : ''}">${tempo(h.usado * 1000)} / ${tempo(disp * 1000)}</td>
      <td class="suave" style="font-size:13px">${[...h.apartes.map(a => `${PL.TIPOS[a.tipo]}: ${esc(a.nome)} (${tempo(a.usado * 1000)})`), ...h.cedentes.map(c => 'Tempo cedido por ' + esc(c))].join('<br>') || '—'}</td></tr>`;
  }).join('') : '<tr><td class="vazio">Nenhum pronunciamento registrado.</td></tr>';

  const grav = S.gravacoes || [];
  $('gravacoes').innerHTML = grav.length ? grav.map(g => `<div class="grav-item">
      <div style="display:flex;gap:8px;align-items:center"><b>${esc(g.nome)}</b>${sigla(g.partido)}<span class="suave" style="font-size:13px">${esc(g.faseNome)} · ${new Date(g.inicio).toLocaleString('pt-BR')}</span>
        <span style="flex:1"></span>${g.fim ? `<span class="suave tnum" style="font-size:12px">${(g.bytes / 1048576).toFixed(1)} MB</span>` : '<span class="rec">gravando</span>'}</div>
      ${g.fim ? `<audio controls preload="none" src="/gravacao/${g.id}.${g.ext}"></audio>
      <div class="grupo"><a class="botao pq" href="/gravacao/${g.id}.${g.ext}?baixar=1">Baixar</a>
        <button class="botao pq fantasma" onclick="if(confirm('Apagar esta gravação? Não pode ser desfeito.'))A('gravacaoRemover',{id:'${g.id}'})">Apagar</button></div>
      ${transcricaoHtml(g)}` : ''}
    </div>`).join('') : '<p class="vazio">Nenhuma gravação. Ative “Gravar falas” na aba Sessão.</p>';

  // WebM gravado sem ffmpeg no servidor não traz a duração: força o navegador a calcular
  document.querySelectorAll('#gravacoes audio').forEach(au => {
    au.onloadedmetadata = () => {
      if (au.duration !== Infinity) return;
      au.currentTime = 1e101;
      au.ontimeupdate = () => { au.ontimeupdate = null; au.currentTime = 0; };
    };
  });

  const oc = S.ocorrencias || [];
  $('ocorrencias').innerHTML = oc.length ? '<tr><th>Hora</th><th>Ocorrência</th><th>Parlamentar</th><th>Motivo</th></tr>' + oc.map(o =>
    `<tr><td class="tnum">${hora(o.ts)}</td><td><span class="chip ${['corte', 'tempo_esgotado'].includes(o.tipo) ? 'nao' : ['congelar', 'plenario_congelado'].includes(o.tipo) ? 'interr' : 'sim'}">${PL.OCORRENCIAS[o.tipo] || o.tipo}</span></td>
      <td>${esc(o.nome || '—')}</td><td class="suave">${esc(o.motivo || '—')}</td></tr>`).join('') : '<tr><td class="vazio">Nenhuma ocorrência.</td></tr>';
}
function csvOcorrencias() {
  baixarCsv('ocorrencias', [['Data e hora', 'Ocorrência', 'Parlamentar', 'Motivo'],
    ...[...(PL.estado.ocorrencias || [])].reverse().map(o => [new Date(o.ts).toLocaleString('pt-BR'), PL.OCORRENCIAS[o.tipo] || o.tipo, o.nome, o.motivo])]);
}
function baixarCsv(nome, linhas) {
  const csv = '﻿' + linhas.map(l => l.map(c => `"${String(c ?? '').replace(/"/g, '""')}"`).join(';')).join('\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
  a.download = `${nome}-${new Date().toISOString().slice(0, 10)}.csv`; a.click();
}
function csvTempos() {
  const S = PL.estado;
  baixarCsv('ata-tempos', [['Parlamentar', 'Partido', 'Fase', 'Matéria', 'Início', 'Fim', 'Regimental (s)', 'Cedido (s)', 'Extra (s)', 'Usado (s)', 'Apartes / questões', 'Cedentes'],
    ...[...S.hist].reverse().map(h => [h.nome, h.partido, h.faseNome, S.pauta.find(x => x.id === h.materia)?.numero || '', new Date(h.inicio).toLocaleString('pt-BR'), new Date(h.fim).toLocaleString('pt-BR'),
      h.previsto, h.bonus, h.extra, h.usado, h.apartes.map(a => `${PL.TIPOS[a.tipo]}: ${a.nome} (${a.usado}s)`).join('; '), h.cedentes.join('; ')])]);
}
function csvVotacoes() {
  const S = PL.estado;
  baixarCsv('votacoes', [['Matéria', 'Ementa', 'Processo', 'Quórum', 'Sim', 'Não', 'Abstenção', 'Resultado', 'Encerrada em', 'Votos nominais'],
    ...[...S.votacoes].reverse().map(v => [v.numero, v.ementa, v.tipo, PL.QUORUNS[v.quorum], v.contagem?.sim, v.contagem?.nao, v.contagem?.abst, PL.SITUACOES[v.resultado],
      new Date(v.fim).toLocaleString('pt-BR'), Object.entries(v.votos || {}).map(([pid, x]) => `${parl(pid).nome}: ${PL.VOTOS[x]}`).join('; ')])]);
}

/* ---------------- configurações ---------------- */
let cfgFases = [], cfgPartidos = [];
const CORES = ['#1f8a4c', '#d66060', '#1f8fff', '#19b6d8', '#4fb83b', '#c9a227', '#8e5bd6', '#e07a2f'];
function carregarConfig() {
  const S = PL.estado; if (!S) return;
  $('cNome').value = S.casa.nome; $('cTitulo').value = S.casa.titulo; $('cTema').value = S.casa.tema; $('cDestaque').value = S.casa.destaque;
  desenharCores();
  const c = S.cfg;
  $('cAlerta').value = c.alerta; $('cAparte').value = c.aparte; $('cOrdem').value = c.ordem;
  $('cDesconta').checked = c.desconta; $('cCorte').checked = c.corte; $('cSom').checked = c.som;
  $('cAuto').checked = c.autoInscricao; $('cExige').checked = c.exigePresenca; $('cPresCel').checked = c.presencaCelular;
  $('cTranscPub').checked = !!c.transcricaoAutoPublicar;
  cfgFases = S.fases.map(f => ({ ...f })); desenharFases();
  cfgPartidos = Object.entries(S.partidos); desenharPartidos();
  $('sUrl').value = S.sapl.url || ''; $('sAnos').value = new Date().getFullYear();
  carregarRodape();
  carregarTransmissao();
  desenharBackup();
  desenharSapl();
  const m = S.mesa;
  mesaOpcoes();
  $('mModelo').value = m.modelo || (m.driver === 'osc' ? 'osc' : m.driver === 'http' ? 'http' : '');
  $('mHost').value = m.host; $('mPorta').value = m.porta; $('mEndereco').value = m.endereco;
  $('mMidiCanal').value = m.midiCanal || 1; $('mMetodo').value = m.metodo || 'GET';
  $('mAberto').value = m.valorAberto; $('mFechado').value = m.valorFechado; $('mTipo').value = m.tipoValor;
  $('mUrl').value = m.url; $('mTribuna').value = m.canalTribuna; $('mReenviar').checked = m.reenviar;
  mesaCampos();
}
/* ---------------- rodapé público ---------------- */
const CAMPOS_RODAPE = ['endereco', 'telefone', 'whatsapp', 'email', 'site', 'instagram', 'facebook', 'youtube', 'linkedin'];
function carregarRodape() {
  const r = PL.estado.rodape || {};
  for (const [pref, chave] of [['rc', 'camara'], ['ra', 'aggilli']])
    for (const k of CAMPOS_RODAPE) $(`${pref}_${k}`).value = r[chave]?.[k] || '';
}
async function salvarRodape() {
  const ler = pref => Object.fromEntries(CAMPOS_RODAPE.map(k => {
    let v = $(`${pref}_${k}`).value.trim();
    if (k === 'instagram' && v.startsWith('@')) v = 'https://instagram.com/' + v.slice(1);
    return [k, v];
  }));
  await PL.acao('presidente', 'rodapeSalvar', { camara: ler('rc'), aggilli: ler('ra') }).catch(() => {});
  carregarRodape(); PL.avisar('Rodapé salvo.');
}

/* ---------------- SAPL ---------------- */
async function salvarSapl() {
  await A('saplConfigurar', { url: $('sUrl').value });
  if (PL.estado.sapl.url) A('saplTestar');
}
function importarMaterias() {
  const anos = $('sAnos').value.split(/[,; ]+/).map(Number).filter(Boolean);
  const tipos = [...$('sTipos').selectedOptions].map(o => +o.value);
  A('saplImportarMaterias', { anos, tipos, limite: +$('sLimite').value });
}
function desenharSapl() {
  const sp = PL.estado.sapl, job = sp.job;
  $('saplCasa').innerHTML = sp.casa ? `<span class="chip sim">${esc(sp.casa.nome)}</span>` : sp.url ? '<span class="chip">não testado</span>' : '';
  $('sAcoes').classList.toggle('oculto', !sp.casa);
  if ($('sTipos').options.length !== sp.tipos.length)
    $('sTipos').innerHTML = sp.tipos.map(t => `<option value="${t.id}" ${['PL', 'PLO', 'PLC', 'PLL'].includes(t.sigla) ? 'selected' : ''}>${esc(t.sigla)} — ${esc(t.descricao)}</option>`).join('');
  if ($('sSessao').options.length !== sp.sessoes.length)
    $('sSessao').innerHTML = sp.sessoes.map(x => `<option value="${x.id}">${x.data ? new Date(x.data + 'T12:00').toLocaleDateString('pt-BR') + ' · ' : ''}${esc(x.nome)}</option>`).join('');
  const rodando = job && !job.fim;
  document.querySelectorAll('#cartaoSapl button').forEach(b => { b.disabled = rodando; });
  $('sJob').innerHTML = !job ? '' : `
    <div class="detalhe" style="color:${job.erro ? 'var(--vermelho)' : 'var(--texto)'}">${rodando ? '⏳ ' : job.erro ? '⚠ ' : '✓ '}${esc(job.erro || job.mensagem)}</div>
    ${job.total ? `<div class="barra" style="margin:8px 0"><i style="width:${Math.round(100 * job.feitos / job.total)}%"></i></div><small class="suave">${job.feitos} de ${job.total}</small>` : ''}
    ${job.log.length ? `<details style="margin-top:6px"><summary class="detalhe" style="cursor:pointer">Registro (${job.log.length})</summary><div class="suave tnum" style="font-size:12px;line-height:1.6;max-height:180px;overflow:auto">${job.log.map(esc).join('<br>')}</div></details>` : ''}`;
}
PL.aoMudar(() => { if (abaAtual === 'config') { desenharSapl(); desenharBackup(); } });
function desenharBackup() {
  const b = PL.estado.backup || {};
  const tam = n => n > 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.round(n / 1024) + ' KB';
  $('bkBotao').disabled = !!b.emAndamento;
  $('bkEstado').innerHTML = b.emAndamento ? '⏳ Fazendo backup…'
    : b.erro ? `<span style="color:var(--vermelho)">⚠ Falhou: ${esc(b.erro)}</span>`
    : b.ultimo ? `✓ Último backup: <b>${new Date(b.ultimo).toLocaleString('pt-BR')}</b> · ${esc(b.arquivo)} (${tam(b.tamanho)}) · ${b.copias || 1} cópia(s) guardadas em <code>${esc(b.pasta || '')}</code><br>
        Nextcloud: ${b.nextcloud === 'enviado' ? '<span class="chip sim">enviado</span>' : b.nextcloud === 'desligado' || !b.nextcloud ? 'não configurado' : `<span class="chip nao">${esc(b.nextcloud)}</span>`}`
    : 'Nenhum backup ainda.';
}

const MESA_AJUDA = {
  simulado: 'Sem equipamento: o painel só mostra o que abriria e fecharia. Bom para treinar.',
  osc: 'Ligue a mesa na mesma rede do servidor e informe o IP dela (veja no menu de rede/setup da mesa).',
  yamaha: 'Na mesa: ative o controle remoto por rede (porta 49280). O painel liga e desliga o botão ON de cada canal de entrada.',
  allenheath_qu: 'MIDI por rede (TCP). Qu e Avantis usam a porta 51325; dLive usa 51328. Confira o canal MIDI configurado na mesa.',
  allenheath_sq: 'MIDI por rede (TCP 51325). Confira o canal MIDI configurado na mesa (Utility → MIDI).',
  soundcraft: 'Use o IP da mesa Ui (o mesmo que abre a tela de controle no navegador).',
  http: 'O painel envia POST com JSON {canais:[{canal, aberto}]} a cada mudança. Serve para o Bitfocus Companion (que controla centenas de mesas), Node-RED ou a API de um sistema de conferência.',
  http_modelo: 'Uma chamada por canal. Use {canal}, {acao} (on/off), {valor} (1/0) ou {aberto} (true/false). Ex.: Shelly: http://192.168.0.60/relay/{canal}?turn={acao}',
};
function mesaOpcoes() {
  const P = PL.estado.mesaPresets || {};
  $('mModelo').innerHTML = '<option value="">Simulada (sem equipamento)</option>' +
    Object.entries(P).map(([k, p]) => `<option value="${k}">${esc(p.nome)}</option>`).join('');
}
function mesaCampos() {
  const p = (PL.estado.mesaPresets || {})[$('mModelo').value], d = p ? p.driver : 'simulado';
  $('mRede').classList.toggle('oculto', ['simulado', 'http', 'http_modelo'].includes(d));
  $('mMidi').classList.toggle('oculto', !d.startsWith('allenheath'));
  $('mOsc').classList.toggle('oculto', d !== 'osc');
  $('mHttp').classList.toggle('oculto', !d.startsWith('http'));
  $('mMetodoRot').classList.toggle('oculto', d !== 'http_modelo');
  $('mUrl').placeholder = d === 'http' ? 'http://192.168.0.20:1880/microfones' : 'http://192.168.0.60/relay/{canal}?turn={acao}';
  $('mAjuda').textContent = MESA_AJUDA[d] + (p && !['osc', 'http', 'http_modelo'].includes(d) ? ' Protocolo do fabricante: teste os canais antes da primeira sessão.' : '');
}
function mesaPreset(nome) {
  const p = (PL.estado.mesaPresets || {})[nome];
  if (p) {
    if (p.porta) $('mPorta').value = p.porta;
    if (p.endereco) { $('mEndereco').value = p.endereco; $('mAberto').value = p.valorAberto; $('mFechado').value = p.valorFechado; $('mTipo').value = p.tipoValor; }
  }
  mesaCampos();
}
async function salvarMesa() {
  const p = (PL.estado.mesaPresets || {})[$('mModelo').value];
  await PL.acao('presidente', 'mesaSalvar', { cfg: {
    driver: p ? p.driver : 'simulado', modelo: $('mModelo').value, host: $('mHost').value, porta: +$('mPorta').value,
    endereco: $('mEndereco').value, valorAberto: $('mAberto').value, valorFechado: $('mFechado').value, tipoValor: $('mTipo').value,
    url: $('mUrl').value, metodo: $('mMetodo').value, midiCanal: +$('mMidiCanal').value || 1,
    canalTribuna: +$('mTribuna').value, reenviar: $('mReenviar').checked } }).then(() => PL.avisar('Mesa de som salva.')).catch(() => {});
}

/* ---------------- mesa de som (status) e gravação ---------------- */
function desenharMesaEGravacao() {
  const S = PL.estado, st = S.mesaStatus || {}, nomes = { simulado: 'simulada', osc: 'OSC', http: 'HTTP' };
  $('mesaEstado').innerHTML = `<span class="chip ${st.ok ? 'sim' : 'nao'}">${nomes[S.mesa.driver]} · ${st.ok ? 'ok' : 'erro'}</span>`;
  $('mesaLog').innerHTML = (st.erro ? `<div class="n-fim">${esc(st.erro)}</div>` : '') + ((st.log || []).slice(0, 5).map(esc).join('<br>') || 'Nenhum comando enviado ainda.');
  const outro = PL.gravando() && !Grav.ligado;
  $('gravEstado').innerHTML = Grav.ligado ? (Grav.gid ? '<span class="rec">gravando</span>' : '<span class="chip">aguardando orador</span>')
    : outro ? '<span class="rec">em outro computador</span>' : '';
  $('gravBotao').textContent = Grav.ligado ? '■ Parar de gravar' : '● Gravar falas neste computador';
  $('gravBotao').className = 'botao ' + (Grav.ligado ? 'perigo' : '');
  if (!window.isSecureContext) {
    $('gravBotao').disabled = true;
    $('gravAjuda').innerHTML = 'Para gravar, abra este painel em <b>http://localhost:8090</b> no computador do servidor ou por HTTPS (o navegador só libera o microfone em conexão segura).';
  }
}

/* Transmissão ao vivo: este navegador capta a entrada de áudio (a mesma da gravação) e manda
   partes de 1 s ao servidor, que converte para MP3 e publica no Icecast/AzuraCast. */
const Tr = { stream: null, rec: null, envio: Promise.resolve(), querendo: false, falhas: 0, bytes: 0 };
setInterval(() => { if (PL.estado?.transmissaoStatus?.ativa) desenharTransmissao(); }, 5000);
const VMIME = ['video/webm;codecs=vp8,opus', 'video/webm;codecs=vp9,opus', 'video/webm'].find(t => window.MediaRecorder?.isTypeSupported(t));
async function listarCameras() {
  if (!navigator.mediaDevices?.enumerateDevices) return;
  const atual = localStorage.getItem('legis.camera') || '';
  const lista = (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === 'videoinput');
  $('trCamera').innerHTML = '<option value="">Sem câmera: tela automática (orador, foto e cronômetro)</option>' +
    lista.map((d, k) => `<option value="${esc(d.deviceId || 'padrao')}">📷 ${esc(d.label || 'Câmera ' + (k + 1))}</option>`).join('');
  $('trCamera').value = [...$('trCamera').options].some(o => o.value === atual) ? atual : '';
}
$('trCamera').onchange = () => localStorage.setItem('legis.camera', $('trCamera').value);
navigator.mediaDevices?.addEventListener?.('devicechange', listarCameras);
listarCameras();
async function trAlternar() {
  const st = PL.estado.transmissaoStatus || {};
  if (Tr.querendo || st.ativa || st.conectando) return trParar();
  if (!MIME) return PL.avisar('Este navegador não capta áudio. Use Chrome, Edge ou Firefox.', true);
  const yt = PL.estado.youtube || {};
  if (!PL.estado.transmissao?.configurada && !(yt.configurado && yt.ligado)) { document.querySelector('[data-aba="config"]').click(); $('cartaoTransmissaoCfg').scrollIntoView({ behavior: 'smooth' }); return PL.avisar('Configure a rádio ou o YouTube primeiro.', true); }
  const cam = yt.configurado && yt.ligado ? $('trCamera').value : '';
  if (cam && !VMIME) return PL.avisar('Este navegador não envia vídeo. Use Chrome, Edge ou Firefox.', true);
  try {
    const id = $('gravDispositivo').value;
    Tr.stream = await navigator.mediaDevices.getUserMedia({
      audio: { deviceId: id ? { exact: id } : undefined, echoCancellation: false, noiseSuppression: false, autoGainControl: false },
      video: cam ? { deviceId: cam !== 'padrao' ? { exact: cam } : undefined, width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 30 } } : false });
  } catch (e) { return PL.avisar((cam ? 'Sem acesso à câmera ou à entrada de áudio: ' : 'Sem acesso à entrada de áudio: ') + e.message, true); }
  Tr.video = Tr.stream.getVideoTracks().length > 0;
  if (Tr.video) { $('trPrevia').srcObject = Tr.stream; $('trPrevia').classList.remove('oculto'); listarCameras(); }
  Tr.querendo = true; Tr.falhas = 0;
  await PL.acao('presidente', 'transmissaoLigar', { camera: Tr.video }).catch(() => { trParar(); });
  desenharTransmissao();
}
async function ytSalvar() {
  await PL.acao('presidente', 'youtubeConfigurar', { chave: $('ytChave').value, url: $('ytUrl').value, link: $('ytLink').value, ligado: $('ytLigado').checked })
    .then(() => { PL.avisar('YouTube salvo.'); carregarTransmissao(); }).catch(() => {});
}
async function ytRemoverChave() {
  if (!confirm('Remover a chave do YouTube deste servidor?')) return;
  await PL.acao('presidente', 'youtubeRemoverChave').then(() => { PL.avisar('Chave removida.'); carregarTransmissao(); }).catch(() => {});
}
function trSincronizar() {
  const st = PL.estado.transmissaoStatus || {};
  if (Tr.querendo && st.ativa && !Tr.rec) trComecarEnvio();
  if (Tr.querendo && !st.ativa && !st.conectando && st.erro) { PL.avisar(st.erro, true); trParar(false); }
  desenharTransmissao();
}
function trComecarEnvio() {
  const tipo = Tr.video ? VMIME : MIME;
  const rec = new MediaRecorder(Tr.stream, { mimeType: tipo, audioBitsPerSecond: 128000, ...(Tr.video ? { videoBitsPerSecond: 2500000 } : {}) });
  Tr.envio = Promise.resolve();
  rec.ondataavailable = e => {
    if (!e.data.size || Tr.rec !== rec) return;
    Tr.envio = Tr.envio.then(() => api('/api/transmissao/parte', e.data, tipo.split(';')[0]))
      .then(r => { Tr.falhas = 0; Tr.bytes = r.bytes; })
      .catch(err => { if (++Tr.falhas >= 3 && Tr.rec === rec) { PL.avisar(err.message, true); trParar(); } });
  };
  rec.start(1000);
  Tr.rec = rec;
}
async function trParar(avisarServidor = true) {
  Tr.querendo = false;
  const rec = Tr.rec; Tr.rec = null;
  if (rec && rec.state !== 'inactive') rec.stop();
  Tr.stream?.getTracks().forEach(t => t.stop()); Tr.stream = null; Tr.video = false;
  $('trPrevia').srcObject = null; $('trPrevia').classList.add('oculto');
  if (avisarServidor) await PL.acao('presidente', 'transmissaoDesligar').catch(() => {});
  desenharTransmissao();
}
function desenharTransmissao() {
  const S = PL.estado; if (!S) return;
  const st = S.transmissaoStatus || {}, aqui = Tr.querendo;
  const min = st.desde ? Math.floor((Date.now() - st.desde) / 60000) : 0;
  $('trEstado').innerHTML = st.ativa ? `<span class="rec">no ar${aqui ? '' : ' (outro computador)'}</span>`
    : st.conectando ? '<span class="chip ambar">conectando…</span>' : st.erro ? '<span class="chip nao">parada</span>' : '';
  $('trBotao').textContent = st.ativa || st.conectando || aqui ? '■ Encerrar transmissão' : '📡 Transmitir a sessão ao vivo';
  $('trBotao').className = 'botao ' + (st.ativa || aqui ? 'perigo' : '');
  $('trBotao').disabled = !window.isSecureContext;
  $('trAjuda').innerHTML = !window.isSecureContext ? 'Para transmitir, abra este painel por HTTPS ou em localhost (o navegador só libera o áudio em conexão segura).'
    : st.ativa ? `No ar há ${min} min · ${(Math.max(st.bytes, aqui ? Tr.bytes : 0) / 1048576).toFixed(1)} MB enviados${S.transmissao.publico ? ` · <a href="${esc(S.transmissao.publico)}" target="_blank" rel="noopener">ouvir</a>` : ''}`
    : st.erro ? `<span class="n-fim">${esc(st.erro)}</span>`
    : S.transmissao?.configurada ? 'Usa a mesma entrada de áudio da gravação e envia para o servidor de rádio configurado.'
    : (S.youtube?.configurado && S.youtube?.ligado) ? 'Usa a mesma entrada de áudio da gravação e envia ao YouTube (a rádio não está configurada).'
    : 'Configure a rádio ou o YouTube em Configurações.';
  $('trCfgEstado').innerHTML = S.transmissao?.configurada ? '<span class="chip sim">configurada</span>' : '<span class="chip">não configurada</span>';
  const yt = S.youtube || {}, ys = S.youtubeStatus || {};
  $('trCamera').classList.toggle('oculto', !(yt.configurado && yt.ligado));
  $('trCamera').disabled = aqui || st.ativa;
  $('trYoutube').innerHTML = !(yt.configurado && yt.ligado) ? ''
    : ys.ativa ? `<span class="rec">▶ YouTube no ar</span> · ${ys.modo === 'camera' ? 'câmera' : 'tela automática'}${yt.link ? ` · <a href="${esc(yt.link)}" target="_blank" rel="noopener">assistir</a>` : ''}`
    : ys.erro ? `<span class="n-fim">YouTube: ${esc(ys.erro)}</span>`
    : (aqui || st.ativa) ? '<span class="chip ambar">YouTube conectando…</span>' : '▶ Também vai para o YouTube.';
  $('ytCfgEstado').innerHTML = yt.configurado ? `<span class="chip ${yt.ligado ? 'sim' : ''}">${yt.ligado ? 'ligado' : 'chave salva, desligado'}</span>` : '<span class="chip">sem chave</span>';
  $('ytRemover').classList.toggle('oculto', !yt.configurado);
}
PL.aoMudar(() => trSincronizar());
addEventListener('beforeunload', e => { if (Tr.rec) { e.preventDefault(); e.returnValue = ''; } });

let trPresets = {};
async function carregarTransmissao() {
  let c;
  try { c = await (await apiGet('/api/transmissao')).json(); } catch { return; }
  trPresets = c.presets;
  $('trBitrate').innerHTML = c.bitrates.map(b => `<option value="${b}">${b} kbps${b === 128 ? ' (recomendado)' : ''}</option>`).join('');
  $('trPreset').value = c.preset; $('trHost').value = c.host; $('trPorta').value = c.porta; $('trMount').value = c.mount;
  $('trUsuario').value = c.usuario; $('trSenha').value = ''; $('trSenha').placeholder = c.temSenha ? '•••••• (mantida; digite para trocar)' : '';
  $('trBitrate').value = c.bitrate; $('trPublico').value = c.publico; $('trNome').value = c.nome; $('trTls').checked = c.tls;
  if (!c.ffmpeg) $('trCfgEstado').innerHTML = '<span class="chip nao">servidor sem ffmpeg</span>';
  const y = c.youtube || {};
  $('ytUrl').value = y.url || ''; $('ytLink').value = y.link || ''; $('ytLigado').checked = y.ligado !== false;
  $('ytChave').value = ''; $('ytChave').placeholder = y.temChave ? '•••••• (mantida; cole outra para trocar)' : 'xxxx-xxxx-xxxx-xxxx-xxxx';
  trVisibilidade();
}
function trPreset() {
  trVisibilidade();
  const p = trPresets[$('trPreset').value]; if (!p || $('trPreset').value === 'local') return;
  $('trPorta').value = p.porta; $('trMount').value = p.mount; if (p.usuario) $('trUsuario').value = p.usuario;
}
function trVisibilidade() {
  const local = $('trPreset').value === 'local';
  document.querySelectorAll('.trDestino').forEach(e => e.classList.toggle('oculto', local));
  $('trLocalAjuda').classList.toggle('oculto', !local);
  $('trLocalAjuda').innerHTML = `Na estação do AzuraCast, a sessão entra puxando <b>http://${esc(location.hostname)}:${esc(location.port || '80')}/aovivo</b> (use o IP deste servidor). Em "Endereço público para ouvir", ponha o link da estação.`;
}
async function trSalvar() {
  await PL.acao('presidente', 'transmissaoConfigurar', { preset: $('trPreset').value, host: $('trHost').value, porta: +$('trPorta').value,
    mount: $('trMount').value, usuario: $('trUsuario').value, senha: $('trSenha').value, tls: $('trTls').checked, bitrate: +$('trBitrate').value,
    publico: $('trPublico').value, nome: $('trNome').value }).then(() => { PL.avisar('Transmissão salva.'); carregarTransmissao(); }).catch(() => {});
}

/* Gravação: este navegador grava a entrada de áudio (saída da mesa) e envia
   em partes de 5 s ao servidor. Cada fala (orador + início) vira um arquivo. */
const Grav = { ligado: false, stream: null, rec: null, gid: null, alvo: null, envio: Promise.resolve(), cadeia: Promise.resolve(), analisador: null, nivel: null };
const MIME = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4'].find(t => window.MediaRecorder?.isTypeSupported(t));
async function api(caminho, corpo, tipo = 'application/json') {
  const r = await fetch(caminho, { method: 'POST', headers: { 'Content-Type': tipo, Authorization: 'Bearer ' + PL.token('presidente') },
    body: tipo === 'application/json' ? JSON.stringify(corpo || {}) : corpo });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.erro || 'Falha no envio da gravação.');
  return j;
}
async function listarEntradas() {
  const atual = localStorage.getItem('parlatorio.entrada') || '';
  const lista = (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === 'audioinput');
  $('gravDispositivo').innerHTML = '<option value="">Entrada padrão do sistema</option>' +
    lista.map((d, k) => `<option value="${esc(d.deviceId)}">${esc(d.label || 'Entrada ' + (k + 1))}</option>`).join('');
  $('gravDispositivo').value = lista.some(d => d.deviceId === atual) ? atual : '';
}
$('gravDispositivo').onchange = async () => {
  localStorage.setItem('parlatorio.entrada', $('gravDispositivo').value);
  if (Grav.ligado) { await gravDesligar(); await gravLigar(); }
};
async function gravAlternar() { Grav.ligado ? gravDesligar() : gravLigar(); }
async function gravLigar() {
  if (!MIME) return PL.avisar('Este navegador não grava áudio. Use Chrome, Edge ou Firefox.', true);
  try {
    const id = $('gravDispositivo').value;
    Grav.stream = await navigator.mediaDevices.getUserMedia({ audio: { deviceId: id ? { exact: id } : undefined, echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
  } catch (e) { return PL.avisar('Sem acesso à entrada de áudio: ' + e.message, true); }
  const ctx = new AudioContext(), fonte = ctx.createMediaStreamSource(Grav.stream);
  Grav.analisador = ctx.createAnalyser(); Grav.analisador.fftSize = 512; fonte.connect(Grav.analisador);
  Grav.nivel = new Uint8Array(Grav.analisador.fftSize); Grav.ctx = ctx;
  Grav.ligado = true;
  listarEntradas();
  gravSincronizar(); desenhar();
}
async function gravDesligar() {
  Grav.ligado = false; Grav.alvo = null;
  Grav.cadeia = Grav.cadeia.then(gravPararAtual);
  await Grav.cadeia;
  Grav.stream?.getTracks().forEach(t => t.stop()); Grav.stream = null;
  Grav.ctx?.close(); Grav.analisador = null;
  desenhar();
}
function gravSincronizar() {
  if (!Grav.ligado) return;
  const o = PL.estado.orador;
  const alvo = o && o.inicio ? o.pid + ':' + o.inicio : null;
  if (alvo === Grav.alvo) return;
  Grav.alvo = alvo;
  Grav.cadeia = Grav.cadeia.then(async () => {
    await gravPararAtual();
    if (alvo && Grav.ligado) await gravIniciar(o.pid, o.inicio);
    desenhar();
  }).catch(e => PL.avisar(e.message, true));
}
async function gravIniciar(pid, inicio) {
  const { id } = await api('/api/gravacao/iniciar', { pid, inicio, mime: MIME });
  Grav.gid = id; Grav.envio = Promise.resolve();
  const rec = new MediaRecorder(Grav.stream, { mimeType: MIME, audioBitsPerSecond: 96000 });
  rec.ondataavailable = e => {
    if (!e.data.size) return;
    Grav.envio = Grav.envio.then(() => api(`/api/gravacao/${id}/parte`, e.data, MIME.split(';')[0])).catch(err => PL.avisar(err.message, true));
  };
  rec.start(5000);
  Grav.rec = rec;
}
async function gravPararAtual() {
  const rec = Grav.rec, id = Grav.gid;
  if (!rec) return;
  await new Promise(ok => { rec.onstop = ok; rec.stop(); });
  await Grav.envio;
  await api(`/api/gravacao/${id}/fim`).catch(() => {});
  Grav.rec = null; Grav.gid = null;
}
PL.aoMudar(() => gravSincronizar());
addEventListener('beforeunload', e => { if (Grav.rec) { e.preventDefault(); e.returnValue = ''; } });
function pintarNivel() {
  if (!Grav.analisador) { $('gravNivel').firstElementChild.style.width = '0%'; return; }
  Grav.analisador.getByteTimeDomainData(Grav.nivel);
  let pico = 0; for (const v of Grav.nivel) pico = Math.max(pico, Math.abs(v - 128));
  const pct = Math.min(100, pico / 128 * 140);
  $('gravNivel').className = 'barra ' + (pct > 95 ? 'n-fim' : pct > 75 ? 'n-alerta' : '');
  $('gravNivel').firstElementChild.style.width = pct + '%';
}
function desenharCores() {
  $('cCores').innerHTML = CORES.map(c => `<button type="button" style="background:${c}" class="${$('cDestaque').value === c ? 'on' : ''}" title="${c}" onclick="$('cDestaque').value='${c}';previaCor()"></button>`).join('');
}
function previaCor() { document.documentElement.style.setProperty('--destaque', $('cDestaque').value); desenharCores(); }
$('cDestaque').oninput = previaCor;
$('cTema').onchange = () => { document.documentElement.dataset.tema = $('cTema').value; };
function desenharFases() {
  $('cFases').innerHTML = cfgFases.map((f, k) => `<tr>
    <td><input class="campo" value="${esc(f.nome)}" oninput="cfgFases[${k}].nome=this.value"></td>
    <td><input class="campo" type="number" min="0.5" step="0.5" value="${f.min}" oninput="cfgFases[${k}].min=+this.value" style="width:80px"></td>
    <td><input class="campo" type="number" min="0" step="5" value="${f.totalMin}" oninput="cfgFases[${k}].totalMin=+this.value" style="width:90px"></td>
    <td><button class="botao pq fantasma" onclick="cfgFases.splice(${k},1);desenharFases()">✕</button></td></tr>`).join('');
}
function desenharPartidos() {
  $('cPartidos').innerHTML = cfgPartidos.map(([s, c], k) => `<div style="display:flex;gap:6px;align-items:center">
    <input type="color" value="${c}" oninput="cfgPartidos[${k}][1]=this.value">
    <input class="campo" value="${esc(s)}" oninput="cfgPartidos[${k}][0]=this.value">
    <button class="botao pq fantasma" onclick="cfgPartidos.splice(${k},1);desenharPartidos()">✕</button></div>`).join('');
}
async function salvarConfig() {
  await A('configSalvar', {
    casa: { nome: $('cNome').value, titulo: $('cTitulo').value, destaque: $('cDestaque').value, tema: $('cTema').value },
    cfg: { alerta: +$('cAlerta').value, aparte: +$('cAparte').value, ordem: +$('cOrdem').value, desconta: $('cDesconta').checked, corte: $('cCorte').checked,
      som: $('cSom').checked, autoInscricao: $('cAuto').checked, exigePresenca: $('cExige').checked, presencaCelular: $('cPresCel').checked, transcricaoAutoPublicar: $('cTranscPub').checked },
    fases: cfgFases, partidos: Object.fromEntries(cfgPartidos)
  });
  PL.avisar('Configurações salvas.');
}
$('cLogo').onchange = async e => {
  const arq = e.target.files[0]; if (!arq) return;
  await A('imagem', { alvo: 'logo', dataUrl: await PL.lerImagem(arq, 512, false) });
  await A('imagem', { alvo: 'logoAta', dataUrl: await PL.lerImagem(arq, 400, false, 'jpeg') });  // versão para a ata em PDF
  e.target.value = '';
};
async function logoAggilli() { await A('imagem', { alvo: 'logo' }); await A('imagem', { alvo: 'logoAta' }); }
function zerar() {
  dialogo('Zerar sessão', `<p style="margin:0">Apaga filas, presença, pedidos, histórico de falas e votações. Parlamentares e configurações são mantidos.</p>
    <label class="linha">Manter a pauta (volta para “pendente”)<input type="checkbox" id="dManter" checked></label>`,
    () => A('sessaoZerar', { manterPauta: $('dManter').checked }), 'Zerar sessão');
}

/* ---------------- plenário virtual ---------------- */
let projetosParaVirtual = null;
const SIT_V = { agendada: ['Agendada', ''], aberta: ['Votação aberta', 'sim'], apurando: ['Apurando…', 'ambar'], encerrada: ['Encerrada', ''], cancelada: ['Cancelada', 'nao'] };
const RES_V = { aprovada: 'sim', rejeitada: 'nao', sem_quorum: 'ambar', destacado: 'interr' };
const dt = ms => new Date(ms).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
const valorLocal = ms => { const d = new Date(ms - new Date().getTimezoneOffset() * 60000); return d.toISOString().slice(0, 16); };
async function alternarRemoto() {
  await A('remotoConfigurar', { ativo: !PL.estado.remoto.ativo, servidor: $('remServidor').value.trim() || PL.estado.remoto.servidor });
}
async function abrirVideo() {
  if (PL.temVideo()) { PL.encerrarVideo(); $('remVideo').classList.add('oculto'); desenharVirtual(); return; }
  $('remVideo').classList.remove('oculto');
  try { await PL.video($('remVideo'), 'presidente'); } catch (e) { $('remVideo').classList.add('oculto'); PL.avisar(e.message, true); }
  desenharVirtual();
}
async function criarVirtual() {
  const ini = new Date($('vInicio').value).getTime(), fim = new Date($('vFim').value).getTime();
  await PL.acao('presidente', 'virtualCriar', { titulo: $('vTitulo').value, inicio: ini, fim }).then(() => { $('vTitulo').value = ''; PL.avisar('Sessão virtual criada. Inclua os itens antes da abertura.'); }).catch(() => {});
}
async function carregarProjetosVirtual() {
  try { projetosParaVirtual = (await (await apiGet('/api/transparencia?interno=1')).json()).projetos.filter(p => ['em_tramitacao', 'em_pauta'].includes(p.situacao)); }
  catch { projetosParaVirtual = []; }
  desenharVirtual();
}
function desenharVirtual() {
  const S = PL.estado, rem = S.remoto;
  if (document.activeElement !== $('remServidor')) $('remServidor').value = rem.servidor || '';
  $('remEstado').innerHTML = rem.ativo ? '<span class="chip vivo"><span class="ponto"></span>Ativa</span>' : '<span class="chip">Desligada</span>';
  $('remAlternar').textContent = rem.ativo ? 'Desligar sessão remota' : 'Ligar sessão remota';
  $('remAlternar').className = 'botao ' + (rem.ativo ? 'perigo' : 'ok');
  $('remVideoBt').disabled = !rem.ativo; $('remVideoBt').textContent = PL.temVideo() ? 'Fechar vídeo' : 'Abrir vídeo aqui';
  const remotos = Object.entries(S.presencaModo || {}).filter(([, m]) => m === 'remoto').map(([pid]) => parl(pid));
  $('remPresentes').innerHTML = rem.ativo ? (remotos.length ? `<div class="detalhe">Presentes remotamente (${remotos.length}):</div><div class="grupo" style="margin-top:6px">${remotos.map(p => `<span class="chip">${esc(p.nome)} ${PL.micChip(p.id)}</span>`).join('')}</div>` : '<p class="detalhe">Nenhum vereador remoto ainda.</p>') : '';
  if (!$('vInicio').value) { const h = Date.now() + 3600000; $('vInicio').value = valorLocal(h - h % 3600000); $('vFim').value = valorLocal(h - h % 3600000 + 3 * 86400000); }
  if (projetosParaVirtual === null && S.virtuais.some(v => v.status === 'agendada')) { projetosParaVirtual = []; carregarProjetosVirtual(); }
  const n = S.parls.length;
  $('listaVirtuais').innerHTML = S.virtuais.length ? S.virtuais.map(v => {
    const [rot, cls] = SIT_V[v.status];
    const usados = new Set(v.itens.map(i => i.projeto));
    return `<div class="cartao ${v.status === 'aberta' ? '' : 'neutro'}">
      <h2>${esc(v.titulo)}<span class="esp"></span><span class="chip ${cls}">${rot}</span></h2>
      <div class="detalhe">Votação de ${dt(v.inicio)} a ${dt(v.fim)} · ${v.itens.length} item(ns)</div>
      ${v.itens.map(it => {
        const votos = Object.values(it.votos), c = { sim: 0, nao: 0, abst: 0 };
        votos.forEach(x => c[x.valor]++);
        return `<div style="border-top:1px solid var(--linha);padding:10px 0;margin-top:8px">
          <div style="display:flex;gap:8px;align-items:flex-start"><div style="flex:1"><b>${esc(it.numero)}</b> <small class="suave">${esc(it.autor || '')} · ${PL.QUORUNS[it.quorum]}</small>
            <div style="font-size:13px">${esc(it.ementa)}</div></div>
            ${it.resultado ? `<span class="chip ${RES_V[it.resultado] || ''}">${it.resultado === 'destacado' ? 'Destacado' : PL.SITUACOES[it.resultado]}</span>` : ''}
            ${v.status === 'agendada' ? `<button class="botao pq fantasma" onclick="A('virtualItem',{vid:'${v.id}',projetoId:'${it.projeto}',remover:true})">✕</button>` : ''}</div>
          ${v.status !== 'agendada' ? `<div class="tnum" style="font-size:13px;margin-top:4px">Sim <b>${c.sim}</b> · Não <b>${c.nao}</b> · Abst. <b>${c.abst}</b> · votaram ${votos.length} de ${n}
            ${it.destaques.length ? ` · <span class="chip interr" style="font-size:10px">destaque: ${it.destaques.map(d => esc(parl(d).nome)).join(', ')}</span>` : ''}</div>` : ''}
          ${it.manifestacoes.length ? `<details style="font-size:13px;margin-top:4px"><summary class="suave" style="cursor:pointer">${it.manifestacoes.length} manifestação(ões)</summary>${it.manifestacoes.map(m => `<p style="margin:6px 0"><b>${esc(m.nome)}</b> <small class="suave">${dt(m.ts)}</small><br>${esc(m.texto)}</p>`).join('')}</details>` : ''}
        </div>`;
      }).join('') || '<p class="vazio">Sem itens ainda.</p>'}
      ${v.status === 'agendada' ? `<div class="grupo" style="margin-top:10px">
          <select class="campo" id="vProj_${v.id}" style="flex:1;min-width:200px">${(projetosParaVirtual || []).filter(p => !usados.has(p.id)).map(p => `<option value="${p.id}">${esc(p.numero)} — ${esc(p.ementa.slice(0, 70))}</option>`).join('')}</select>
          <select class="campo" id="vQ_${v.id}" style="width:auto"><option value="simples">Maioria simples</option><option value="absoluta">Maioria absoluta</option><option value="dois_tercos">Dois terços</option></select>
          <button class="botao pq" onclick="A('virtualItem',{vid:'${v.id}',projetoId:$('vProj_${v.id}').value,quorum:$('vQ_${v.id}').value})">Incluir</button></div>` : ''}
      <div class="grupo" style="justify-content:flex-end;margin-top:10px">
        ${['agendada', 'aberta'].includes(v.status) ? `<button class="botao pq fantasma" onclick="if(confirm('Cancelar esta sessão virtual? Os votos serão descartados.'))A('virtualCancelar',{vid:'${v.id}'})">Cancelar sessão</button>` : ''}
        ${v.status === 'aberta' ? `<button class="botao pq perigo" onclick="if(confirm('Encerrar a votação agora, antes do prazo? Fica registrado na ata.'))A('virtualEncerrarAgora',{vid:'${v.id}'})">Encerrar agora</button>` : ''}
        ${v.arquivo ? `<button class="botao pq principal" onclick="abrirAta('${v.arquivo}')">Ata PDF</button>` : ''}
      </div></div>`;
  }).join('') : '<div class="cartao neutro"><p class="vazio" style="margin:0">Nenhuma sessão virtual ainda.</p></div>';
}

/* ---------------- banco de projetos ---------------- */
let dadosProj = null;
const PARTES = ['beneficiados', 'abrangencia', 'vulneraveis', 'urgencia'];
const NOMES_PARTES = { beneficiados: 'Pessoas beneficiadas', abrangencia: 'Abrangência', vulneraveis: 'Grupos vulneráveis', urgencia: 'Urgência' };
const SIT_PROJ = { em_tramitacao: 'Em tramitação', em_pauta: 'Em pauta', aprovada: 'Aprovado', rejeitada: 'Rejeitado', retirada: 'Retirado', arquivado: 'Arquivado', proposta: 'Proposta' };
async function carregarProjetos() {
  projetosRev = PL.estado.projetosRev;
  try { dadosProj = await (await apiGet('/api/transparencia?interno=1')).json(); } catch (e) { return PL.avisar(e.message, true); }
  if ($('pArea').options.length === 1) $('pArea').innerHTML += Object.entries(dadosProj.areas).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join('');
  const rel = PL.estado.relevancia;
  if (document.activeElement?.closest?.('#aba-projetos .cartao') == null || !$('rPop').value) {
    $('rPop').value = rel.populacao; $('rBenef').value = rel.pesos.beneficiados; $('rAbr').value = rel.pesos.abrangencia;
    $('rVuln').value = rel.pesos.vulneraveis; $('rUrg').value = rel.pesos.urgencia;
  }
  desenharProjetos();
}
['pBusca', 'pArea', 'pSituacao'].forEach(id => $(id).addEventListener('input', () => desenharProjetos()));
function barraNota(r) {
  if (!r) return '<span class="suave" style="font-size:12px">sem ficha social</span>';
  return `<div class="nota" title="${PARTES.map(k => `${NOMES_PARTES[k]}: ${r.partes[k]}`).join(' · ')}"><div class="b">${PARTES.map(k => `<i class="c-${k}" style="width:${r.partes[k]}%"></i>`).join('')}</div><b>${r.nota}</b></div>`;
}
const fmtNum = n => (n || 0).toLocaleString('pt-BR');
function desenharProjetos() {
  if (!dadosProj) return;
  const q = $('pBusca').value.toLowerCase(), area = $('pArea').value, sit = $('pSituacao').value;
  const lista = dadosProj.projetos.filter(p => (!q || (p.numero + ' ' + p.ementa + ' ' + p.autor).toLowerCase().includes(q))
    && (!area || (p.areas || []).includes(area)) && (!sit || p.situacao === sit));
  const naPauta = new Set(PL.estado.pauta.map(m => m.id));
  $('tabProjetos').innerHTML = lista.length ? '<tr><th>#</th><th>Projeto</th><th>Áreas</th><th class="num">Beneficiados</th><th>Relevância</th><th>Situação</th><th></th></tr>' +
    lista.map((p, k) => `<tr><td class="suave tnum">${p.relevancia ? k + 1 : '—'}</td>
      <td><b>${esc(p.numero)}</b><div style="font-size:13px;max-width:420px">${esc(p.ementa)}</div><small class="suave">${esc(p.autor || '')}</small></td>
      <td><div class="tags">${(p.areas || []).map(a => `<span class="tag">${esc(dadosProj.areas[a])}</span>`).join('')}</div></td>
      <td class="num tnum">${p.beneficiados ? fmtNum(p.beneficiados) : '—'}</td>
      <td>${barraNota(p.relevancia)}</td>
      <td><span class="chip ${p.situacao === 'aprovada' ? 'sim' : p.situacao === 'rejeitada' ? 'nao' : p.situacao === 'em_pauta' ? 'ambar' : ''}">${SIT_PROJ[p.situacao] || p.situacao}</span></td>
      <td style="white-space:nowrap;text-align:right"><button class="botao pq" onclick="editarProjeto('${p.id}')">Ficha</button>
        ${p.situacao !== 'proposta' ? `<button class="botao pq" onclick="gerirConsulta('${p.id}')" title="Texto, consulta pública e anexo">Texto e consulta</button>` : ''}
        ${['em_tramitacao', 'proposta'].includes(p.situacao) && !naPauta.has(p.id) ? `<button class="botao pq principal" onclick="A('pautaIncluir',{id:'${p.id}'})">Pautar</button>` : ''}
        ${!naPauta.has(p.id) ? `<button class="botao pq fantasma" onclick="if(confirm('Remover ${esc(p.numero)} do banco de projetos?'))A('projetoRemover',{id:'${p.id}'})">✕</button>` : ''}</td></tr>`).join('')
    : '<tr><td class="vazio">Nenhum projeto encontrado.</td></tr>';
  const t = dadosProj.projetos, comFicha = t.filter(p => p.relevancia);
  $('projResumo').innerHTML = `<div class="numeros" style="margin:0">
    <div><b>${t.length}</b>projetos</div><div><b>${comFicha.length}</b>com ficha social</div>
    <div><b>${t.filter(p => p.situacao === 'aprovada').length}</b>aprovados</div><div><b>${fmtNum(dadosProj.totais.beneficiadosAprovados)}</b>pessoas em projetos aprovados</div></div>
    <div class="grupo" style="margin-top:12px;font-size:12px">${PARTES.map(k => `<span style="display:inline-flex;align-items:center;gap:5px"><i class="c-${k}" style="width:10px;height:10px;border-radius:3px;display:inline-block"></i>${NOMES_PARTES[k]}</span>`).join('')}</div>`;
}
function notaLocal(p) {  // mesma fórmula do servidor, para a prévia ao preencher a ficha
  if (!p.beneficiados && !p.abrangencia) return null;
  const rel = PL.estado.relevancia, pop = Math.max(rel.populacao, 1000);
  const comp = { beneficiados: Math.min(1, Math.log10(p.beneficiados + 1) / Math.log10(pop + 1)),
    abrangencia: { local: 1 / 3, regional: 2 / 3, municipal: 1 }[p.abrangencia] || 0,
    vulneraveis: Math.min(1, p.vulneraveis.length / 3), urgencia: { baixa: .2, media: .6, alta: 1 }[p.urgencia] || 0 };
  const soma = PARTES.reduce((a, k) => a + Math.max(0, rel.pesos[k]), 0) || 1;
  const partes = Object.fromEntries(PARTES.map(k => [k, Math.round(1000 * Math.max(0, rel.pesos[k]) * comp[k] / soma) / 10]));
  return { nota: Math.round(PARTES.reduce((a, k) => a + partes[k], 0)), partes };
}
function editarProjeto(id) {
  const d = dadosProj, p = id ? d.projetos.find(x => x.id === id) : { numero: '', ementa: '', autor: '', areas: [], beneficiados: 0, abrangencia: '', vulneraveis: [], urgencia: '', fonte: '', situacao: 'em_tramitacao', historico: [] };
  const naPauta = PL.estado.pauta.some(m => m.id === id);
  const opcoes = (nome, mapa, marcados) => `<div class="opcoes">${Object.entries(mapa).map(([k, v]) => `<label><input type="checkbox" name="${nome}" value="${k}" ${marcados.includes(k) ? 'checked' : ''} hidden>${esc(v)}</label>`).join('')}</div>`;
  dlg.classList.add('largo');
  dialogo(id ? 'Ficha do projeto' : 'Novo projeto', `
    <div style="display:grid;grid-template-columns:1fr 2fr;gap:10px">
      <label class="rot">Número<input class="campo" id="fNum" value="${esc(p.numero)}" placeholder="PL 123/2026"></label>
      <label class="rot">Autoria<input class="campo" id="fAutor" value="${esc(p.autor)}" list="dlAutores"></label>
    </div>
    <datalist id="dlAutores">${PL.estado.parls.map(x => `<option value="${esc(x.nome)}">`).join('')}<option value="Executivo Municipal"><option value="Mesa Diretora"></datalist>
    <label class="rot">Ementa<textarea class="campo" id="fEmenta" rows="2">${esc(p.ementa)}</textarea></label>
    <div class="rot">Áreas${opcoes('fArea', d.areas, p.areas || [])}</div>
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px">
      <label class="rot">Pessoas beneficiadas (estimativa)<input class="campo" type="number" min="0" id="fBenef" value="${p.beneficiados || ''}"></label>
      <label class="rot">Abrangência<select class="campo" id="fAbr"><option value="">—</option>${Object.entries(d.abrangenciaNomes).map(([k, v]) => `<option value="${k}" ${p.abrangencia === k ? 'selected' : ''}>${esc(v)}</option>`).join('')}</select></label>
      <label class="rot">Urgência<select class="campo" id="fUrg"><option value="">—</option>${Object.entries(d.urgenciaNomes).map(([k, v]) => `<option value="${k}" ${p.urgencia === k ? 'selected' : ''}>${esc(v)}</option>`).join('')}</select></label>
    </div>
    <label class="rot">Fonte da estimativa<input class="campo" id="fFonte" value="${esc(p.fonte || '')}" placeholder="Ex.: Censo IBGE 2022; cadastro da Secretaria de Saúde"></label>
    <div class="rot">Grupos vulneráveis atendidos${opcoes('fVuln', d.vulneraveisNomes, p.vulneraveis || [])}</div>
    ${!naPauta ? `<label class="linha">Situação<select class="campo" id="fSit" style="width:auto">${[...(p.situacao === 'proposta' ? ['proposta'] : []), 'em_tramitacao', 'aprovada', 'rejeitada', 'retirada', 'arquivado'].map(k => `<option value="${k}" ${p.situacao === k ? 'selected' : ''}>${SIT_PROJ[k]}</option>`).join('')}</select></label>` : '<p class="detalhe" style="margin:0">Na pauta da sessão: a situação muda pela votação.</p>'}
    <div class="previa-nota" id="fPrevia"></div>
    ${p.saplUrl ? `<p class="detalhe" style="margin:0">Importado do SAPL · <a href="${esc(p.saplUrl)}" target="_blank" rel="noopener noreferrer">abrir no SAPL ↗</a>${p.textoUrl ? ` · <a href="${esc(p.textoUrl)}" target="_blank" rel="noopener noreferrer">texto original ↗</a>` : ''}</p>` : ''}
    ${p.situacao === 'proposta' ? `<div class="rot">Proposta de parlamentar${p.instrumento ? ' · instrumento sugerido: ' + esc(p.instrumento) : ''}
      ${p.justificativa ? `<div style="color:var(--texto);font-size:13px;white-space:pre-wrap">${esc(p.justificativa)}</div>` : ''}
      <small>Para protocolar, informe o número oficial e mude a situação para “Em tramitação”.</small></div>` : ''}
    ${(p.historico || []).length ? `<div class="rot">Histórico<div style="font-size:13px;color:var(--texto)">${p.historico.map(h => `${new Date(h.ts).toLocaleDateString('pt-BR')} · ${esc(h.sessao)} · ${h.evento === 'votacao' ? 'votação: ' + (PL.SITUACOES[h.resultado] || h.resultado) + (h.contagem ? ` (${h.contagem.sim}×${h.contagem.nao})` : '') : 'em pauta: ' + (PL.SITUACOES[h.situacao] || h.situacao)}`).join('<br>')}</div></div>` : ''}`,
    async () => {
      await PL.acao('presidente', 'projetoSalvar', fichaDoFormulario(id));
      carregarProjetos();
    });
  const previa = () => {
    const r = notaLocal(fichaDoFormulario(id));
    $('fPrevia').innerHTML = r ? `<b>${r.nota}</b><div style="flex:1">${barraNota(r)}<small class="suave">${PARTES.map(k => `${NOMES_PARTES[k]} ${r.partes[k]}`).join(' · ')}</small></div>`
      : '<span class="suave">Informe beneficiados ou abrangência para calcular a relevância.</span>';
  };
  $('dlgCorpo').addEventListener('input', previa); previa();
  dlg.addEventListener('close', () => dlg.classList.remove('largo'), { once: true });
}
function fichaDoFormulario(id) {
  const marcados = nome => [...document.querySelectorAll(`input[name=${nome}]:checked`)].map(i => i.value);
  return { id, numero: $('fNum').value, ementa: $('fEmenta').value, autor: $('fAutor').value, areas: marcados('fArea'),
    beneficiados: +$('fBenef').value || 0, abrangencia: $('fAbr').value, vulneraveis: marcados('fVuln'), urgencia: $('fUrg').value,
    fonte: $('fFonte').value, situacao: $('fSit')?.value };
}
async function salvarRelevancia() {
  await A('relevanciaSalvar', { populacao: +$('rPop').value, pesos: { beneficiados: +$('rBenef').value, abrangencia: +$('rAbr').value, vulneraveis: +$('rVuln').value, urgencia: +$('rUrg').value } });
  PL.avisar('Critérios salvos.');
}
async function escolherDoBanco() {
  let d;
  try { d = await (await apiGet('/api/transparencia?interno=1')).json(); } catch (e) { return PL.avisar(e.message, true); }
  const naPauta = new Set(PL.estado.pauta.map(m => m.id));
  const disp = d.projetos.filter(p => ['em_tramitacao', 'proposta'].includes(p.situacao) && !naPauta.has(p.id));
  dialogo('Incluir projetos na pauta', disp.length ? `<p class="detalhe" style="margin:0">Ordenados por relevância social.</p>` + disp.map(p => `<label class="linha" style="align-items:flex-start;border-top:1px solid var(--linha)">
      <span><input type="checkbox" name="dPautar" value="${p.id}"> <b>${esc(p.numero)}</b> ${esc(p.ementa)}</span>${barraNota(p.relevancia)}</label>`).join('')
    : '<p class="vazio">Nenhum projeto em tramitação disponível.</p>',
    async () => { for (const i of document.querySelectorAll('input[name=dPautar]:checked')) await PL.acao('presidente', 'pautaIncluir', { id: i.value }); }, 'Incluir na pauta');
}

/* ---------------- sessões e ata ---------------- */
let sessoesArquivadas = [];
async function apiGet(caminho) {
  const r = await fetch(caminho, { headers: { Authorization: 'Bearer ' + PL.token('presidente') } });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).erro || 'Falha ao carregar.');
  return r;
}
function desenharSessaoAtual() {
  const S = PL.estado;
  const marcos = [...Object.values(S.presenca), ...S.hist.map(h => h.inicio), ...S.ocorrencias.map(o => o.ts)];
  const inicio = marcos.length ? Math.min(...marcos) : null;
  $('sessaoAtual').innerHTML = `<h2>Sessão em andamento</h2>
    <div style="font-size:22px;font-weight:700">${esc(S.casa.titulo)}</div>
    <div class="detalhe">${inicio ? 'Primeiro registro às ' + hora(inicio) + ' de ' + new Date(inicio).toLocaleDateString('pt-BR') : 'Nenhum registro ainda'}</div>
    <div class="numeros">
      <div><b>${Object.keys(S.presenca).length}/${S.parls.length}</b>presentes</div>
      <div><b>${S.hist.length}</b>pronunciamentos</div>
      <div><b>${S.votacoes.length}</b>votações</div>
      <div><b>${(S.gravacoes || []).length}</b>gravações</div>
      <div><b>${S.ocorrencias.length}</b>ocorrências</div>
    </div>
    <div class="grupo" style="justify-content:flex-end">
      <button class="botao" onclick="abrirAta('atual')">Prévia da ata (PDF)</button>
      <button class="botao principal" onclick="encerrarSessao()">Encerrar e arquivar sessão…</button>
    </div>`;
}
async function carregarSessoes() {
  try { sessoesArquivadas = (await (await apiGet('/api/sessoes')).json()).sessoes; }
  catch (e) { return PL.avisar(e.message, true); }
  const dur = s => tempo(s.fim - s.inicio);
  $('tabSessoes').innerHTML = sessoesArquivadas.length
    ? '<tr><th>Data</th><th>Sessão</th><th>Duração</th><th class="num">Presentes</th><th class="num">Falas</th><th class="num">Votações</th><th class="num">Gravações</th><th></th></tr>' +
      sessoesArquivadas.map(s => `<tr><td class="tnum">${new Date(s.inicio).toLocaleDateString('pt-BR')}<br><small class="suave">${hora(s.inicio)}–${hora(s.fim)}</small></td>
        <td><b>${esc(s.titulo)}</b></td><td class="tnum">${dur(s)}</td><td class="num">${s.presentes}/${s.membros}</td>
        <td class="num">${s.falas}</td><td class="num">${s.votacoes}</td><td class="num">${s.gravacoes}</td>
        <td style="text-align:right;white-space:nowrap"><button class="botao pq principal" onclick="abrirAta('${s.id}')">Ata PDF</button>
          <button class="botao pq" onclick="verSessao('${s.id}')">Detalhes</button>
          <button class="botao pq fantasma" onclick="excluirSessao('${s.id}')">✕</button></td></tr>`).join('')
    : '<tr><td class="vazio">Nenhuma sessão arquivada. Use “Encerrar e arquivar sessão” ao fim de cada sessão.</td></tr>';
}
async function abrirAta(id) {
  const janela = window.open('', '_blank');  // abre já no clique, para o navegador não bloquear
  try {
    const blob = await (await apiGet(`/api/ata/${id}.pdf`)).blob();
    const url = URL.createObjectURL(blob);
    if (janela) janela.location = url;
    else { const a = document.createElement('a'); a.href = url; a.download = 'ata.pdf'; a.click(); }
  } catch (e) { janela?.close(); PL.avisar(e.message, true); }
}
function proximoTitulo(t) {
  return t.replace(/(\d+)(ª|º|a|o)?/, (_, n, suf) => (+n + 1) + (suf || ''));
}
function encerrarSessao() {
  const S = PL.estado;
  const naoVotadas = S.pauta.filter(m => ['pendente', 'em_discussao', 'em_votacao', 'adiada'].includes(m.situacao)).length;
  dialogo('Encerrar e arquivar sessão', `
    <p style="margin:0">A sessão <b>${esc(S.casa.titulo)}</b> será arquivada com presença, falas, votações, ocorrências e gravações.
      A ata em PDF fica disponível em “Sessões anteriores”. Filas, presença e histórico recomeçam em branco.</p>
    ${S.votacao?.aberta ? '<p class="chip nao" style="margin:0">Há uma votação aberta: encerre-a antes.</p>' : ''}
    ${S.orador ? '<p class="chip ambar" style="margin:0">O orador na tribuna será encerrado.</p>' : ''}
    <label class="rot">Título da próxima sessão<input class="campo" id="dProx" value="${esc(proximoTitulo(S.casa.titulo))}"></label>
    <label class="linha">Levar para a próxima sessão as ${naoVotadas} matéria(s) não votada(s)<input type="checkbox" id="dManterPauta" checked></label>`,
    async () => {
      await PL.acao('presidente', 'sessaoEncerrar', { proximoTitulo: $('dProx').value, manterPauta: $('dManterPauta').checked });
      PL.avisar('Sessão arquivada.');
      carregarSessoes();
    }, 'Encerrar e arquivar');
}
async function verSessao(id) {
  let r;
  try { r = await (await apiGet('/api/sessoes/' + id)).json(); } catch (e) { return PL.avisar(e.message, true); }
  const nome = pid => r.parls.find(p => p.id === pid)?.nome || '?';
  dlg.classList.add('largo');
  dialogo(r.titulo, `
    <div class="detalhe">${new Date(r.inicio).toLocaleString('pt-BR')} — ${hora(r.fim)} · ${Object.keys(r.presenca).length}/${r.parls.length} presentes</div>
    <h3 style="font-size:15px;margin:8px 0 0">Votações</h3>
    ${r.votacoes.length ? '<table class="tab">' + r.votacoes.map(v => `<tr><td><b>${esc(v.numero)}</b> ${esc(v.ementa)}</td><td>${v.tipo}</td>
      <td class="tnum">${v.contagem ? `${v.contagem.sim} × ${v.contagem.nao} (${v.contagem.abst})` : '—'}</td>
      <td><span class="chip ${v.resultado === 'aprovada' ? 'sim' : v.resultado === 'rejeitada' ? 'nao' : 'ambar'}">${PL.SITUACOES[v.resultado]}</span></td></tr>`).join('') + '</table>'
      : '<p class="vazio">Nenhuma.</p>'}
    <h3 style="font-size:15px;margin:8px 0 0">Pronunciamentos</h3>
    ${r.hist.length ? '<table class="tab">' + r.hist.map(h => `<tr><td class="tnum">${hora(h.inicio)}</td><td>${esc(h.nome)} ${sigla(h.partido)}</td>
      <td>${esc(h.faseNome)}</td><td class="tnum">${tempo(h.usado * 1000)}</td></tr>`).join('') + '</table>' : '<p class="vazio">Nenhum.</p>'}
    <h3 style="font-size:15px;margin:8px 0 0">Gravações</h3>
    ${r.gravacoes.length ? r.gravacoes.map(g => `<div class="grav-item"><div><b>${esc(g.nome)}</b> <span class="suave">${esc(g.faseNome)} · ${hora(g.inicio)}</span></div>
      <audio controls preload="none" src="/gravacao/${g.id}.${g.ext}"></audio>${transcricaoHtml(g)}</div>`).join('') : '<p class="vazio">Nenhuma.</p>'}
    ${r.ocorrencias.length ? `<h3 style="font-size:15px;margin:8px 0 0">Ocorrências</h3><table class="tab">${r.ocorrencias.map(o => `<tr><td class="tnum">${hora(o.ts)}</td>
      <td>${PL.OCORRENCIAS[o.tipo] || o.tipo}</td><td>${esc(o.nome || '—')}</td><td class="suave">${esc(o.motivo || '')}</td></tr>`).join('')}</table>` : ''}`,
    () => abrirAta(id), 'Abrir ata em PDF');
  dlg.addEventListener('close', () => dlg.classList.remove('largo'), { once: true });
}
function excluirSessao(id) {
  const s = sessoesArquivadas.find(x => x.id === id);
  dialogo('Excluir sessão arquivada', `<p style="margin:0">Excluir <b>${esc(s.titulo)}</b> de ${new Date(s.inicio).toLocaleDateString('pt-BR')}?
    O registro e as <b>${s.gravacoes} gravação(ões)</b> de áudio serão apagados <b>definitivamente</b>. Baixe a ata em PDF antes, se precisar.</p>`,
    async () => { await PL.acao('presidente', 'sessaoArquivoRemover', { id }); carregarSessoes(); }, 'Excluir definitivamente');
  $('dlgOk').className = 'botao perigo';
  dlg.addEventListener('close', () => { $('dlgOk').className = 'botao principal'; }, { once: true });
}

/* ---------------- diálogo genérico ---------------- */
/* ---------------- transcrição dos discursos ---------------- */
const TRANSC_STATUS = { pendente: ['ambar', 'Na fila para transcrever'], processando: ['ambar', 'Transcrevendo…'],
  pronta: ['sim', 'Transcrita'], erro: ['nao', 'Falha na transcrição'], vazia: ['', 'Sem texto'] };
function transcricaoHtml(g) {
  const t = g.transcricao, ligada = PL.estado?.transcricaoMotor && PL.estado.transcricaoMotor !== 'desligada';
  if (!t) return `<div class="transc"><span class="suave">${ligada ? 'Sem transcrição.' : 'Transcrição automática desligada.'}</span>
    <span style="flex:1"></span>${ligada ? `<button class="botao pq fantasma" onclick="A('transcricaoRefazer',{id:'${g.id}'})">Transcrever</button>` : ''}
    <button class="botao pq fantasma" onclick="editarTranscricao('${g.id}')">Digitar texto</button></div>`;
  const [cor, rot] = TRANSC_STATUS[t.status] || ['', t.status];
  const palavras = t.palavras ?? (t.texto || '').split(/\s+/).filter(Boolean).length;
  const pronta = t.status === 'pronta';
  return `<div class="transc"><span class="chip ${cor}">${rot}</span>
    ${pronta ? `<span class="suave">${palavras} palavras · ${t.revisada ? 'revisada' : 'automática'}</span>
      <span class="chip ${t.publicada ? 'sim' : ''}">${t.publicada ? 'Pública' : 'Não publicada'}</span>` : ''}
    ${t.status === 'erro' ? `<span class="suave" title="${esc(t.erro || '')}">${esc((t.erro || '').slice(0, 80))}</span>` : ''}
    <span style="flex:1"></span>
    ${t.status !== 'processando' && t.status !== 'pendente' ? `<button class="botao pq ${pronta ? '' : 'fantasma'}" onclick="editarTranscricao('${g.id}')">${pronta ? 'Ler e revisar' : 'Digitar texto'}</button>` : ''}
    ${pronta ? `<button class="botao pq fantasma" onclick="A('transcricaoSalvar',{id:'${g.id}',publicada:${!t.publicada}})">${t.publicada ? 'Despublicar' : 'Publicar'}</button>` : ''}
    ${ligada && (t.status === 'erro' || pronta) ? `<button class="botao pq fantasma" title="Transcrever de novo a partir do áudio" onclick="if(confirm('Transcrever de novo? O texto atual, inclusive correções, será substituído.'))A('transcricaoRefazer',{id:'${g.id}'})">Refazer</button>` : ''}
  </div>`;
}
async function editarTranscricao(id) {
  let d;
  try { d = await (await apiGet('/api/transcricao/' + id)).json(); } catch (e) { return PL.avisar(e.message, true); }
  const t = d.transcricao || {};
  if (dlg.open) dlg.close();
  dlg.classList.add('largo');
  dialogo(`Discurso — ${d.nome}`, `
    <div class="detalhe">${esc(d.fase)} · ${new Date(d.inicio).toLocaleString('pt-BR')} · ${t.revisada ? 'texto revisado' : t.motor ? 'transcrição automática (' + esc(t.motor) + ')' : 'sem transcrição'}</div>
    <audio controls preload="none" src="${d.audio}" style="width:100%"></audio>
    <label class="rot">Texto do discurso — corrija o que a transcrição automática errou; separe parágrafos com uma linha em branco
      <textarea class="campo" id="tTexto" rows="14" style="font:inherit;line-height:1.5;resize:vertical">${esc(t.texto || '')}</textarea></label>
    <label class="linha">Publicar na página pública (busca de discursos e perfil do parlamentar)<input type="checkbox" id="tPub" ${t.publicada ? 'checked' : ''}></label>
    <p class="suave" style="margin:0;font-size:13px">Até ser publicado, só o painel do presidente vê este texto. A ata em PDF traz a íntegra e indica o que ainda é automático.</p>`,
    async () => { await PL.acao('presidente', 'transcricaoSalvar', { id, texto: $('tTexto').value, publicada: $('tPub').checked }); PL.avisar('Texto salvo.'); });
  dlg.addEventListener('close', () => dlg.classList.remove('largo'), { once: true });
}

function dialogo(titulo, html, aoConfirmar, rotulo = 'Salvar') {
  $('dlgTitulo').textContent = titulo; $('dlgCorpo').innerHTML = html; $('dlgOk').textContent = rotulo;
  $('dlgOk').onclick = async () => { try { await aoConfirmar(); dlg.close(); } catch {} };
  dlg.showModal();
}

/* ---------------- relógios (5×/s) ---------------- */
function relogios() {
  const S = PL.estado; if (!S) return;
  $('relogio').textContent = PL.relogio();
  if (abaAtual === 'sessao') pintarNivel();
  if (S.orador) PL.pintar(S.orador, $('tOrador'), $('bOrador'));
  if (S.interr) PL.pintar(S.interr, $('tInterr'));
  if (abaAtual === 'sessao' && S.orador?.start) {
    const f = fase(S.orador.fase), el = document.querySelector(`[data-fase-txt="${S.orador.fase}"]`);
    if (el && f.totalMin) {
      const u = PL.usadoNaFase(f.id), t = f.totalMin * 60000;
      el.textContent = `${tempo(u)} de ${tempo(t)}`;
      document.querySelector(`[data-fase="${f.id}"] i`).style.width = Math.min(100, u / t * 100) + '%';
    }
  }
}
setInterval(relogios, 200);

addEventListener('keydown', e => {
  if (abaAtual !== 'sessao' || e.target.matches('input,select,textarea') || dlg.open || $('app').classList.contains('oculto')) return;
  if (e.code === 'Space') { e.preventDefault(); alternar(); }
  else if (e.key === 'n' || e.key === 'N') A('proximo');
  else if (e.key === 'e' || e.key === 'E') A('encerrar');
  else if (e.key === '+') A('addTempo', { seg: 60 });
  else if (e.key === 't' || e.key === 'T') { e.preventDefault(); if (PL.estado.orador) tempoPersonalizado(); }
  else if (e.key === 'c' || e.key === 'C') { const o = PL.estado.orador; if (o) o.congelado ? A('descongelar') : congelarOrador(); }
});
if (navigator.mediaDevices?.enumerateDevices) listarEntradas();

iniciar();
