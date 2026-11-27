/* Emendas estruturadas — painel da presidência (aba Projetos), app do parlamentar e página pública (/emendas).
   Todo texto vindo do servidor passa por esc(); o comparativo chega como operações [op, texto] e só aqui
   vira HTML: o texto é escapado ANTES de ser envolvido por <ins>/<del>. */
const Emendas = (() => {
  const temPL = typeof PL !== 'undefined';
  const esc = temPL ? PL.esc : s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const papel = document.getElementById('aba-projetos') ? 'presidente' : document.getElementById('cartaoRegimento') ? 'parlamentar' : null;
  const token = () => (temPL && papel ? PL.token(papel) : null);
  const avisar = (m, erro) => (temPL ? PL.avisar(m, erro) : alert(m));
  let caixa = null, atual = '', dados = null, comp = null, compAlvo = null, modo = papel === 'parlamentar' ? 'inline' : 'lado', soMudancas = true, rev = null;

  async function get(url) {
    const t = token();
    const r = await fetch(url, { headers: t ? { Authorization: 'Bearer ' + t } : {} });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.erro || 'Falha ao carregar.');
    return j;
  }
  const acao = (nome, args) => PL.acao(papel, nome, args);

  function opsHtml(ops, lado = '') {
    return ops.map(([o, t]) => o === '=' ? esc(t)
      : o === '-' ? (lado === 'depois' ? '' : `<del>${esc(t)}</del>`)
      : (lado === 'antes' ? '' : `<ins>${esc(t)}</ins>`)).join('');
  }
  const data = ms => ms ? new Date(ms).toLocaleDateString('pt-BR') : '';
  const chip = s => `<span class="chip ${s === 'aprovada' ? 'sim' : s === 'rejeitada' ? 'nao' : s === 'apresentada' ? 'ambar' : ''}">${esc(dados.situacoes[s] || s)}</span>`;

  /* ---------- estilos próprios (uma vez) ---------- */
  if (!document.getElementById('emEstilo')) {
    const st = document.createElement('style');
    st.id = 'emEstilo';
    st.textContent = `
      .em ins { background: color-mix(in srgb, var(--sim, #1f8a4c) 28%, transparent); text-decoration: none; border-radius: 3px }
      .em del { background: color-mix(in srgb, var(--nao, #c0392b) 26%, transparent); border-radius: 3px }
      .em .em-txt { white-space: pre-wrap; line-height: 1.55 }
      .em .em-cartao { background: var(--cartao2); border-radius: 10px; padding: 10px 12px; margin-top: 8px; font-size: 14px }
      .em .em-cartao.retirada, .em .em-cartao.prejudicada { opacity: .65 }
      .em .em-linha { padding: 4px 8px; border-left: 3px solid transparent; white-space: pre-wrap; line-height: 1.5 }
      .em .em-linha.alterado { border-left-color: var(--ambar, #e5a100) } .em .em-linha.acrescido { border-left-color: var(--sim, #1f8a4c) }
      .em .em-linha.suprimido { border-left-color: var(--nao, #c0392b) } .em .em-linha.renumerado { border-left-color: var(--azul, #3a7bd5) }
      .em table.em-lado { width: 100%; border-collapse: collapse; table-layout: fixed }
      .em table.em-lado td, .em table.em-lado th { vertical-align: top; padding: 6px 8px; border-top: 1px solid var(--linha); white-space: pre-wrap; text-align: left; font-size: 14px }
      .em .em-disp { padding: 4px 0; border-top: 1px solid var(--linha); display: flex; gap: 8px; align-items: flex-start }
      .em .em-disp .em-txt { flex: 1; font-size: 14px }
      .em form.em-form { display: grid; gap: 8px; margin-top: 8px }
      @media (max-width: 640px) { .em table.em-lado td, .em table.em-lado th { font-size: 12px } }`;
    document.head.append(st);
  }

  /* ---------- desenho ---------- */
  function formulario() {
    const pres = dados.pode.presidente;
    const parls = temPL && PL.estado ? PL.estado.parls : [];
    return `<details class="cartao neutro" style="padding:10px" id="emNova"><summary style="cursor:pointer;font-weight:700">+ Nova emenda</summary>
      <form class="em-form" data-em-form>
        <div class="grupo">
          <label class="rot" style="flex:1;min-width:150px">Tipo<select class="campo" name="tipo">
            ${Object.entries(dados.tipos).map(([k, v]) => `<option value="${esc(k)}">${esc(v)}</option>`).join('')}</select></label>
          <label class="rot" style="flex:3;min-width:200px">Dispositivo<select class="campo" name="alvo">
            <option value="">— texto integral (substitutivo) ou fim do texto (aditiva)</option>
            ${dados.dispositivos.map(d => `<option value="${esc(d.id)}">${esc(d.ref)} — ${esc(d.texto.slice(0, 60))}</option>`).join('')}</select></label>
        </div>
        <label class="rot" data-em-agl hidden>Números das emendas aglutinadas (ex.: 2, 5)<input class="campo" name="aglutina" maxlength="80"></label>
        <label class="rot" data-em-texto>Texto novo (com o rótulo para dispositivos novos: “§ 3º …”, “IV – …”, “Art. 2º-A …”)
          <textarea class="campo" name="texto" rows="4" maxlength="20000" style="font:inherit;line-height:1.5"></textarea></label>
        <label class="rot">Justificativa<textarea class="campo" name="justificativa" rows="3" maxlength="5000" required minlength="10" style="font:inherit"></textarea></label>
        ${pres ? `<label class="rot">Autoria<select class="campo" name="autorPid"><option value="">Mesa Diretora</option>
          ${parls.map(p => `<option value="${esc(p.id)}">${esc(p.nome)} (${esc(p.partido)})</option>`).join('')}</select></label>` : ''}
        <div class="grupo" style="justify-content:flex-end"><button class="botao principal">Apresentar emenda</button></div>
      </form></details>`;
  }

  function cartaoEmenda(e) {
    const pres = dados.pode.presidente, minha = dados.pode.pid && e.autorPid === dados.pode.pid;
    const r = e.resultado || {};
    const contagem = r.contagem ? ` (sim ${r.contagem.sim ?? 0}, não ${r.contagem.nao ?? 0}, abst. ${r.contagem.abst ?? 0})` : '';
    const botoes = [`<button class="botao pq" data-em-acao="comparar" data-id="${esc(e.id)}">Comparar</button>`];
    if (pres && e.situacao === 'apresentada') {
      botoes.push(`<button class="botao pq" data-em-acao="pautar" data-id="${esc(e.id)}" title="Inclui a emenda na pauta para votação">Pautar votação</button>`,
        `<button class="botao pq" data-em-acao="aprovada" data-id="${esc(e.id)}">Aprovada</button>`,
        `<button class="botao pq" data-em-acao="rejeitada" data-id="${esc(e.id)}">Rejeitada</button>`,
        `<button class="botao pq" data-em-acao="prejudicada" data-id="${esc(e.id)}">Prejudicada</button>`);
    }
    if (pres && ['aprovada', 'rejeitada', 'prejudicada'].includes(e.situacao))
      botoes.push(`<button class="botao pq fantasma" data-em-acao="apresentada" data-id="${esc(e.id)}">Desfazer resultado</button>`);
    if (e.situacao === 'apresentada' && (pres || minha))
      botoes.push(`<button class="botao pq fantasma" data-em-acao="retirar" data-id="${esc(e.id)}">Retirar</button>`);
    return `<div class="em-cartao ${esc(e.situacao)}">
      <div class="grupo" style="justify-content:space-between;align-items:center"><b>${esc(e.titulo)}</b>${chip(e.situacao)}</div>
      <div class="suave" style="font-size:13px">${esc(dados.tipos[e.tipo] || e.tipo)} · ${esc(e.alvoRef)} · ${esc(e.autor)} · ${data(e.criado)}
        ${e.aglutina?.length ? ' · aglutina as emendas nº ' + e.aglutina.map(Number).join(', ') : ''}
        ${e.resultado && e.situacao !== 'apresentada' ? ' · ' + esc(r.votacao ? 'votação ' + r.votacao + contagem : r.motivo || r.nota || r.por || '') : ''}</div>
      ${e.texto ? `<div class="em-txt" style="margin:6px 0">${esc(e.texto)}</div>` : '<div class="suave" style="margin:6px 0">Suprime o dispositivo.</div>'}
      <details><summary class="suave" style="cursor:pointer;font-size:13px">Justificativa</summary><div class="em-txt">${esc(e.justificativa)}</div></details>
      <div class="grupo" style="margin-top:6px">${botoes.join('')}</div></div>`;
  }

  function comparativoHtml() {
    if (!comp) return '';
    const linhas = soMudancas ? comp.linhas.filter(l => l.estado !== 'igual') : comp.linhas;
    const r = comp.resumo;
    const cab = `<div class="suave" style="font-size:13px;margin:6px 0">${r.alterado} alterado(s) · ${r.acrescido} acrescido(s) · ${r.suprimido} suprimido(s) · ${r.renumerado} renumerado(s)</div>
      ${comp.avisos.length ? `<ul class="detalhe" style="margin:4px 0">${comp.avisos.map(a => `<li>${esc(a)}</li>`).join('')}</ul>` : ''}`;
    if (!linhas.length) return cab + '<p class="vazio">Nenhuma mudança no texto.</p>';
    if (modo === 'lado')
      return cab + `<table class="em-lado"><tr><th>Texto original</th><th>${compAlvo ? 'Com a emenda' : 'Texto final'}</th></tr>
        ${linhas.map(l => `<tr class="em-linha ${esc(l.estado)}"><td>${opsHtml(l.ops, 'antes')}</td><td>${opsHtml(l.ops, 'depois')}</td></tr>`).join('')}</table>`;
    return cab + linhas.map(l => `<div class="em-linha ${esc(l.estado)}">${opsHtml(l.ops)}</div>`).join('');
  }

  function desenhar() {
    if (!caixa) return;
    if (!dados) { caixa.innerHTML = ''; return; }
    const p = dados.projeto, base = encodeURIComponent(p.id);
    const aprovado = p.situacao === 'aprovada';
    caixa.innerHTML = `<div class="em">
      <div class="grupo" style="align-items:center;margin:8px 0"><b>${esc(p.numero)}</b><span class="suave" style="flex:1;min-width:160px">${esc(p.ementa)}</span></div>
      <div class="grupo" style="font-size:14px">
        ${papel ? `<a class="botao pq fantasma" href="/emendas?p=${base}" target="_blank" rel="noopener">Página pública</a>` : ''}
        <a class="botao pq fantasma" href="/redacao-final/${base}" target="_blank" rel="noopener">${aprovado ? 'Autógrafo' : 'Texto consolidado'} (HTML)</a>
        <a class="botao pq fantasma" href="/redacao-final/${base}.pdf" target="_blank" rel="noopener">PDF</a>
        <a class="botao pq fantasma" href="/dados/akn/projeto/${base}/redacao-final.xml" target="_blank" rel="noopener">Akoma Ntoso</a></div>
      ${dados.pode.emendar ? formulario() : ''}
      <h3 style="font-size:15px;margin:14px 0 0">Emendas (${dados.emendas.length})</h3>
      ${dados.emendas.length ? dados.emendas.map(cartaoEmenda).join('') : '<p class="vazio">Nenhuma emenda apresentada.</p>'}
      <h3 style="font-size:15px;margin:14px 0 4px">Comparativo</h3>
      <div class="grupo" style="align-items:center">
        <button class="botao pq ${compAlvo === '' ? 'principal' : ''}" data-em-acao="comparar" data-id="">Original × texto final (emendas aprovadas)</button>
        <select class="campo" style="width:auto" data-em-modo><option value="lado" ${modo === 'lado' ? 'selected' : ''}>Lado a lado</option><option value="inline" ${modo === 'inline' ? 'selected' : ''}>No texto</option></select>
        <label class="linha" style="display:inline-flex;gap:6px;font-size:13px"><input type="checkbox" data-em-mudancas ${soMudancas ? 'checked' : ''}> só o que muda</label></div>
      ${compAlvo ? `<div class="detalhe">Comparativo da ${esc((dados.emendas.find(e => e.id === compAlvo) || {}).titulo || 'emenda')} com o texto original.</div>` : ''}
      <div data-em-comp>${comparativoHtml()}</div>
      <details style="margin-top:12px" ${papel ? '' : 'open'}><summary style="cursor:pointer;font-weight:700">${aprovado ? 'Texto final aprovado' : 'Texto com as emendas aprovadas até agora'}</summary>
        ${dados.final.avisos.length ? `<ul class="detalhe">${dados.final.avisos.map(a => `<li>${esc(a)}</li>`).join('')}</ul>` : ''}
        <div class="em-txt" style="margin-top:8px">${esc(dados.final.texto)}</div></details>
      <details style="margin-top:12px"><summary style="cursor:pointer;font-weight:700">Texto original por dispositivo (${dados.dispositivos.length})</summary>
        ${dados.dispositivos.map(d => `<div class="em-disp" style="padding-left:${Math.max(0, d.nivel - 1) * 14}px"><div class="em-txt">${esc(d.linha)}</div>
          ${dados.pode.emendar ? `<button class="botao pq fantasma" data-em-acao="emendar" data-id="${esc(d.id)}" title="${esc(d.ref)}">Emendar</button>` : ''}</div>`).join('')}</details>
    </div>`;
  }

  async function abrir(pid) {
    atual = pid || '';
    comp = null; compAlvo = null;
    if (!atual) { dados = null; return desenhar(); }
    try { dados = await get('/api/emendas/' + encodeURIComponent(atual)); } catch (e) { dados = null; desenhar(); return avisar(e.message, true); }
    desenhar();
    if (!papel) await comparar('');
  }
  async function recarregar() {
    if (!atual) return;
    const form = caixa?.querySelector('[data-em-form]');
    if (form && form.contains(document.activeElement)) return;  // não apaga o que está sendo digitado
    const alvo = compAlvo;
    try { dados = await get('/api/emendas/' + encodeURIComponent(atual)); } catch { return; }
    desenhar();
    if (alvo !== null) await comparar(alvo);
  }
  async function comparar(eid) {
    try { comp = await get(`/api/emendas/${encodeURIComponent(atual)}/comparativo` + (eid ? '?emenda=' + encodeURIComponent(eid) : '')); }
    catch (e) { return avisar(e.message, true); }
    compAlvo = eid;
    desenhar();
    if (eid) caixa.querySelector('[data-em-comp]')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  async function apresentar(form) {
    const f = new FormData(form);
    const args = { projeto: atual, tipo: f.get('tipo'), alvo: f.get('alvo'), texto: f.get('texto') || '', justificativa: f.get('justificativa') || '' };
    if (f.get('autorPid')) args.autorPid = f.get('autorPid');
    if (args.tipo === 'aglutinativa') args.aglutina = String(f.get('aglutina') || '').split(/[^0-9]+/).filter(Boolean).map(Number);
    try { await acao('emendaPropor', args); } catch { return; }
    avisar('Emenda apresentada.');
    document.activeElement?.blur?.();
    await recarregar();
  }

  function ajustarForm(form, preencher) {
    const tipo = form.tipo.value;
    form.querySelector('[data-em-texto]').hidden = tipo === 'supressiva';
    form.querySelector('[data-em-agl]').hidden = tipo !== 'aglutinativa';
    const d = dados.dispositivos.find(x => x.id === form.alvo.value);
    if (preencher && d && ['modificativa', 'substitutiva', 'aglutinativa'].includes(tipo) && (!form.texto.value || form.texto.dataset.auto === '1')) {
      form.texto.value = tipo === 'modificativa' ? d.texto : d.linha;
      form.texto.dataset.auto = '1';
    }
  }

  function ligar(raiz) {
    raiz.addEventListener('click', async ev => {
      const b = ev.target.closest('[data-em-acao]');
      if (!b || !raiz.contains(b)) return;
      const id = b.dataset.id, a = b.dataset.emAcao;
      if (a === 'comparar') return comparar(id);
      if (a === 'emendar') {
        const det = raiz.querySelector('#emNova'), form = raiz.querySelector('[data-em-form]');
        if (!form) return;
        det.open = true;
        form.alvo.value = id;
        if (form.tipo.value === 'supressiva') form.tipo.value = 'modificativa';
        ajustarForm(form, true);
        det.scrollIntoView({ behavior: 'smooth', block: 'start' });
        return form.texto.focus();
      }
      try {
        if (a === 'retirar') { if (!confirm('Retirar esta emenda? A numeração dela não é reaproveitada.')) return; await acao('emendaRetirar', { id }); }
        else if (a === 'pautar') { await acao('emendaPautar', { id }); avisar('Emenda incluída na pauta: abra a votação na aba da sessão.'); }
        else await acao('emendaResultado', { id, situacao: a });
      } catch { return; }
      recarregar();
    });
    raiz.addEventListener('change', ev => {
      if (ev.target.matches('[data-em-modo]')) { modo = ev.target.value; desenhar(); }
      else if (ev.target.matches('[data-em-mudancas]')) { soMudancas = ev.target.checked; desenhar(); }
      else if (ev.target.closest('[data-em-form]') && ['tipo', 'alvo'].includes(ev.target.name)) ajustarForm(ev.target.form, true);
      else if (ev.target.name === 'texto') ev.target.dataset.auto = '';
    });
    raiz.addEventListener('input', ev => { if (ev.target.name === 'texto') ev.target.dataset.auto = ''; });
    raiz.addEventListener('submit', ev => {
      if (!ev.target.matches('[data-em-form]')) return;
      ev.preventDefault();
      apresentar(ev.target);
    });
  }

  async function listaProjetos(sel) {
    let lista = [];
    try { lista = (await get('/api/emendas')).projetos; } catch { return []; }
    if (sel) {
      const antes = sel.value || atual;
      sel.innerHTML = '<option value="">Escolha um projeto com texto cadastrado…</option>' + lista.map(p => {
        const c = p.emendas, n = Object.values(c).reduce((a, b) => a + b, 0);
        return `<option value="${esc(p.id)}">${esc(p.numero)} — ${esc(p.ementa.slice(0, 70))}${n ? ` (${n} emenda${n > 1 ? 's' : ''})` : ''}</option>`;
      }).join('');
      sel.value = [...sel.options].some(o => o.value === antes) ? antes : '';
    }
    return lista;
  }

  /* ---------- montagem em cada tela ---------- */
  function montarPainel(onde, comoDetalhes) {
    const el = document.createElement(comoDetalhes ? 'details' : 'div');
    el.className = 'cartao neutro';
    el.id = 'emCartao';
    el.style.marginTop = '16px';
    el.innerHTML = `${comoDetalhes ? '<summary style="cursor:pointer;font-weight:700">✍️ Emendas aos projetos</summary>' : '<h2>Emendas e redação final</h2>'}
      <p class="detalhe" style="margin-top:6px">Emendas por dispositivo (supressiva, substitutiva, modificativa, aditiva, aglutinativa), comparativo com o texto original e redação final com as emendas aprovadas.</p>
      <select class="campo" id="emProjeto" aria-label="Projeto"></select><div id="emCorpo"></div>`;
    onde(el);
    caixa = el.querySelector('#emCorpo');
    ligar(caixa);
    const sel = el.querySelector('#emProjeto');
    sel.addEventListener('change', () => abrir(sel.value));
    const carregar = () => listaProjetos(sel);
    if (comoDetalhes) el.addEventListener('toggle', () => { if (el.open) carregar(); });
    else carregar();
    PL.aoMudar(S => {
      if (S.projetosRev === rev) return;
      const primeira = rev === null;
      rev = S.projetosRev;
      if (primeira) return;
      if (!comoDetalhes || el.open) { carregar(); recarregar(); }
    });
  }

  function montarPublico(raiz) {
    caixa = document.createElement('div');
    const lista = document.createElement('div');
    raiz.append(lista, caixa);
    ligar(caixa);
    const pid = new URLSearchParams(location.search).get('p') || '';
    listaProjetos(null).then(projetos => {
      lista.innerHTML = projetos.length ? `<div class="em-lista">${projetos.map(p => {
        const c = p.emendas, n = Object.values(c).reduce((a, b) => a + b, 0);
        return `<a href="?p=${encodeURIComponent(p.id)}" class="${p.id === pid ? 'ativa' : ''}"><b>${esc(p.numero)}</b> — ${esc(p.ementa)}
          <div class="suave" style="font-size:13px">${n} emenda(s): ${c.aprovada} aprovada(s), ${c.rejeitada} rejeitada(s), ${c.apresentada} aguardando</div></a>`;
      }).join('')}</div>` : '<p class="vazio">Nenhum projeto com texto publicado ainda.</p>';
    });
    if (pid) abrir(pid);
  }

  if (papel === 'presidente') montarPainel(el => document.getElementById('aba-projetos').append(el), false);
  else if (papel === 'parlamentar') montarPainel(el => { const r = document.getElementById('cartaoRegimento'); r.parentNode.insertBefore(el, r); }, true);
  else if (document.getElementById('emendasPublico')) montarPublico(document.getElementById('emendasPublico'));

  return { abrir, comparar, opsHtml };
})();
