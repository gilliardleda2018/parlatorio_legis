/* Painel do presidente — recursos de 2026: rascunho da ata por IA, vídeo com marcação,
   texto do projeto, consulta pública com devolutiva e anexos das matérias. */

/* ---------------- rascunho da ata por IA ---------------- */
let rsDados = null;
async function carregarResumo() {
  const sel = $('rsSessao');
  if (!sel.options.length || sel.dataset.rev !== String(PL.estado?.rev ? Math.floor(PL.estado.rev / 50) : 0)) {
    let sessoes = [];
    try { sessoes = (await (await apiGet('/api/sessoes')).json()).sessoes || []; } catch { }
    const atual = sel.value || 'atual';
    sel.innerHTML = '<option value="atual">Sessão em andamento</option>' +
      sessoes.slice(0, 40).map(s => `<option value="${s.id}">${esc(s.titulo)} — ${new Date(s.inicio).toLocaleDateString('pt-BR')}</option>`).join('');
    sel.value = [...sel.options].some(o => o.value === atual) ? atual : 'atual';
    sel.dataset.rev = String(PL.estado?.rev ? Math.floor(PL.estado.rev / 50) : 0);
  }
  try { rsDados = await (await apiGet('/api/resumo/' + sel.value)).json(); } catch (e) { return PL.avisar(e.message, true); }
  desenharResumo();
  carregarVideo();
}
function desenharResumo() {
  if (!rsDados) return;
  const job = PL.estado.resumoJob, rodando = job && !job.fim && job.sessao === $('rsSessao').value;
  const res = rsDados.resumo;
  const semIa = (PL.estado.iaMotor || '').startsWith('desligada');
  $('rsCorpo').innerHTML = `
    <p class="detalhe" style="margin-top:0">Um parágrafo por matéria (e um para o expediente), redigido a partir das transcrições, das falas registradas e das votações.
      ${semIa ? 'Sem IA configurada no servidor: o rascunho lista quem falou, por quanto tempo e o resultado.' : `Motor: <b>${esc(PL.estado.iaMotor)}</b>.`}
      É sempre rascunho: revise e marque cada item como revisado; a ata em PDF indica o que ainda não foi revisado.</p>
    <div class="grupo" style="margin-bottom:10px">
      <button class="botao principal" ${rodando ? 'disabled' : ''} onclick="gerarResumo()">${res ? 'Gerar de novo' : 'Gerar rascunho'}</button>
      ${rodando ? `<span class="chip ambar">gerando… ${job.feitos || 0}/${job.total || '?'}</span>` : ''}
      ${job?.erro && job.sessao === $('rsSessao').value ? `<span class="n-fim">${esc(job.erro)}</span>` : ''}
      <a class="botao fantasma" href="#" onclick="event.preventDefault();abrirAta('${$('rsSessao').value}')">Abrir ata em PDF</a>
    </div>
    ${res ? res.itens.map(i => `<div class="cartao neutro" style="margin-bottom:10px" data-chave="${esc(i.chave)}">
        <div class="grupo" style="justify-content:space-between;align-items:center"><b>${esc(i.titulo)}</b>
          <span class="chip ${i.revisado ? 'sim' : 'ambar'}">${i.revisado ? 'revisado' : 'rascunho'}</span></div>
        <textarea class="campo" rows="5" style="margin-top:8px;font:inherit;line-height:1.5">${esc(i.texto)}</textarea>
        <div class="grupo" style="justify-content:space-between;margin-top:6px"><small class="suave">${esc(i.motor || '')}</small>
          <span><label class="linha" style="display:inline-flex">Revisado <input type="checkbox" ${i.revisado ? 'checked' : ''}></label>
          <button class="botao pq" onclick="salvarItemResumo(this)">Salvar</button></span></div></div>`).join('')
      : '<p class="vazio">Nenhum rascunho ainda para esta sessão.</p>'}`;
}
async function gerarResumo() {
  if (rsDados?.resumo && !confirm('Gerar de novo? Os textos atuais, inclusive revisões, serão substituídos.')) return;
  await PL.acao('presidente', 'ataResumoGerar', { sessao: $('rsSessao').value }).catch(() => {});
}
async function salvarItemResumo(botao) {
  const caixa = botao.closest('[data-chave]');
  await PL.acao('presidente', 'ataResumoEditar', { sessao: $('rsSessao').value, chave: caixa.dataset.chave,
    texto: caixa.querySelector('textarea').value, revisado: caixa.querySelector('input[type=checkbox]').checked })
    .then(() => { PL.avisar('Item salvo.'); carregarResumo(); }).catch(() => {});
}
let rsUltimoFim = null;
PL.aoMudar(S => {
  if (abaAtual !== 'relatorios') return;
  const job = S.resumoJob;
  if (job && job.fim && job.fim !== rsUltimoFim) { rsUltimoFim = job.fim; carregarResumo(); }
  else if (job && !job.fim) desenharResumo();
});
document.querySelector('[data-aba="relatorios"]')?.addEventListener('click', () => setTimeout(carregarResumo, 50));

/* ---------------- vídeo da sessão ---------------- */
async function carregarVideo() {
  const sid = $('rsSessao').value;
  let r = null;
  try { r = await (await fetch('/api/sessao-publica/' + sid)).json(); } catch { }
  const v = r?.video;
  $('vdUrl').value = v?.url || '';
  $('vdHora').value = v?.inicio ? new Date(v.inicio).toTimeString().slice(0, 8) : '';
  $('vdLink').innerHTML = sid !== 'atual' ? `Página pública: <a href="/sessao/${sid}" target="_blank" rel="noopener">/sessao/${sid}</a>`
    : 'A página pública fica disponível quando a sessão for encerrada e arquivada (o vídeo informado aqui vai junto).';
  $('vdLink').dataset.inicio = r?.inicio || '';
}
async function salvarVideo() {
  const sid = $('rsSessao').value, url = $('vdUrl').value.trim(), hora = $('vdHora').value;
  let inicio = null;
  if (url && hora) {
    const base = new Date(Number($('vdLink').dataset.inicio) || Date.now());
    const [h, m, s] = hora.split(':').map(Number);
    base.setHours(h, m, s || 0, 0);
    inicio = base.getTime();
  }
  if (url && !inicio) return PL.avisar('Informe o horário em que o vídeo começou, para marcar cada fala.', true);
  await PL.acao('presidente', 'sessaoVideo', { sessao: sid, url, inicio }).then(() => PL.avisar('Vídeo salvo.')).catch(() => {});
}

/* ---------------- texto do projeto, consulta pública, anexo ---------------- */
async function gerirConsulta(id) {
  let d;
  try { d = await (await apiGet('/api/projeto/' + id)).json(); } catch (e) { return PL.avisar(e.message, true); }
  const p = d.projeto, c = p.consulta || {};
  const prazo = c.prazo ? new Date(c.prazo).toISOString().slice(0, 10) : '';
  const porArtigo = {};
  for (const x of d.comentarios) (porArtigo[x.artigo] ||= []).push(x);
  const rot = Object.fromEntries(d.artigos.map(a => [a.chave, a.rotulo]));
  dlg.classList.add('largo');
  dialogo(`${p.numero} — texto e consulta pública`, `
    <p class="detalhe" style="margin:0">${esc(p.ementa)}</p>
    <label class="rot">Texto integral do projeto (cada artigo começando com “Art. 1º”, “Art. 2º”…)
      <textarea class="campo" id="cpTexto" rows="10" style="font:inherit;line-height:1.5">${esc(p.texto || '')}</textarea></label>
    <div class="grupo" style="justify-content:space-between"><small class="suave">${d.artigos.length} dispositivo(s) reconhecido(s)</small>
      <button class="botao pq" onclick="salvarTextoProjeto('${id}')">Salvar texto</button></div>
    <div class="cartao neutro" style="padding:12px">
      <b>Consulta pública</b> <span class="chip ${d.aberta ? 'sim' : ''}">${d.aberta ? 'aberta' : c.desde ? 'encerrada' : 'não aberta'}</span>
      <div class="grupo" style="margin-top:8px;align-items:end">
        <label class="rot">Aberta até<input class="campo" type="date" id="cpPrazo" value="${prazo}"></label>
        <button class="botao pq ${d.aberta ? '' : 'principal'}" onclick="abrirConsulta('${id}', ${!d.aberta})">${d.aberta ? 'Encerrar consulta' : 'Abrir consulta'}</button>
        ${c.desde ? `<a class="botao pq fantasma" href="/participe#${id}" target="_blank" rel="noopener">Ver página pública</a>` : ''}
      </div></div>
    <div class="cartao neutro" style="padding:12px"><b>Anexo (PDF, aparece no celular dos vereadores e na consulta)</b>
      <div class="grupo" style="margin-top:8px;align-items:center">${p.anexo ? `<a href="/anexo/${id}.pdf?v=${p.anexo.v}" target="_blank" rel="noopener">${esc(p.anexo.nome)}</a>
        <button class="botao pq fantasma" onclick="enviarAnexo('${id}', null)">Remover</button>` : '<span class="suave">Nenhum anexo.</span>'}
        <input type="file" accept="application/pdf" onchange="enviarAnexo('${id}', this.files[0])"></div></div>
    <h3 style="font-size:15px;margin:6px 0 0">Contribuições (${d.comentarios.length})</h3>
    ${d.comentarios.length ? Object.entries(porArtigo).map(([art, lista]) => `<div><b style="font-size:13px">${esc(rot[art] || 'Comentário geral')}</b>
      ${lista.map(x => `<div class="cartao neutro" style="padding:10px;margin-top:6px;${x.oculto ? 'opacity:.5' : ''}">
        <div style="font-size:13px"><b>${esc(x.nome)}</b>${x.bairro ? ' · ' + esc(x.bairro) : ''} · ${new Date(x.criado).toLocaleDateString('pt-BR')} · ${x.apoios} apoio(s)${x.oculto ? ' · <b>oculto</b>' : ''}</div>
        <div style="white-space:pre-wrap;margin:4px 0">${esc(x.texto)}</div>
        ${x.resposta ? `<div class="detalhe">Resposta (${esc(d.situacoes[x.resposta.situacao])}, ${esc(x.resposta.autor)}): ${esc(x.resposta.texto)}</div>` : ''}
        <div class="grupo" style="margin-top:6px">
          <select class="campo" style="width:auto">${Object.entries(d.situacoes).map(([k, v]) => `<option value="${k}" ${x.resposta?.situacao === k ? 'selected' : ''}>${v}</option>`).join('')}</select>
          <input class="campo" placeholder="Resposta ao cidadão" style="flex:1;min-width:180px" value="${esc(x.resposta?.texto || '')}">
          <button class="botao pq" onclick="responderContribuicao(this,'${x.id}','${id}')">Responder</button>
          <button class="botao pq fantasma" onclick="ocultarContribuicao('${x.id}', ${!x.oculto}, '${id}')">${x.oculto ? 'Mostrar' : 'Ocultar'}</button></div>
      </div>`).join('')}</div>`).join('') : '<p class="vazio">Nenhuma contribuição ainda.</p>'}`,
    () => {}, 'Fechar');
  dlg.addEventListener('close', () => dlg.classList.remove('largo'), { once: true });
}
async function salvarTextoProjeto(id) {
  await PL.acao('presidente', 'projetoTexto', { id, texto: $('cpTexto').value }).then(() => { PL.avisar('Texto salvo.'); dlg.close(); gerirConsulta(id); }).catch(() => {});
}
async function abrirConsulta(id, aberta) {
  const prazo = $('cpPrazo').value ? new Date($('cpPrazo').value + 'T23:59:59').getTime() : null;
  await PL.acao('presidente', 'consultaAbrir', { id, aberta, prazo }).then(() => { dlg.close(); gerirConsulta(id); }).catch(() => {});
}
async function enviarAnexo(id, arquivo) {
  let dataUrl = '', nome = '';
  if (arquivo) {
    if (arquivo.size > 2_800_000) return PL.avisar('PDF maior que 2,8 MB.', true);
    dataUrl = await new Promise(ok => { const r = new FileReader(); r.onload = () => ok(r.result); r.readAsDataURL(arquivo); });
    nome = arquivo.name;
  }
  await PL.acao('presidente', 'materiaAnexo', { id, nome, dataUrl }).then(() => { dlg.close(); gerirConsulta(id); }).catch(() => {});
}
async function responderContribuicao(botao, comentario, id) {
  const linha = botao.parentElement;
  await PL.acao('presidente', 'consultaResponder', { comentario, situacao: linha.querySelector('select').value,
    texto: linha.querySelector('input').value }).then(() => { PL.avisar('Resposta publicada.'); dlg.close(); gerirConsulta(id); }).catch(() => {});
}
async function ocultarContribuicao(comentario, oculto, id) {
  await PL.acao('presidente', 'consultaOcultar', { comentario, oculto }).then(() => { dlg.close(); gerirConsulta(id); }).catch(() => {});
}

/* ---------------- rádio da Câmara (programação) ---------------- */
async function carregarRadio() {
  if (!PL.estado?.radioLocal) return;
  $('cartaoRadio').classList.remove('oculto');
  let d;
  try { d = await (await apiGet('/api/radio')).json(); } catch (e) { return PL.avisar(e.message, true); }
  $('rdEstado').innerHTML = `<span class="chip sim">${d.ouvintes} ouvinte(s) agora</span>`;
  const mb = b => (b / 1048576).toFixed(1).replace('.', ',') + ' MB';
  $('rdLista').innerHTML = d.arquivos.length ? '<table class="tab">' + d.arquivos.map(a => `<tr><td>${esc(a.nome)}</td><td class="tnum suave">${mb(a.bytes)}</td>
      <td style="text-align:right"><button class="botao pq fantasma" onclick="removerProgramacao('${esc(a.nome).replace(/'/g, "\\'")}')">Remover</button></td></tr>`).join('') + '</table>'
    : '<p class="vazio">Nenhum áudio na programação: fora das sessões a rádio fica em silêncio.</p>';
}
async function enviarProgramacao(arquivos) {
  for (const arq of arquivos) {
    PL.avisar(`Enviando ${arq.name}…`);
    const r = await fetch('/api/radio/arquivo?nome=' + encodeURIComponent(arq.name), { method: 'POST',
      headers: { Authorization: 'Bearer ' + PL.token('presidente'), 'Content-Type': 'application/octet-stream' }, body: arq });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) PL.avisar(`${arq.name}: ${j.erro || 'falhou'}`, true);
  }
  carregarRadio();
}
async function removerProgramacao(nome) {
  if (!confirm(`Tirar “${nome}” da programação?`)) return;
  await PL.acao('presidente', 'radioRemover', { nome }).then(carregarRadio).catch(() => {});
}
document.querySelector('[data-aba="config"]')?.addEventListener('click', () => setTimeout(carregarRadio, 100));


/* ---------------- primeira configuração: de qual cidade é esta Câmara ---------------- */
const UFS = { AC: 'Acre', AL: 'Alagoas', AP: 'Amapá', AM: 'Amazonas', BA: 'Bahia', CE: 'Ceará', DF: 'Distrito Federal',
  ES: 'Espírito Santo', GO: 'Goiás', MA: 'Maranhão', MT: 'Mato Grosso', MS: 'Mato Grosso do Sul', MG: 'Minas Gerais',
  PA: 'Pará', PB: 'Paraíba', PR: 'Paraná', PE: 'Pernambuco', PI: 'Piauí', RJ: 'Rio de Janeiro', RN: 'Rio Grande do Norte',
  RS: 'Rio Grande do Sul', RO: 'Rondônia', RR: 'Roraima', SC: 'Santa Catarina', SP: 'São Paulo', SE: 'Sergipe', TO: 'Tocantins' };
$('cidUf').insertAdjacentHTML('beforeend', Object.entries(UFS).map(([s, n]) => `<option value="${s}">${s} — ${esc(n)}</option>`).join(''));
const semAcento = t => t.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().trim();

PL.aoMudar(S => verificarCidade(S));
function verificarCidade(S) {
  if (!Cidade.pronto || Cidade.aberto || S.camaraConfigurada !== false || sessionStorage.getItem('legis.cidadeDepois')) return;
  if (document.querySelector('dialog[open]')) return;   // não cobre outra janela (ex.: troca de PIN)
  Cidade.aberto = true;
  $('dlgCidade').showModal();
  $('cidUf').focus();
}
$('cidUf').onchange = async () => {
  const uf = $('cidUf').value;
  $('cidMun').value = ''; $('cidLista').innerHTML = ''; Cidade.municipios = [];
  if (!uf) { $('cidMun').disabled = true; return; }
  $('cidMun').disabled = false; $('cidMun').focus();
  $('cidEstado').textContent = `Buscando os municípios de ${UFS[uf]} no IBGE…`;
  try {
    const lista = await (await fetch(`https://servicodados.ibge.gov.br/api/v1/localidades/estados/${uf}/municipios?orderBy=nome`)).json();
    if ($('cidUf').value !== uf) return;
    Cidade.municipios = lista.map(m => m.nome);
    $('cidLista').innerHTML = Cidade.municipios.map(n => `<option value="${esc(n)}">`).join('');
    $('cidEstado').textContent = `${Cidade.municipios.length} municípios. Digite para filtrar.`;
  } catch { $('cidEstado').textContent = 'Sem acesso à lista do IBGE: digite o nome do município.'; }
};
$('cidMun').onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); $('cidOk').click(); } };
$('cidDepois').onclick = () => { sessionStorage.setItem('legis.cidadeDepois', '1'); $('dlgCidade').close(); };
$('dlgCidade').addEventListener('cancel', () => sessionStorage.setItem('legis.cidadeDepois', '1'));
$('cidOk').onclick = async () => {
  const uf = $('cidUf').value, mun = $('cidMun').value.trim();
  if (!uf || !mun) { $('cidEstado').textContent = 'Escolha o estado e o município.'; return; }
  const certo = Cidade.municipios.find(n => semAcento(n) === semAcento(mun));
  if (Cidade.municipios.length && !certo) { $('cidEstado').textContent = `“${mun}” não está na lista do IBGE para ${uf}. Escolha um nome da lista.`; return; }
  $('cidOk').disabled = $('cidDepois').disabled = $('cidUf').disabled = $('cidMun').disabled = true;
  $('cidEstado').textContent = 'Buscando os vereadores no TSE e as fotos… pode levar até um minuto.';
  try {
    const r = await api('/api/cidade', { uf, municipio: certo || mun });
    const pop = r.populacao ? ` · ${r.populacao.toLocaleString('pt-BR')} habitantes (IBGE)` : '';
    $('cidCorpo').innerHTML = `<p style="margin:0;font-size:1.15em"><b>✔ ${esc(r.nome)}</b></p>
      <p class="detalhe" style="margin:0">${r.vereadores} vereadores eleitos em 2024, ${r.fotos} com foto${pop}.</p>
      <p class="detalhe" style="margin:0">O PIN inicial de cada vereador é <b>0000</b>, com troca obrigatória no primeiro acesso.
      Suplentes que assumiram depois e mudanças de partido se ajustam em <b>Plenário</b>.</p>`;
    $('cidRodape').innerHTML = '<button class="botao principal" id="cidFim">Começar</button>';
    $('cidFim').onclick = () => $('dlgCidade').close();
    $('cidFim').focus();
  } catch (e) {
    $('cidEstado').textContent = e.message;
    $('cidOk').disabled = $('cidDepois').disabled = $('cidUf').disabled = $('cidMun').disabled = false;
  }
};


/* ---------------- perguntas do público ao vivo (Evento Interativo) ---------------- */
let pqRev = -1;
async function carregarPerguntas() {
  const r = await (await apiGet('/api/perguntas')).json().catch(() => null);
  if (!r) return;
  const S = PL.estado, telao = S.telaoPergunta?.id;
  const ordem = { pendente: 0, aprovada: 1, respondida: 2, recusada: 3 };
  const lista = r.perguntas.sort((a, b) => ordem[a.situacao] - ordem[b.situacao] || a.criado - b.criado);
  $('pqLista').innerHTML = lista.length ? lista.map(p => `<div class="contrib" style="margin-top:8px;${p.situacao === 'recusada' ? 'opacity:.45' : ''}">
      <span class="chip ${p.situacao === 'pendente' ? 'ambar' : p.situacao === 'recusada' ? 'nao' : 'sim'}" style="float:right">${p.id === telao ? '📺 no telão' : p.situacao}</span>
      <b>${esc(p.nome)}</b>${p.bairro ? ' · ' + esc(p.bairro) : ''}<div style="margin:4px 0">${esc(p.texto)}</div>
      <div class="grupo">${p.situacao === 'pendente' ? `<button class="botao pq ok" onclick="A('perguntaModerar',{id:'${p.id}',situacao:'aprovada'})">Aprovar</button><button class="botao pq fantasma" onclick="A('perguntaModerar',{id:'${p.id}',situacao:'recusada'})">Recusar</button>` : ''}
        ${p.situacao === 'aprovada' || p.situacao === 'respondida' ? (p.id === telao ? `<button class="botao pq" onclick="A('perguntaTelao',{})">Tirar do telão</button>` : `<button class="botao pq principal" onclick="A('perguntaTelao',{id:'${p.id}'})">📺 Mostrar no telão</button>`)
          + (p.situacao === 'aprovada' ? `<button class="botao pq fantasma" onclick="A('perguntaModerar',{id:'${p.id}',situacao:'respondida'})">Marcar respondida</button>` : '') : ''}</div></div>`).join('')
    : '<p class="vazio" style="margin:6px 0 0">Nenhuma pergunta nesta sessão.</p>';
}
PL.aoMudar(S => {
  $('pqAbertas').checked = !!S.perguntasAbertas;
  $('pqEstado').innerHTML = S.perguntasPendentes ? `<span class="chip ambar">${S.perguntasPendentes} aguardando</span>` : S.perguntasAbertas ? '<span class="chip sim">recebendo</span>' : '';
  const chave = S.cidadaniaRev + ':' + (S.telaoPergunta?.id || '') + ':' + S.perguntasAbertas;
  if (chave !== pqRev) { pqRev = chave; carregarPerguntas(); if (abaAtual === 'projetos') carregarIdeias(); }
});

/* ---------------- Ideia Legislativa ---------------- */
async function carregarIdeias() {
  const r = await (await apiGet('/api/ideias')).json().catch(() => null);
  if (!r) return;
  $('idMetaChip').textContent = `meta atual: ${r.meta} apoios`;
  if (document.activeElement !== $('idMeta')) $('idMeta').value = PL.estado?.cfg?.ideiaMeta || 0;
  const peso = i => ({ meta: 0, aberta: 1, encerrada: 2, virou_projeto: 3, arquivada: 4 })[i.situacao] ?? 5;
  $('idLista').innerHTML = r.ideias.length ? r.ideias.sort((a, b) => peso(a) - peso(b) || b.apoios - a.apoios).map(i => `<div class="contrib" style="margin-top:8px;${i.oculta ? 'opacity:.45' : ''}">
      <span class="chip ${i.situacao === 'meta' ? 'ambar' : i.situacao === 'virou_projeto' ? 'sim' : ''}" style="float:right">${esc(r.situacoes[i.situacao] || i.situacao)}</span>
      <b>${esc(i.titulo)}</b> <span class="suave">· ${esc(i.nome)}${i.bairro ? ', ' + esc(i.bairro) : ''} · ${i.apoios}/${i.meta} apoios</span>
      <div style="white-space:pre-wrap;margin:4px 0;font-size:14px">${esc(i.texto)}</div>
      ${i.resposta ? `<div class="detalhe">Resposta: ${esc(i.resposta.texto)}</div>` : ''}
      <div class="grupo" style="margin-top:6px"><select class="campo" style="width:auto"><option value="virou_projeto">Virou projeto</option><option value="meta">Em análise</option><option value="arquivada">Arquivada</option><option value="aberta">Reabrir para apoios</option></select>
        <input class="campo" placeholder="Resposta pública ao cidadão" style="flex:1;min-width:180px"><button class="botao pq" onclick="responderIdeia(this,'${i.id}')">Responder</button>
        <button class="botao pq fantasma" onclick="A('ideiaOcultar',{id:'${i.id}',oculta:${!i.oculta}})">${i.oculta ? 'Mostrar' : 'Ocultar'}</button></div></div>`).join('')
    : '<p class="vazio">Nenhuma ideia enviada ainda.</p>';
}
function responderIdeia(botao, id) {
  const g = botao.parentElement;
  A('ideiaResponder', { id, situacao: g.querySelector('select').value, texto: g.querySelector('input').value });
}
document.querySelector('[data-aba="projetos"]').addEventListener('click', () => carregarIdeias());


/* ---------------- análise prévia da proposição (Ulysses) ---------------- */
let analiseAberta = null, analiseTimer = null;
const GRAV = { alto: ['nao', 'Atenção'], medio: ['ambar', 'Conferir'], baixo: ['', 'Observar'], info: ['', 'Informação'] };
async function verAnalise(id) {
  analiseAberta = id; clearTimeout(analiseTimer);
  let r; try { r = await (await apiGet(`/api/analise/${id}`)).json(); } catch (e) { return PL.avisar(e.message, true); }
  const a = r.analise;
  $('dlgTitulo').textContent = `Análise prévia — ${r.numero}`;
  $('dlgOk').textContent = a && !a.emAndamento ? (a.revisada ? 'Salvar resumo' : 'Marcar como revisada') : 'Gerar análise';
  $('dlgOk').onclick = () => {
    if (!a || a.emAndamento === undefined && !a.comissoes) return A('projetoAnalisar', { id }).then(() => setTimeout(() => verAnalise(id), 800));
    if (a.emAndamento) return;
    A('projetoAnaliseRevisar', { id, resumo: $('anResumo') ? $('anResumo').value : null }).then(() => { PL.avisar('Análise revisada: o resumo passa a aparecer nas enquetes públicas.'); verAnalise(id); });
  };
  $('dlgCorpo').innerHTML = `<p class="detalhe" style="margin:0"><b>${esc(r.numero)}</b> — ${esc(r.ementa)}</p>
    ${!r.temTexto ? '<p class="detalhe" style="margin:0">⚠ Sem o texto do projeto cadastrado, a análise usa só a ementa. Cadastre em “Texto e consulta”.</p>' : ''}
    ${!a ? `<p>Gera um rascunho com as comissões sugeridas, alertas jurídicos comuns (vício de iniciativa, despesa, renúncia de receita, competência da União) ${r.ia !== 'desligada' ? 'e um resumo em linguagem simples feito pela IA (' + esc(r.ia) + ')' : ''}. <b>É só apoio</b>: não substitui o parecer jurídico nem o da comissão.</p>`
      : a.emAndamento ? '<p class="detalhe">⏳ Analisando… (com IA pode levar até um minuto)</p>'
      : `<div><span class="chip ${a.revisada ? 'sim' : 'ambar'}">${a.revisada ? '✓ revisada por ' + esc(a.revisor) : 'rascunho — revise antes de usar'}</span> <span class="suave" style="font-size:12px">motor: ${esc(a.motor)}</span></div>
        ${a.aviso ? `<p class="detalhe">${esc(a.aviso)}</p>` : ''}
        <div><b>Comissões sugeridas</b><div class="tags" style="margin-top:4px">${a.comissoes.map(c => `<span class="tag">${esc(c)}</span>`).join('')}</div>
          <small class="suave">Ajuste conforme as comissões do seu Regimento Interno.</small></div>
        <div><b>Alertas</b>${a.alertas.length ? a.alertas.map(x => `<div class="contrib" style="margin-top:6px"><span class="chip ${GRAV[x.gravidade][0]}">${GRAV[x.gravidade][1]}</span> <b>${esc(x.titulo)}</b>
          <div style="font-size:14px;margin-top:4px">${esc(x.texto)}</div><small class="suave">Trecho: “${esc(x.trecho)}”</small></div>`).join('') : '<p class="detalhe">Nenhum alerta pelas regras automáticas.</p>'}</div>
        ${a.observacoes ? `<div><b>Pontos para a comissão conferir (IA)</b><ul style="margin:4px 0">${a.observacoes.map(o => `<li>${esc(o)}</li>`).join('')}</ul></div>` : ''}
        <label class="rot">Resumo em linguagem simples ${a.revisada ? '(público nas enquetes)' : '(revise e edite; só aparece ao público depois de revisado)'}
          <textarea class="campo" id="anResumo" rows="4" placeholder="Escreva um resumo simples para o cidadão">${esc(a.resumo || '')}</textarea></label>
        <div class="grupo"><button class="botao pq fantasma" onclick="A('projetoAnalisar',{id:'${id}'}).then(()=>setTimeout(()=>verAnalise('${id}'),800))">Gerar de novo</button></div>`}`;
  if (!dlg.open) dlg.showModal();
  if (a && a.emAndamento) analiseTimer = setTimeout(() => { if (dlg.open && analiseAberta === id) verAnalise(id); }, 3000);
}


/* ---------------- Regimento Interno e Lei Orgânica (Ulysses Chat) ---------------- */
async function perguntarRegimento(campo, saida) {
  const pergunta = $(campo).value.trim(); if (pergunta.length < 5) return;
  $(saida).innerHTML = '⏳ Procurando nos artigos…';
  try {
    const r = await api('/api/regimento/perguntar', { pergunta });
    $(saida).innerHTML = (r.resposta ? `<div style="white-space:pre-wrap;color:var(--texto);margin-bottom:6px">${esc(r.resposta)}</div>` : '')
      + (r.fontes || []).map((f, k) => `<details style="margin-top:4px"><summary>[${k + 1}] ${esc(f.norma)}, art. ${esc(f.artigo)}${f.caminho ? ' · ' + esc(f.caminho) : ''}</summary>
          <div style="white-space:pre-wrap;font-size:13px;margin:4px 0 0 12px">${esc(f.texto)}</div></details>`).join('')
      + (r.modo === 'trechos' ? '<small class="suave">Artigos mais próximos da pergunta (IA desligada: leia os artigos).</small>' : r.modo === 'ia' ? '<small class="suave">Resposta da IA a partir dos artigos acima: confira sempre o texto.</small>' : '');
  } catch (e) { $(saida).textContent = e.message; }
}
async function carregarNormas() {
  let r; try { r = await (await apiGet('/api/normas')).json(); } catch { return; }
  $('nmEstado').innerHTML = r.normas.length ? `<span class="chip sim">${r.normas.length} norma(s)</span>` : '<span class="chip">nenhuma</span>';
  if (!r.pdf) $('nmArquivo').accept = 'text/plain,.txt,.md';
  $('nmLista').innerHTML = r.normas.map(n => `<div class="linha" style="margin-top:6px"><span><b>${esc(n.nome)}</b> <span class="suave">· ${n.artigos} artigos</span></span>
    <button class="botao pq fantasma" onclick="if(confirm('Remover ${esc(n.nome)}?'))A('normaRemover',{id:'${n.id}'}).then(carregarNormas)">Remover</button></div>`).join('');
}
async function salvarNorma() {
  const f = $('nmArquivo').files[0], args = { nome: $('nmNome').value || 'Regimento Interno', texto: $('nmTexto').value };
  if (f) {
    if (f.size > 2_800_000) return PL.avisar('Arquivo grande demais (máximo 2,8 MB): cole o texto.', true);
    if (f.type === 'application/pdf' || f.name.toLowerCase().endsWith('.pdf')) args.dataUrl = await new Promise(ok => { const l = new FileReader(); l.onload = () => ok(l.result); l.readAsDataURL(f); });
    else args.texto = await f.text();
  }
  await PL.acao('presidente', 'normaSalvar', args).then(() => { PL.avisar('Norma cadastrada.'); $('nmTexto').value = ''; $('nmArquivo').value = ''; carregarNormas(); }).catch(() => {});
}
document.querySelector('[data-aba="config"]').addEventListener('click', () => carregarNormas());
