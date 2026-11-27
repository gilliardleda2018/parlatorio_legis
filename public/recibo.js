/* App do parlamentar — recibo do voto nominal (conferível por qualquer pessoa em /verificar).
   Voto secreto: só "seu voto foi contado", nunca o valor. */
(() => {
  let ultimaChave = '', pedindo = false;
  function cartao() {
    let c = document.getElementById('reciboCartao');
    if (!c) {
      const alvo = document.getElementById('conteudoApp');
      if (!alvo) return null;
      c = document.createElement('div');
      c.id = 'reciboCartao';
      c.className = 'cartao neutro oculto';
      c.setAttribute('aria-live', 'polite');
      alvo.insertAdjacentElement('afterend', c);
    }
    return c;
  }
  async function atualizar(forcar = false) {
    const S = PL.estado, euAgora = typeof eu !== 'undefined' ? eu : null, c = cartao();
    if (!S || !euAgora || !c) return;
    const v = S.votacao;
    if (!v || v.tipo === 'simbolica' || !v.votantes.includes(euAgora.pid)) { c.classList.add('oculto'); ultimaChave = ''; return; }
    const chave = v.id + ':' + (v.votos?.[euAgora.pid] || '') + ':' + v.votantes.length;
    if ((chave === ultimaChave && !forcar) || pedindo) return;
    pedindo = true;
    try {
      const r = await fetch('/api/recibo?votacao=' + encodeURIComponent(v.id), { headers: { Authorization: 'Bearer ' + PL.token('parlamentar') } });
      const j = await r.json();
      if (!r.ok) return;
      ultimaChave = chave;
      c.innerHTML = j.recibo
        ? `<h2>Recibo do seu voto</h2>
           <p style="margin:0 0 6px">${PL.esc(j.numero || '')}: <b>${PL.esc(PL.VOTOS[j.valor] || j.valor)}</b></p>
           <p style="margin:0;font-size:22px;letter-spacing:2px" class="tnum"><b>${PL.esc(j.recibo)}</b></p>
           <p class="detalhe" style="margin:6px 0 0">Código da votação: <b>${PL.esc(j.votacao)}</b>. Anote ou fotografe: em <a href="/verificar?votacao=${encodeURIComponent(j.votacao)}" target="_blank" rel="noopener">/verificar</a>
             qualquer pessoa confere se o recibo corresponde a um voto registrado.${j.aberta ? ' Se mudar o voto, o recibo muda.' : ''}</p>`
        : `<h2>Voto ${j.tipo === 'secreta' ? 'secreto' : ''}</h2><p class="detalhe" style="margin:0">${PL.esc(j.mensagem || '')}</p>`;
      c.classList.remove('oculto');
    } catch { } finally { pedindo = false; }
  }
  PL.aoMudar(() => atualizar());
  if (typeof confirmarVoto === 'function') {   // depois de votar, busca o recibo na hora
    const original = confirmarVoto;
    window.confirmarVoto = function (...a) { original.apply(this, a); setTimeout(() => atualizar(true), 300); };
  }
})();
