/* Telão: placar das opiniões do cidadão (só as aprovadas) sobre o item em discussão, quando a presidência liga. */
(() => {
  const caixa = document.createElement('div');
  caixa.className = 'placar-cidadao oculto';
  caixa.setAttribute('aria-live', 'polite');
  const css = document.createElement('style');
  css.textContent = `.placar-cidadao{position:fixed;left:2vh;bottom:2vh;z-index:20;background:var(--cartao2);color:var(--texto);border-radius:1.4vh;
    padding:1.2vh 2vh;font-size:2.4vh;box-shadow:0 .6vh 2vh rgba(0,0,0,.35);display:flex;gap:2vh;align-items:center}
    .placar-cidadao.oculto{display:none}.placar-cidadao small{display:block;font-size:1.6vh;color:var(--suave)}.placar-cidadao b{font-size:3.2vh}`;
  document.head.append(css);
  const colocar = () => document.body.append(caixa);
  document.body ? colocar() : addEventListener('DOMContentLoaded', colocar);
  PL.aoMudar(S => {
    const pc = S.placarCidadao, m = S.materiaAtual, c = pc && m ? pc.itens?.[m] : null;
    const mostrar = !!(pc?.mostrar && c && !S.votacao);
    caixa.classList.toggle('oculto', !mostrar);
    if (mostrar) caixa.innerHTML = `<div><small>Opinião do cidadão</small>pela internet</div>
      <div>👍 <b>${Number(c.favor) || 0}</b> a favor</div><div>👎 <b>${Number(c.contra) || 0}</b> contra</div><div>✋ <b>${Number(c.neutro) || 0}</b> neutro</div>`;
  });
})();
