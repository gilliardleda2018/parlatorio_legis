/* Assistente do cidadão: "Pergunte à Câmara". Responde com base nos registros públicos do LEGIS
   (projetos, sessões, discursos publicados, parlamentares, contatos) e mostra as fontes. */
(() => {
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const css = document.createElement('style');
  css.textContent = `.assist-bt{position:fixed;right:18px;bottom:18px;z-index:60;border:0;border-radius:999px;padding:12px 18px;font-weight:700;
      background:var(--destaque,#1f8a4c);color:#fff;box-shadow:0 6px 20px rgba(0,0,0,.35);cursor:pointer;font-size:15px}
    .assist{position:fixed;right:18px;bottom:76px;z-index:60;width:min(390px,calc(100vw - 24px));max-height:min(560px,75vh);display:flex;flex-direction:column;
      background:var(--cartao,#111b2e);color:var(--texto,#e6edf6);border-radius:14px;box-shadow:0 10px 32px rgba(0,0,0,.45);border-top:4px solid var(--destaque,#1f8a4c)}
    .assist[hidden]{display:none}.assist header{padding:12px 14px;font-weight:700;display:flex;justify-content:space-between;align-items:center}
    .assist .msgs{flex:1;overflow:auto;padding:0 14px;display:flex;flex-direction:column;gap:10px;font-size:14px;line-height:1.5}
    .assist .eu{align-self:flex-end;background:var(--cartao2,#1b2a44);padding:8px 10px;border-radius:10px;max-width:85%}
    .assist .ela{background:color-mix(in srgb,var(--destaque,#1f8a4c) 14%,transparent);padding:8px 10px;border-radius:10px}
    .assist .fontes{font-size:12px;margin-top:6px;color:var(--suave,#9fb0c6)}.assist .fontes a{color:inherit}
    .assist form{display:flex;gap:8px;padding:12px 14px}.assist input{flex:1;background:var(--fundo-fundo,#0a1426);border:1px solid var(--linha,#1f2d48);
      border-radius:8px;padding:9px 10px;color:inherit;font:inherit}.assist small.aviso{padding:0 14px 10px;color:var(--suave,#9fb0c6);font-size:11px}`;
  document.head.append(css);
  const bt = document.createElement('button');
  bt.className = 'assist-bt'; bt.type = 'button'; bt.textContent = '💬 Pergunte à Câmara';
  bt.setAttribute('aria-expanded', 'false');
  const caixa = document.createElement('section');
  caixa.className = 'assist'; caixa.hidden = true; caixa.setAttribute('aria-label', 'Assistente da Câmara');
  caixa.innerHTML = `<header>Pergunte à Câmara <button type="button" class="botao pq fantasma" aria-label="Fechar">✕</button></header>
    <div class="msgs" aria-live="polite"><div class="ela">Olá! Pergunte sobre projetos, sessões, votações, vereadores ou o que foi dito na tribuna.
      Respondo com base nos registros públicos da Câmara e mostro de onde tirei a informação.</div></div>
    <form><input name="q" placeholder="Ex.: o que foi aprovado sobre saúde?" maxlength="300" autocomplete="off" aria-label="Sua pergunta">
      <button class="botao principal">Enviar</button></form>
    <small class="aviso">Respostas automáticas podem conter erros: confira nas fontes indicadas.</small>`;
  const msgs = caixa.querySelector('.msgs'), historico = [];
  const abrir = () => { caixa.hidden = !caixa.hidden; bt.setAttribute('aria-expanded', String(!caixa.hidden)); if (!caixa.hidden) caixa.querySelector('input').focus(); };
  bt.onclick = abrir; caixa.querySelector('header button').onclick = abrir;
  caixa.querySelector('form').onsubmit = async ev => {
    ev.preventDefault();
    const campo = caixa.querySelector('input'), pergunta = campo.value.trim();
    if (pergunta.length < 4) return;
    campo.value = '';
    msgs.insertAdjacentHTML('beforeend', `<div class="eu">${esc(pergunta)}</div>`);
    const espera = document.createElement('div'); espera.className = 'ela'; espera.textContent = 'Procurando nos registros…';
    msgs.append(espera); msgs.scrollTop = msgs.scrollHeight;
    const r = await fetch('/api/perguntar', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pergunta, historico, escopo: 'tudo' }) }).then(x => x.json()).catch(() => ({ erro: 'Sem conexão.' }));
    if (r.erro) { espera.textContent = r.erro; return; }
    const fontes = (r.fontes || []).map((f, k) => `<a href="${esc(f.link || '#')}">[${k + 1}] ${esc(f.titulo)}</a>`).join(' · ');
    espera.innerHTML = r.resposta ? `${esc(r.resposta)}${fontes ? `<div class="fontes">Fontes: ${fontes}</div>` : ''}`
      : `Encontrei estes registros:<div class="fontes">${(r.fontes || []).slice(0, 4).map((f, k) => `<div style="margin-top:4px"><a href="${esc(f.link || '#')}">[${k + 1}] ${esc(f.titulo)}</a> — ${esc(f.texto.slice(0, 160))}…</div>`).join('')}</div>`;
    historico.push({ de: 'eu', texto: pergunta }, { de: 'assistente', texto: r.resposta || '' });
    msgs.scrollTop = msgs.scrollHeight;
  };
  addEventListener('DOMContentLoaded', () => document.body.append(bt, caixa));
  if (document.readyState !== 'loading') document.body.append(bt, caixa);
})();
