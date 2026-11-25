/* Painel do Presidente */
const A = (nome, args) => PL.acao('presidente', nome, args).catch(() => {});
const $ = id => document.getElementById(id);
const { esc, avatar, sigla, parl, fase, tempo, hora } = PL;
let abaAtual = 'sessao';
let selInterr = '';
let som = localStorage.getItem('parlatorio.som') !== '0';

/* ---------------- entrada ---------------- */
async function iniciar() {
  PL.conectar();
  const eu = await PL.quemSou('presidente');
  $(eu ? 'app' : 'login').classList.remove('oculto');
  if (!eu) $('pin').focus();
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
  desenhar();
});

/* ---------------- desenho ---------------- */
PL.aoMudar(() => desenhar());
PL.aoSinal = tipo => { if (som) PL.bipe(tipo); };
function desenhar() {
  const S = PL.estado; if (!S) return;
  $('bolhaPresenca').textContent = Object.keys(S.presenca).length + '/' + S.parls.length;
  $('btSom').textContent = som ? '🔔' : '🔕';
  if (abaAtual === 'sessao') desenharSessao();
  if (abaAtual === 'pauta') desenharPauta();
  if (abaAtual === 'plenario') desenharPlenario();
  if (abaAtual === 'relatorios') desenharRelatorios();
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

  desenharTribuna();
  desenharPedidos();
  $('quorum').innerHTML = quorumHtml();
  desenharParlsRapido();
}

function chipEstado(o) {
  const S = PL.estado;
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
      <button class="botao pq" onclick="A('encerrarInterr')">Encerrar ${PL.TIPOS[i.tipo].toLowerCase()}</button></div>`
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
      <button class="botao ${o.start ? '' : 'ok'} gr" onclick="alternar()" ${i?.pausouOrador ? 'disabled' : ''}>${o.start ? '⏸ Pausar' : o.inicio ? '▶ Retomar' : '▶ Iniciar'}</button>
      <button class="botao gr" onclick="A('addTempo',{seg:60})">+1 min</button>
      <button class="botao gr" onclick="A('addTempo',{seg:30})">+30 s</button>
      <button class="botao gr" onclick="A('addTempo',{seg:-30})">−30 s</button>
      <button class="botao perigo gr" onclick="A('encerrar')">■ Encerrar</button>
      <button class="botao principal gr" onclick="A('proximo')">Próximo ⏭</button>
    </div>
    ${blocoInterr}
    <div class="atalhos"><kbd>Espaço</kbd> iniciar/pausar · <kbd>N</kbd> próximo · <kbd>E</kbd> encerrar · <kbd>+</kbd> 1 minuto</div>`;
}
function alternar() { const o = PL.estado.orador; if (o) A(o.start ? 'pausar' : 'iniciar'); }
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
      ${tribuna ? '' : `<button class="botao pq" title="Conceder a palavra agora" onclick="A('chamar',{pid:'${p.id}'})">🎤</button>`}</span></li>`;
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
function desenharPlenario() {
  const S = PL.estado;
  $('quorumGrande').innerHTML = quorumHtml();
  $('tabParls').innerHTML = S.parls.map(p => {
    const pres = S.presenca[p.id];
    return `<tr><td style="width:52px">${avatar(p)}</td><td><b>${esc(p.nome)}</b>${p.lider ? ' <span class="chip" style="font-size:10px">Líder</span>' : ''}</td><td>${sigla(p.partido)}</td>
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
  const p = id ? parl(id) : { nome: '', partido: '', lider: false };
  dialogo(id ? 'Editar parlamentar' : 'Novo parlamentar', `
    ${id ? `<div style="display:flex;gap:12px;align-items:center">${avatar(p, '', 'width:72px;height:72px;font-size:24px')}
      <label class="rot">Foto<input type="file" accept="image/*" id="dFoto"></label>
      ${p.fotoV ? `<button class="botao pq fantasma" onclick="A('imagem',{alvo:'${id}'}).then(()=>dlg.close())">Remover foto</button>` : ''}</div>` : ''}
    <label class="rot">Nome parlamentar<input class="campo" id="dNome" value="${esc(p.nome)}"></label>
    <label class="rot">Partido<input class="campo" id="dPartido" value="${esc(p.partido)}" list="dlPartidos"></label>
    <datalist id="dlPartidos">${Object.keys(PL.estado.partidos).map(s => `<option value="${esc(s)}">`).join('')}</datalist>
    <label class="linha">Líder de bancada<input type="checkbox" id="dLider" ${p.lider ? 'checked' : ''}></label>
    <label class="rot">PIN de acesso pelo celular (4 a 8 dígitos)<input class="campo" id="dPin" inputmode="numeric" placeholder="${id ? 'deixe em branco para manter' : 'padrão: 0000'}"></label>`,
    async () => {
      await A('parlSalvar', { id, nome: $('dNome').value, partido: $('dPartido').value, lider: $('dLider').checked, pin: $('dPin').value });
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
const CORES = ['#d66060', '#1f8fff', '#19b6d8', '#4fb83b', '#2f8f5a', '#c9a227', '#8e5bd6', '#e07a2f'];
function carregarConfig() {
  const S = PL.estado; if (!S) return;
  $('cNome').value = S.casa.nome; $('cTitulo').value = S.casa.titulo; $('cTema').value = S.casa.tema; $('cDestaque').value = S.casa.destaque;
  desenharCores();
  const c = S.cfg;
  $('cAlerta').value = c.alerta; $('cAparte').value = c.aparte; $('cOrdem').value = c.ordem;
  $('cDesconta').checked = c.desconta; $('cCorte').checked = c.corte; $('cSom').checked = c.som;
  $('cAuto').checked = c.autoInscricao; $('cExige').checked = c.exigePresenca; $('cPresCel').checked = c.presencaCelular;
  cfgFases = S.fases.map(f => ({ ...f })); desenharFases();
  cfgPartidos = Object.entries(S.partidos); desenharPartidos();
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
      som: $('cSom').checked, autoInscricao: $('cAuto').checked, exigePresenca: $('cExige').checked, presencaCelular: $('cPresCel').checked },
    fases: cfgFases, partidos: Object.fromEntries(cfgPartidos)
  });
  PL.avisar('Configurações salvas.');
}
$('cLogo').onchange = async e => {
  const arq = e.target.files[0]; if (!arq) return;
  await A('imagem', { alvo: 'logo', dataUrl: await PL.lerImagem(arq, 512, false) });
  e.target.value = '';
};
function zerar() {
  dialogo('Zerar sessão', `<p style="margin:0">Apaga filas, presença, pedidos, histórico de falas e votações. Parlamentares e configurações são mantidos.</p>
    <label class="linha">Manter a pauta (volta para “pendente”)<input type="checkbox" id="dManter" checked></label>`,
    () => A('sessaoZerar', { manterPauta: $('dManter').checked }), 'Zerar sessão');
}

/* ---------------- diálogo genérico ---------------- */
function dialogo(titulo, html, aoConfirmar, rotulo = 'Salvar') {
  $('dlgTitulo').textContent = titulo; $('dlgCorpo').innerHTML = html; $('dlgOk').textContent = rotulo;
  $('dlgOk').onclick = async () => { try { await aoConfirmar(); dlg.close(); } catch {} };
  dlg.showModal();
}

/* ---------------- relógios (5×/s) ---------------- */
function relogios() {
  const S = PL.estado; if (!S) return;
  $('relogio').textContent = PL.relogio();
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
});

iniciar();
