"""Busca de cidades, estradas reais e visualização Leaflet, usando a biblioteca padrão."""

from __future__ import annotations

from copy import deepcopy
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
from threading import Lock, Thread
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


class ErroMapa(RuntimeError):
    """Falha ao consultar um dos serviços de mapas."""


def _coordenadas(latitude, longitude) -> tuple[float, float]:
    try:
        if isinstance(latitude, bool) or isinstance(longitude, bool):
            raise ValueError
        latitude, longitude = float(latitude), float(longitude)
        if not math.isfinite(latitude) or not math.isfinite(longitude):
            raise ValueError
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError
    except (TypeError, ValueError, OverflowError) as erro:
        raise ValueError("A cidade precisa ter latitude e longitude válidas.") from erro
    return latitude, longitude


class ServicoMapas:
    def __init__(self) -> None:
        self._cache: dict[str, dict] = {}
        self._lock = Lock()
        self._ultima_consulta = -math.inf

    def _obter_json(self, url: str) -> dict:
        # O mesmo lock protege o cache e espaça inclusive consultas concorrentes.
        with self._lock:
            if url in self._cache:
                return deepcopy(self._cache[url])
            espera = 1.0 - (time.monotonic() - self._ultima_consulta)
            if espera > 0:
                time.sleep(espera)
            self._ultima_consulta = time.monotonic()
            pedido = Request(url, headers={
                "User-Agent": "SistemaRotasCidades/1.0 (academic desktop application)",
                "Accept": "application/json",
                "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.5",
            })
            try:
                with urlopen(pedido, timeout=20) as resposta:
                    dados = json.loads(resposta.read().decode("utf-8"))
            except HTTPError as erro:
                with erro:
                    if erro.code == 429:
                        raise ErroMapa("O serviço recebeu muitas consultas. Aguarde e tente novamente.") from erro
                    try:
                        detalhe = json.loads(erro.read().decode("utf-8"))
                    except (ValueError, UnicodeError, OSError):
                        detalhe = {}
                    if isinstance(detalhe, dict) and detalhe.get("code") in ("NoRoute", "NoSegment"):
                        raise ErroMapa("Não foi encontrada uma estrada entre essas cidades.") from erro
                    raise ErroMapa("O serviço de mapas está indisponível. Tente novamente mais tarde.") from erro
            except (URLError, OSError, TimeoutError, HTTPException) as erro:
                raise ErroMapa("Não foi possível consultar o mapa. Verifique a conexão com a internet.") from erro
            except (ValueError, UnicodeError) as erro:
                raise ErroMapa("O serviço de mapas devolveu uma resposta inválida.") from erro
            if not isinstance(dados, dict):
                raise ErroMapa("O serviço de mapas devolveu uma resposta inválida.")
            self._cache[url] = dados
            return deepcopy(dados)

    def buscar_cidades(self, consulta: str) -> list[dict]:
        if not isinstance(consulta, str):
            raise ValueError("Informe o nome da cidade.")
        consulta = " ".join(consulta.split())
        if not 2 <= len(consulta) <= 200:
            raise ValueError("Digite entre 2 e 200 caracteres para buscar uma cidade.")
        parametros = urlencode({"q": consulta, "limit": 6, "layer": "city"})
        dados = self._obter_json("https://photon.komoot.io/api/?" + parametros)
        resultados = dados.get("features")
        if not isinstance(resultados, list):
            raise ErroMapa("A busca de cidades devolveu uma resposta inválida.")
        cidades = []
        encontrados = set()
        for resultado in resultados:
            try:
                propriedades = resultado["properties"]
                geometria = resultado["geometry"]
                if geometria.get("type") != "Point":
                    continue
                longitude, latitude = geometria["coordinates"][:2]
                latitude, longitude = _coordenadas(latitude, longitude)
                nome = propriedades.get("name") or propriedades.get("city")
                if not isinstance(nome, str) or not nome.strip():
                    continue
                partes = [nome.strip()]
                for campo in ("state", "country"):
                    valor = propriedades.get(campo)
                    if isinstance(valor, str) and valor.strip() and valor.strip() not in partes:
                        partes.append(valor.strip())
                nome = ", ".join(partes)
                identidade = (nome.casefold(), latitude, longitude)
                if identidade not in encontrados:
                    cidades.append({"nome": nome, "latitude": latitude, "longitude": longitude})
                    encontrados.add(identidade)
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
            if len(cidades) == 6:
                break
        return cidades

    def consultar_estrada(self, origem: dict, destino: dict) -> dict:
        try:
            lat1, lon1 = _coordenadas(origem["latitude"], origem["longitude"])
            lat2, lon2 = _coordenadas(destino["latitude"], destino["longitude"])
        except (KeyError, TypeError) as erro:
            raise ValueError("Selecione duas cidades com coordenadas cadastradas.") from erro
        if (lat1, lon1) == (lat2, lon2):
            raise ValueError("Selecione duas cidades diferentes.")
        url = (
            f"https://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}"
            "?overview=full&geometries=geojson&steps=false"
        )
        dados = self._obter_json(url)
        if dados.get("code") in ("NoRoute", "NoSegment"):
            raise ErroMapa("Não foi encontrada uma estrada entre essas cidades.")
        if dados.get("code") != "Ok":
            raise ErroMapa("O serviço de estradas não conseguiu calcular esse percurso.")
        try:
            rota = dados["routes"][0]
            distancia = float(rota["distance"]) / 1000
            tempo = float(rota["duration"]) / 60
            if not all(math.isfinite(valor) and valor > 0 for valor in (distancia, tempo)):
                raise ValueError
            if rota["geometry"].get("type") != "LineString":
                raise ValueError
            geometria = tuple(_coordenadas(ponto[1], ponto[0]) for ponto in rota["geometry"]["coordinates"])
            if len(geometria) < 2:
                raise ValueError
        except (KeyError, IndexError, TypeError, ValueError, OverflowError, AttributeError) as erro:
            raise ErroMapa("O serviço de estradas devolveu um percurso inválido.") from erro
        return {"distancia": distancia, "tempo": tempo, "geometria": geometria}


def gerar_mapa(grafo, arquivo: Path, rota=None) -> Path:
    coordenadas = grafo.coordenadas
    cidades = [{"nome": nome, "ponto": ponto} for nome, ponto in coordenadas.items()]
    estradas = []
    for estrada in grafo.estradas:
        geometria = getattr(estrada, "geometria", ())
        if not geometria:
            if estrada.origem not in coordenadas or estrada.destino not in coordenadas:
                continue
            pontos = [coordenadas[estrada.origem], coordenadas[estrada.destino]]
        else:
            pontos = geometria
        estradas.append({
            "origem": estrada.origem, "destino": estrada.destino,
            "distancia": estrada.distancia, "tempo": estrada.tempo,
            "custo": estrada.custo, "pontos": pontos,
            "importada": bool(geometria),
            "bidirecional": getattr(estrada, "bidirecional", True),
        })
    destaque = []
    if rota:
        for origem, destino in zip(rota.cidades, rota.cidades[1:]):
            estrada = next((item for item in estradas if item["origem"] == origem and item["destino"] == destino), None)
            invertida = False
            if estrada is None:
                estrada = next((item for item in estradas if item["bidirecional"] and item["origem"] == destino and item["destino"] == origem), None)
                invertida = True
            if estrada:
                trecho = dict(estrada, origem=origem, destino=destino, bidirecional=False)
                if invertida:
                    trecho["pontos"] = list(reversed(estrada["pontos"]))
                destaque.append(trecho)
    dados = {
        "cidades": cidades, "estradas": estradas, "destaque": destaque,
        "semCoordenadas": len(grafo.cidades) - len(cidades),
        "rota": None if rota is None else {
            "cidades": rota.cidades, "distancia": rota.distancia,
            "tempo": rota.tempo, "custo": rota.custo,
        },
    }
    serializado = json.dumps(dados, ensure_ascii=False, allow_nan=False)
    for caractere, escape in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"), ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        serializado = serializado.replace(caractere, escape)
    arquivo = Path(arquivo).resolve()
    arquivo.write_text(_HTML.replace("__DADOS_MAPA__", serializado), encoding="utf-8")
    return arquivo


class ServidorMapa:
    """Serve exclusivamente o mapa gerado, no endereço de loopback do computador."""

    def __init__(self, arquivo: Path) -> None:
        arquivo = Path(arquivo).resolve()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                caminho = urlsplit(self.path).path
                if caminho == "/favicon.ico":
                    self.send_response(204)
                    self.end_headers()
                    return
                if caminho not in ("/", "/mapa"):
                    self.send_error(404)
                    return
                try:
                    conteudo = arquivo.read_bytes()
                except OSError:
                    self.send_error(404, "Mapa indisponivel")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(conteudo)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(conteudo)

            def log_message(self, format, *args):
                pass

        self._servidor = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._servidor.daemon_threads = True
        self.url = f"http://127.0.0.1:{self._servidor.server_port}/mapa"
        self._thread = Thread(target=self._servidor.serve_forever, daemon=True)
        self._thread.start()
        self._fechado = False

    def fechar(self) -> None:
        if not self._fechado:
            self._servidor.shutdown()
            self._servidor.server_close()
            self._thread.join(timeout=2)
            self._fechado = True


_HTML = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="strict-origin-when-cross-origin">
<title>Sistema de Rotas entre Cidades</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
 integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin=""
 onerror="window.falhaEstilo=true">
<style>
html,body,#mapa{height:100%;margin:0}body{font-family:system-ui,sans-serif;color:#17324d;background:#e7edf2}
.painel{position:absolute;z-index:1000;top:16px;left:60px;max-width:350px;padding:16px 20px;background:#ffffffed;border-radius:12px;box-shadow:0 3px 20px #17324d25}
h1{font-size:19px;margin:0 0 8px}.legenda{font-size:13px;display:grid;gap:7px}.linha{display:inline-block;width:26px;border-top:4px solid #2775b9;margin-right:9px;vertical-align:middle}.manual{border-top:3px dashed #788796}.rota{border-color:#e66d22}
#resumo{font-size:13px;line-height:1.5;margin:12px 0 0;white-space:pre-wrap;max-height:22vh;overflow:auto}
#aviso{position:absolute;z-index:1100;bottom:40px;left:50%;transform:translateX(-50%);max-width:80%;background:#fff4d3;color:#654b00;padding:12px 18px;border-radius:8px;font-size:14px;box-shadow:0 2px 12px #0002}
#aviso[hidden]{display:none}.popup{white-space:pre-line;line-height:1.6;overflow-wrap:anywhere}
@media(max-width:600px){.painel{top:10px;left:55px;right:10px;padding:12px;max-width:none}h1{font-size:16px}.legenda{font-size:12px}}
</style>
</head>
<body>
<div id="mapa" role="region" aria-label="Mapa de cidades e estradas"></div>
<aside class="painel"><h1>Rotas entre cidades</h1><div class="legenda">
<span><i class="linha"></i>Estrada importada do OSRM</span>
<span><i class="linha manual"></i>Ligação cadastrada manualmente</span>
<span><i class="linha rota"></i>Rota selecionada no grafo</span>
</div><p id="resumo"></p></aside>
<div id="aviso" role="status">Carregando o mapa. É necessário acesso à internet.</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
 integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
<script>
const dados = __DADOS_MAPA__;
const aviso = document.getElementById('aviso');
const avisar = texto => { aviso.textContent = texto; aviso.hidden = false; };
const numero = valor => Number(valor).toLocaleString('pt-BR', {maximumFractionDigits: 1});
const popup = texto => {const elemento = document.createElement('div'); elemento.className = 'popup'; elemento.textContent = texto; return elemento;};
let resumo = dados.cidades.length + ' cidades no mapa • ' + dados.estradas.length + ' ligações';
if (dados.semCoordenadas) resumo += '\\n' + dados.semCoordenadas + ' cidade(s) sem coordenadas não aparecem no mapa.';
if (dados.rota) resumo += '\\n' + dados.rota.cidades.join(' → ') + '\\n' + numero(dados.rota.distancia) + ' km • ' + numero(dados.rota.tempo) + ' min estimados • R$ ' + numero(dados.rota.custo);
document.getElementById('resumo').textContent = resumo;
if (typeof L === 'undefined' || window.falhaEstilo) {
    avisar('Não foi possível carregar o Leaflet. Verifique a conexão e recarregue esta página.');
} else {
    aviso.hidden = true;
    const mapa = L.map('mapa').setView([-14.235, -51.9253], 4);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors | Estradas: <a href="https://project-osrm.org/">OSRM</a>'
    }).on('tileerror', () => avisar('Algumas imagens do mapa não carregaram. Verifique a conexão e recarregue a página.')).addTo(mapa);
    const limites = [];
    function desenhar(estrada, selecionada) {
        const sentido = estrada.bidirecional ? ' ↔ ' : ' → ';
        const texto = estrada.origem + sentido + estrada.destino + '\\n' + numero(estrada.distancia) + ' km • ' + numero(estrada.tempo) + ' min estimados' + '\\nCusto cadastrado: R$ ' + numero(estrada.custo) + '\\n' + (estrada.importada ? 'Percurso rodoviário importado do OSRM.' : 'Ligação cadastrada: linha reta ilustrativa, sem traçado rodoviário.');
        L.polyline(estrada.pontos, {
            color: selecionada ? '#e66d22' : (estrada.importada ? '#2775b9' : '#788796'),
            weight: selecionada ? 6 : 4, opacity: selecionada ? 1 : .7,
            dashArray: estrada.importada ? null : '8 7'
        }).bindPopup(popup(texto)).addTo(mapa);
        for (const ponto of estrada.pontos) limites.push(ponto);
    }
    dados.estradas.forEach(estrada => desenhar(estrada, false));
    dados.destaque.forEach(estrada => desenhar(estrada, true));
    dados.cidades.forEach(cidade => {
        const ordem = dados.rota ? dados.rota.cidades.indexOf(cidade.nome) : -1;
        const texto = (ordem < 0 ? '' : (ordem + 1) + '. ') + cidade.nome;
        L.circleMarker(cidade.ponto, {
            radius: ordem < 0 ? 7 : 9, color: '#fff', weight: 3,
            fillColor: ordem < 0 ? '#17324d' : '#e66d22', fillOpacity: 1
        }).bindPopup(popup(texto)).bindTooltip(popup(texto)).addTo(mapa);
        limites.push(cidade.ponto);
    });
    if (limites.length) mapa.fitBounds(limites, {paddingTopLeft: [40, 180], paddingBottomRight: [40, 40], maxZoom: 12});
    else avisar('Busque e cadastre cidades reais para exibi-las neste mapa.');
}
</script>
</body>
</html>
"""
