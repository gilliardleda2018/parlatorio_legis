"""Cliente do SAPL (Sistema de Apoio ao Processo Legislativo, Interlegis).

Somente leitura, pela API REST pública de cada instância (`https://sapl.<cidade>.<uf>.leg.br/api/`):
parlamentares ativos e filiação partidária, tipos e matérias legislativas com
autoria, sessões plenárias e os itens de pauta (expediente e ordem do dia).

Respeita o servidor da câmara: páginas de 100, pausa entre pedidos e limite de itens.
"""
import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

AGENTE = "ParlatorioLegis/2 (+https://github.com/gilliardleda2018/parlatorio_legis)"
PAUSA = 0.15                 # segundos entre pedidos
TIPOS_IMAGEM = {"image/png", "image/jpeg", "image/webp"}
MINUSCULAS = {"de", "da", "do", "das", "dos", "e", "di", "du"}


class ErroSapl(Exception):
    pass


def normalizar_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if url.endswith("/api"):
        url = url[:-4]
    partes = urllib.parse.urlparse(url)
    if partes.scheme not in ("http", "https") or not partes.netloc:
        raise ErroSapl("Informe o endereço do SAPL, por exemplo https://sapl.suacidade.uf.leg.br")
    return f"{partes.scheme}://{partes.netloc}"


def nome_bonito(nome: str) -> str:
    """NOME EM MAIÚSCULAS → Nome em Maiúsculas (mantém o que já vem misto)."""
    if not nome or nome != nome.upper():
        return nome
    palavras = nome.lower().split()
    return " ".join(p if (k and p in MINUSCULAS) else p.capitalize() for k, p in enumerate(palavras))


def chave_nome(nome: str) -> str:
    sem = "".join(c for c in unicodedata.normalize("NFD", nome or "") if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", sem.lower()).strip()


class Sapl:
    def __init__(self, url: str):
        self.base = normalizar_url(url)
        self.host = urllib.parse.urlparse(self.base).netloc
        self._autores: dict[int, dict] = {}

    # ---- HTTP
    def _abrir(self, url: str, aceitar="application/json", limite=8_000_000):
        time.sleep(PAUSA)
        req = urllib.request.Request(url, headers={"User-Agent": AGENTE, "Accept": aceitar})
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                dados = r.read(limite + 1)
                if len(dados) > limite:
                    raise ErroSapl("Resposta grande demais do SAPL.")
                return r.headers.get_content_type(), dados
        except urllib.error.HTTPError as e:
            raise ErroSapl(f"O SAPL respondeu {e.code} em {urllib.parse.urlparse(url).path}.")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ErroSapl(f"Não foi possível acessar o SAPL ({getattr(e, 'reason', e)}).")

    def get(self, caminho: str, **params) -> dict:
        url = f"{self.base}{caminho}"
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
        tipo, dados = self._abrir(url)
        try:
            return json.loads(dados)
        except json.JSONDecodeError:
            raise ErroSapl("O endereço não parece ser um SAPL (a resposta não é JSON).")

    def todos(self, caminho: str, limite: int = 2000, **params) -> list[dict]:
        itens, pagina = [], 1
        while len(itens) < limite:
            d = self.get(caminho, page=pagina, page_size=100, **params)
            itens += d.get("results", [])
            proxima = (d.get("pagination") or {}).get("next_page")
            if not proxima:
                break
            pagina = proxima
        return itens[:limite]

    # ---- recursos
    def casa(self) -> dict:
        r = self.get("/api/base/casalegislativa/").get("results") or []
        if not r:
            raise ErroSapl("O SAPL não informou a casa legislativa.")
        return r[0]

    def partidos(self) -> dict[int, str]:
        return {p["id"]: p["sigla"] for p in self.todos("/api/parlamentares/partido/")}

    def parlamentares_ativos(self) -> list[dict]:
        return self.todos("/api/parlamentares/parlamentar/", ativo="True")

    def partido_atual(self, parlamentar_id: int, partidos: dict[int, str]) -> str:
        filiacoes = self.todos("/api/parlamentares/filiacao/", parlamentar=parlamentar_id)
        ativas = [f for f in filiacoes if not f.get("data_desfiliacao")] or filiacoes
        if not ativas:
            return ""
        ultima = max(ativas, key=lambda f: f.get("data") or "")
        return partidos.get(ultima["partido"], "")

    def tipos_materia(self) -> list[dict]:
        return [{"id": t["id"], "sigla": t["sigla"], "descricao": t["descricao"]}
                for t in self.todos("/api/materia/tipomaterialegislativa/")]

    def materias(self, ano: int, tipo: int | None, limite: int, so_tramitando: bool = True) -> list[dict]:
        return self.todos("/api/materia/materialegislativa/", limite=limite, ano=ano, tipo=tipo,
                          em_tramitacao="True" if so_tramitando else None, o="-data_apresentacao")

    def materia(self, materia_id: int) -> dict:
        return self.get(f"/api/materia/materialegislativa/{int(materia_id)}/")

    def autores(self, materia_id: int) -> list[dict]:
        """Autores da matéria, primeiro autor antes: [{nome, parlamentar (id no SAPL ou None)}]."""
        saida = []
        for a in sorted(self.todos("/api/materia/autoria/", materia=materia_id), key=lambda a: not a.get("primeiro_autor")):
            if a["autor"] not in self._autores:
                au = self.get(f"/api/base/autor/{int(a['autor'])}/")
                rel = (au.get("autor_related") or {})
                self._autores[a["autor"]] = {"nome": au.get("nome") or rel.get("text") or "", "objeto": au.get("object_id"),
                                             "tipo": au.get("content_type")}
            saida.append(self._autores[a["autor"]])
        return saida

    def sessoes_recentes(self, quantas: int = 15) -> list[dict]:
        r = self.get("/api/sessao/sessaoplenaria/", o="-data_inicio", page_size=quantas).get("results", [])
        return [{"id": s["id"], "nome": s["__str__"], "data": s.get("data_inicio"), "hora": s.get("hora_inicio")} for s in r]

    def itens_pauta(self, sessao_id: int) -> list[dict]:
        """Expediente e ordem do dia de uma sessão, na ordem do SAPL."""
        itens = []
        for origem, caminho in (("expediente", "/api/sessao/expedientemateria/"), ("ordem_do_dia", "/api/sessao/ordemdia/")):
            for x in self.todos(caminho, sessao_plenaria=sessao_id):
                itens.append({"origem": origem, "ordem": x.get("numero_ordem") or 0, "materia": x["materia"],
                              "resultado": x.get("resultado") or ""})
        itens.sort(key=lambda x: (x["origem"] != "expediente", x["ordem"]))
        return itens

    def imagem(self, url: str) -> tuple[str, bytes] | None:
        """Baixa a foto do parlamentar, só do próprio servidor do SAPL e até 1,5 MB."""
        if not url:
            return None
        partes = urllib.parse.urlparse(url)
        if partes.scheme not in ("http", "https") or partes.netloc != self.host:
            return None
        tipo, dados = self._abrir(url, aceitar="image/*", limite=1_500_000)
        return (tipo, dados) if tipo in TIPOS_IMAGEM else None
