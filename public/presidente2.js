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
