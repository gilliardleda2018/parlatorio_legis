/* Painel do presidente — integridade e governança da IA: selo e revisão do rascunho da ata por IA,
   política pública de uso de IA (/ia) e atalho para conferir a auditoria (/verificar). */

/* ---------------- selo do rascunho da ata ---------------- */
if (typeof desenharResumo === 'function') {
  const desenharResumoOriginal = desenharResumo;
  window.desenharResumo = function () {
    desenharResumoOriginal();
    const corpo = $('rsCorpo'), res = rsDados?.resumo;
    if (!corpo || !res?.itens?.length) return;
    const selo = rsDados.seloIA, revisado = !!res.revisao;
    corpo.insertAdjacentHTML('afterbegin', `<div class="cartao ${revisado ? 'neutro' : ''}" style="margin-bottom:10px;border-top-color:var(--${revisado ? 'sim' : 'ambar'})">
      <div class="grupo" style="justify-content:space-between;align-items:center">
        <span>${selo ? `<b>${esc(selo)}</b>` : 'Rascunho feito só com os registros (sem IA).'}</span>
        <span class="grupo">${revisado ? `<button class="botao pq fantasma" onclick="revisarAtaIA(false)">Voltar a rascunho</button>`
          : `<input class="campo" id="rsRevisor" placeholder="Revisado por (nome)" maxlength="80" style="width:200px">
             <button class="botao pq principal" onclick="revisarAtaIA(true)">Marcar rascunho como revisado</button>`}</span></div>
      <small class="suave">O selo sai na ata em PDF e o uso fica no registro público de IA (<a href="/ia" target="_blank" rel="noopener">/ia</a>).</small></div>`);
  };
}
async function revisarAtaIA(revisado) {
  const revisor = $('rsRevisor')?.value.trim() || '';
  if (revisado && !confirm('Confirmar que todo o rascunho foi lido e revisado por uma pessoa?')) return;
  await PL.acao('presidente', 'iaRevisarAta', { sessao: $('rsSessao').value, revisor, revisado })
    .then(() => { PL.avisar(revisado ? 'Rascunho marcado como revisado.' : 'Rascunho voltou a não revisado.'); carregarResumo(); }).catch(() => {});
}

/* ---------------- Configurações: política de uso de IA e auditoria ---------------- */
(() => {
  const aba = document.getElementById('aba-config');
  if (!aba) return;
  aba.insertAdjacentHTML('beforeend', `<div class="cartao neutro" style="margin-top:16px" id="cartaoPoliticaIA">
    <h2>Uso de IA e integridade</h2>
    <p class="detalhe" style="margin-top:0">A política abaixo aparece na página pública <a href="/ia" target="_blank" rel="noopener">/ia</a>, junto com as estatísticas do registro de uso.
      Deixe em branco para voltar ao texto padrão (baseado nas diretrizes da UIP e na Portaria 227/2025 da Câmara dos Deputados).
      Para conferir o registro de auditoria e os selos das atas, use <a href="/verificar" target="_blank" rel="noopener">/verificar</a>.</p>
    <textarea class="campo" id="politicaIA" rows="10" style="font:inherit;line-height:1.5"></textarea>
    <div class="grupo" style="margin-top:8px"><button class="botao principal" onclick="salvarPoliticaIA()">Salvar política</button>
      <span class="suave" id="politicaIAInfo"></span></div></div>`);
  fetch('/api/ia').then(r => r.json()).then(d => {
    $('politicaIA').value = d.politica;
    $('politicaIAInfo').textContent = d.padrao ? 'Usando o texto padrão.' : '';
  }).catch(() => {});
})();
async function salvarPoliticaIA() {
  await PL.acao('presidente', 'iaPoliticaSalvar', { texto: $('politicaIA').value })
    .then(() => { PL.avisar('Política de uso de IA salva.'); $('politicaIAInfo').textContent = ''; }).catch(() => {});
}
