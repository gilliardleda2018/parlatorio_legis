/* Parlatório Legis — funções comuns às três telas (presidente, telão, parlamentar). */
const PL = (() => {
  let estado = null;
  let desvio = 0;            // relógio do servidor − relógio local (ms)
  let ultimoSinal = null;
  const ouvintes = [];
  const tokenChave = papel => 'parlatorio.token.' + papel;

  /* ----- conexão (SSE) ----- */
  function conectar() {
    const fonte = new EventSource('/eventos');
    const bolinha = () => document.querySelectorAll('.conexao');
    fonte.onopen = () => bolinha().forEach(b => { b.className = 'conexao on'; b.title = 'Conectado'; });
    fonte.onerror = () => bolinha().forEach(b => { b.className = 'conexao off'; b.title = 'Sem conexão — tentando novamente'; });
    fonte.onmessage = e => {
      const m = JSON.parse(e.data);
      desvio = m.agora - Date.now();
      if (m.tipo !== 'estado') return;
      const primeira = !estado;
      estado = m.estado;
      aplicarTema();
      if (!primeira && ultimoSinal !== null && estado.sinal.n !== ultimoSinal) PL.aoSinal?.(estado.sinal.tipo);
      ultimoSinal = estado.sinal.n;
      ouvintes.forEach(f => f(estado));
    };
  }
  const agora = () => Date.now() + desvio;

  /* ----- API ----- */
  async function entrar(papel, pin, pid) {
    const r = await fetch('/api/entrar', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ papel, pin, pid }) });
    const j = await r.json();
    if (!r.ok) throw new Error(j.erro);
    localStorage.setItem(tokenChave(papel), j.token);
    return j.ator;
  }
  async function quemSou(papel) {
    const t = localStorage.getItem(tokenChave(papel));
    if (!t) return null;
    const r = await fetch('/api/eu', { headers: { Authorization: 'Bearer ' + t } });
    const j = await r.json();
    return j.ator && j.ator.papel === papel ? j.ator : null;
  }
  async function sair(papel) {
    const t = localStorage.getItem(tokenChave(papel));
    if (t) await fetch('/api/sair', { method: 'POST', headers: { Authorization: 'Bearer ' + t } }).catch(() => {});
    localStorage.removeItem(tokenChave(papel));
  }
  async function acao(papel, nome, args = {}) {
    const r = await fetch('/api/acao', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + localStorage.getItem(tokenChave(papel)) },
      body: JSON.stringify({ nome, args })
    });
    const j = await r.json().catch(() => ({ erro: 'Falha de comunicação.' }));
    if (r.status === 401) { localStorage.removeItem(tokenChave(papel)); location.reload(); }
    if (!r.ok) { avisar(j.erro, true); throw new Error(j.erro); }
    return j;
  }

  /* ----- formatação ----- */
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  function tempo(ms) {
    const neg = ms < 0; ms = Math.abs(ms);
    const s = neg ? Math.floor(ms / 1000) : Math.ceil(ms / 1000);
    const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), ss = s % 60;
    return (neg ? '+' : '') + (h ? h + ':' + String(m).padStart(2, '0') : String(m).padStart(2, '0')) + ':' + String(ss).padStart(2, '0');
  }
  const hora = ts => new Date(ts).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
  const relogio = () => new Date(agora()).toLocaleTimeString('pt-BR');
  const decorrido = t => t.acc + (t.start ? agora() - t.start : 0);
  const restante = t => t.total - decorrido(t);
  function nivel(t) {
    const r = restante(t);
    return r <= 0 ? "fim" : r <= Math.min(estado.cfg.alerta * 1000, t.total / 4) ? "alerta" : "ok";
  }
  function pintar(t, elTempo, elBarra) {
    if (!elTempo) return;
    const r = restante(t), n = nivel(t);
    elTempo.textContent = tempo(r);
    elTempo.classList.remove('n-ok', 'n-alerta', 'n-fim', 'pisca');
    elTempo.classList.add('n-' + n);
    if (n === 'fim') elTempo.classList.add('pisca');
    if (elBarra) {
      elBarra.className = 'barra n-' + n;
      elBarra.firstElementChild.style.width = Math.max(0, Math.min(100, r / t.total * 100)) + '%';
    }
  }

  /* ----- parlamentares ----- */
  const parl = id => estado.parls.find(p => p.id === id) || { id, nome: '(removido)', partido: '', fotoV: 0 };
  const fase = id => estado.fases.find(f => f.id === id) || { nome: '—', min: 0, totalMin: 0 };
  const corPartido = sig => estado.partidos[sig] || '#5b6b86';
  function iniciais(n) { const p = n.split(' ').filter(w => w.length > 2); return ((p[0] || n)[0] + ((p.at(-1) || '')[0] || '')).toUpperCase(); }
  function avatar(p, classe = '', estilo = '') {
    const conteudo = p.fotoV ? `<img src="/imagem/${p.id}?v=${p.fotoV}" alt="">` : esc(iniciais(p.nome));
    return `<div class="av ${classe}" style="--cor-partido:${corPartido(p.partido)};${p.fotoV ? '' : 'background:' + corPartido(p.partido) + ';'}${estilo}">${conteudo}</div>`;
  }
  const sigla = s => s ? `<span class="sigla" style="--cor-partido:${corPartido(s)}">${esc(s)}</span>` : '';
  const TIPOS = { aparte: 'Aparte', ordem: 'Questão de ordem', pelaordem: 'Pela ordem', inscricao: 'Inscrição' };
  const SITUACOES = { pendente: 'Pendente', em_discussao: 'Em discussão', em_votacao: 'Em votação', aprovada: 'Aprovada', rejeitada: 'Rejeitada', adiada: 'Adiada', retirada: 'Retirada', sem_quorum: 'Sem quórum' };
  const QUORUNS = { simples: 'Maioria simples', absoluta: 'Maioria absoluta', dois_tercos: 'Dois terços' };
  const VOTOS = { sim: 'Sim', nao: 'Não', abst: 'Abstenção' };
  const MICS = { aberto: 'Microfone aberto', fechado: 'Microfone fechado', cortado: 'Microfone cortado', tribuna: 'Na tribuna' };
  const OCORRENCIAS = { congelar: 'Tempo congelado', descongelar: 'Tempo descongelado', corte: 'Microfone cortado', liberacao: 'Microfone liberado',
    tempo_esgotado: 'Tempo esgotado', plenario_congelado: 'Plenário congelado', plenario_retomado: 'Sessão retomada' };
  const mic = pid => estado.microfones?.[pid] || 'fechado';
  function micChip(pid) {
    const m = mic(pid);
    const cls = { aberto: 'vivo', tribuna: 'vivo', cortado: 'corte', fechado: '' }[m];
    return `<span class="chip ${cls}" title="${MICS[m]}"><span class="ponto"></span>${m === 'cortado' ? 'Cortado' : m === 'fechado' ? 'Mic fechado' : 'Mic aberto'}</span>`;
  }
  const gravando = () => (estado.gravacoes || []).some(g => !g.fim && agora() - g.ultimo < 20000);
  function quorum() {
    const n = estado.parls.length, presentes = Object.keys(estado.presenca).length, minimo = Math.floor(n / 2) + 1;
    return { n, presentes, minimo, ok: presentes >= minimo };
  }
  function usadoNaFase(fid) {
    let s = estado.hist.filter(h => h.fase === fid).reduce((a, h) => a + h.usado * 1000, 0);
    if (estado.orador?.fase === fid) s += decorrido(estado.orador);
    return s;
  }

  /* ----- tema ----- */
  function aplicarTema() {
    const c = estado.casa, raiz = document.documentElement;
    raiz.style.setProperty('--destaque', c.destaque || '#d66060');
    if (!raiz.dataset.temaFixo) raiz.dataset.tema = c.tema === 'claro' ? 'claro' : 'escuro';
    document.querySelectorAll('[data-logo]').forEach(img => { img.src = c.logoV ? `/imagem/logo?v=${c.logoV}` : '/marca/logo.svg'; });
    document.querySelectorAll('[data-casa]').forEach(el => { el.textContent = c.nome; });
    document.querySelectorAll('[data-sessao]').forEach(el => { el.textContent = c.titulo; });
  }

  /* ----- som e avisos ----- */
  let actx;
  function bipe(tipo) {
    try {
      actx ||= new AudioContext();
      const [freq, n] = tipo === 'fim' ? [440, 3] : [660, 1];
      for (let i = 0; i < n; i++) {
        const o = actx.createOscillator(), g = actx.createGain(), t0 = actx.currentTime + i * .4;
        o.frequency.value = freq; o.connect(g); g.connect(actx.destination);
        g.gain.setValueAtTime(.25, t0); g.gain.exponentialRampToValueAtTime(.001, t0 + .3);
        o.start(t0); o.stop(t0 + .3);
      }
    } catch {}
  }
  function avisar(texto, erro = false) {
    let box = document.getElementById('avisos');
    if (!box) { box = document.createElement('div'); box.id = 'avisos'; document.body.append(box); }
    const el = document.createElement('div');
    el.className = 'aviso' + (erro ? ' erro' : ''); el.textContent = texto;
    box.append(el); setTimeout(() => el.remove(), 3500);
  }

  /* reduz uma foto para um quadrado de 320 px em JPEG antes de enviar */
  function lerImagem(arquivo, lado = 320, quadrado = true) {
    return new Promise((ok, falha) => {
      const img = new Image();
      img.onload = () => {
        const c = document.createElement('canvas');
        let sx = 0, sy = 0, sw = img.width, sh = img.height;
        if (quadrado) { const m = Math.min(sw, sh); sx = (sw - m) / 2; sy = (sh - m) / 2; sw = sh = m; c.width = c.height = lado; }
        else { const k = Math.min(1, lado / Math.max(sw, sh)); c.width = sw * k; c.height = sh * k; }
        c.getContext('2d').drawImage(img, sx, sy, sw, sh, 0, 0, c.width, c.height);
        ok(c.toDataURL(quadrado ? 'image/jpeg' : 'image/png', .86));
        URL.revokeObjectURL(img.src);
      };
      img.onerror = () => falha(new Error('Imagem inválida.'));
      img.src = URL.createObjectURL(arquivo);
    });
  }

  return {
    conectar, agora, entrar, quemSou, sair, acao, esc, tempo, hora, relogio, decorrido, restante, nivel, pintar,
    parl, fase, corPartido, avatar, sigla, TIPOS, SITUACOES, QUORUNS, VOTOS, quorum, usadoNaFase, bipe, avisar, lerImagem,
    MICS, OCORRENCIAS, mic, micChip, gravando, token: papel => localStorage.getItem(tokenChave(papel)),
    get estado() { return estado; },
    aoMudar: f => ouvintes.push(f),
    aoSinal: null
  };
})();
