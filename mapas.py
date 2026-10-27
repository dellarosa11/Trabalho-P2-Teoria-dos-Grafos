"""Integra o grafo de cidades a serviços de mapas e à visualização no navegador.

Este módulo cuida de coordenadas, consultas HTTP e desenho. A procura do menor
caminho no grafo cadastrado, pelo algoritmo de Dijkstra, fica em ``grafo.py``.
O OSRM é um serviço externo: ele fornece o traçado e estimativas de uma estrada;
isso é diferente de executar Dijkstra sobre as cidades e ligações do projeto.
"""

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
    """Identifica falhas de rede ou respostas inválidas dos serviços de mapas.

    A interface pode capturar esta exceção para mostrar uma mensagem legível,
    sem precisar conhecer os tipos de erro específicos de HTTP, JSON ou rede.
    """


from grafo import validar_coordenadas

# Reutiliza a validação unificada de coordenadas (evita código duplicado)
_coordenadas = validar_coordenadas


class ServicoMapas:
    """
    Reúne as consultas externas e reutiliza respostas já obtidas nesta instância.

    1. Photon (Komoot / OpenStreetMap): busca nomes de cidades e coordenadas (latitude/longitude).
    2. OSRM (Open Source Routing Machine): consulta um percurso rodoviário, sua
       distância (km), duração estimada (min) e pontos do traçado no mapa.

    Não armazena os vértices e as arestas do projeto: essa responsabilidade é do
    grafo. Os resultados daqui podem ser usados para cadastrar cidades e estradas.
    """
    def __init__(self) -> None:
        """Prepara o cache de respostas e a sincronização das consultas HTTP."""
        # A URL completa identifica a consulta. O cache dura enquanto este
        # objeto existir; não é gravado em disco nem tem expiração automática.
        self._cache: dict[str, dict] = {}
        self._lock = Lock()
        # -infinito faz a primeira consulta ocorrer sem espera obrigatória.
        self._ultima_consulta = -math.inf

    def _obter_json(self, url: str) -> dict:
        """Obtém um objeto JSON por URL, com cache e intervalo entre requisições.

        Retorna uma cópia da resposta para que quem a recebeu não modifique o
        conteúdo do cache. Converte falhas conhecidas em ``ErroMapa``. O intervalo
        de um segundo reduz consultas frequentes, mas o servidor ainda pode
        responder com o erro HTTP 429 (limite de requisições).
        """
        # O mesmo lock protege o cache e espaça inclusive consultas concorrentes.
        with self._lock:
            if url in self._cache:
                return deepcopy(self._cache[url])
            # monotonic mede tempo decorrido sem depender de ajustes no relógio
            # do computador. Consultas atendidas pelo cache não usam a rede.
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
                # O timeout impede que esta tentativa espere indefinidamente;
                # decode transforma bytes em texto e loads interpreta o JSON.
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
            # Guarda apenas JSON do tipo esperado; falhas de HTTP/leitura não
            # entram no cache. Cada consulta ainda valida seus campos específicos.
            self._cache[url] = dados
            return deepcopy(dados)

    def buscar_cidades(self, consulta: str) -> list[dict]:
        """
        Procura uma cidade por nome no Photon e devolve até seis sugestões.

        Cada item contém ``nome``, ``latitude`` e ``longitude``. Espaços extras
        são removidos, registros inválidos são ignorados e sugestões repetidas
        são descartadas. A busca só oferece opções: não insere vértices no grafo.
        """
        if not isinstance(consulta, str):
            raise ValueError("Informe o nome da cidade.")
        consulta = " ".join(consulta.split())
        if not 2 <= len(consulta) <= 200:
            raise ValueError("Digite entre 2 e 200 caracteres para buscar uma cidade.")
        # urlencode monta parâmetros válidos mesmo com espaços e acentos.
        parametros = urlencode({"q": consulta, "limit": 6, "layer": "city"})
        dados = self._obter_json("https://photon.komoot.io/api/?" + parametros)
        resultados = dados.get("features")
        if not isinstance(resultados, list):
            raise ErroMapa("A busca de cidades devolveu uma resposta inválida.")
        cidades = []
        # O conjunto permite verificar rapidamente se uma sugestão já apareceu.
        encontrados = set()
        for resultado in resultados:
            try:
                propriedades = resultado["properties"]
                geometria = resultado["geometry"]
                if geometria.get("type") != "Point":
                    continue
                # GeoJSON usa [longitude, latitude]. O projeto e o Leaflet usam
                # (latitude, longitude), por isso a ordem é trocada e validada.
                longitude, latitude = geometria["coordinates"][:2]
                latitude, longitude = _coordenadas(latitude, longitude)
                nome = propriedades.get("name") or propriedades.get("city")
                if not isinstance(nome, str) or not nome.strip():
                    continue
                partes = [nome.strip()]
                # Estado e país ajudam a distinguir cidades com o mesmo nome.
                for campo in ("state", "country"):
                    valor = propriedades.get(campo)
                    if isinstance(valor, str) and valor.strip() and valor.strip() not in partes:
                        partes.append(valor.strip())
                nome = ", ".join(partes)
                # casefold compara os nomes sem diferenciar maiúsculas/minúsculas.
                identidade = (nome.casefold(), latitude, longitude)
                if identidade not in encontrados:
                    cidades.append({"nome": nome, "latitude": latitude, "longitude": longitude})
                    encontrados.add(identidade)
            except (KeyError, TypeError, ValueError, AttributeError):
                # Uma sugestão malformada não impede aproveitar as demais.
                continue
            if len(cidades) == 6:
                break
        return cidades

    def consultar_estrada(self, origem: dict, destino: dict) -> dict:
        """
        Pede ao OSRM um percurso de carro entre duas cidades com coordenadas.

        Devolve ``distancia`` em quilômetros, ``tempo`` estimado em minutos e
        ``geometria`` como tupla de pares (latitude, longitude) para o desenho.
        Os valores são estimativas do serviço, não medições de trânsito ao vivo.
        A função não cadastra a aresta nem executa o Dijkstra local; seus dados
        podem preencher os pesos de distância e tempo de uma estrada no grafo.
        """
        try:
            lat1, lon1 = _coordenadas(origem["latitude"], origem["longitude"])
            lat2, lon2 = _coordenadas(destino["latitude"], destino["longitude"])
        except (KeyError, TypeError) as erro:
            raise ValueError("Selecione duas cidades com coordenadas cadastradas.") from erro
        if (lat1, lon1) == (lat2, lon2):
            raise ValueError("Selecione duas cidades diferentes.")
        # O endpoint do OSRM recebe longitude antes da latitude. overview=full
        # solicita o traçado completo; steps=false dispensa instruções de direção.
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
            # O OSRM responde em metros e segundos; a interface usa km e minutos.
            distancia = float(rota["distance"]) / 1000
            tempo = float(rota["duration"]) / 60
            if not all(math.isfinite(valor) and valor > 0 for valor in (distancia, tempo)):
                raise ValueError
            if rota["geometry"].get("type") != "LineString":
                raise ValueError
            # Converte a LineString GeoJSON de (lon, lat) para (lat, lon).
            # A tupla conserva os pontos necessários para acompanhar as curvas.
            geometria = tuple(_coordenadas(ponto[1], ponto[0]) for ponto in rota["geometry"]["coordinates"])
            if len(geometria) < 2:
                raise ValueError
        except (KeyError, IndexError, TypeError, ValueError, OverflowError, AttributeError) as erro:
            raise ErroMapa("O serviço de estradas devolveu um percurso inválido.") from erro
        return {"distancia": distancia, "tempo": tempo, "geometria": geometria}


# ---------------------------------------------------------------------------
# GERAÇÃO DO MAPA INTERATIVO (LEAFLET.JS)
# ---------------------------------------------------------------------------

def gerar_mapa(grafo, arquivo: Path, rota=None) -> Path:
    """
    Grava um HTML que representa cidades, estradas e uma rota opcional no Leaflet.

    ``grafo`` fornece as cidades, coordenadas e arestas; ``arquivo`` é o destino
    do HTML; ``rota`` pode ser o resultado já calculado por Dijkstra. A função
    apenas prepara a visualização, sem procurar ou recalcular o menor caminho.
    Estradas importadas usam o traçado do OSRM; ligações sem traçado usam uma
    linha reta ilustrativa. A rota selecionada aparece destacada em laranja.
    Retorna o caminho absoluto do arquivo criado.
    """
    coordenadas = grafo.coordenadas
    cidades = [{"nome": nome, "ponto": ponto} for nome, ponto in coordenadas.items()]
    estradas = []
    # Uma aresta pode existir no grafo mesmo sem coordenadas suficientes para
    # aparecer no mapa. Seu uso pelo algoritmo não depende do desenho.
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
        # zip produz os trechos consecutivos: [A, B, C] vira (A, B) e (B, C).
        # Para cada trecho, recupera-se a geometria da aresta correspondente.
        for origem, destino in zip(rota.cidades, rota.cidades[1:]):
            estrada = next((item for item in estradas if item["origem"] == origem and item["destino"] == destino), None)
            invertida = False
            if estrada is None:
                # Só uma ligação bidirecional permite usar a geometria no
                # sentido contrário ao de seu cadastro.
                estrada = next((item for item in estradas if item["bidirecional"] and item["origem"] == destino and item["destino"] == origem), None)
                invertida = True
            if estrada:
                trecho = dict(estrada, origem=origem, destino=destino, bidirecional=False)
                if invertida:
                    # Inverter os pontos mantém o traçado no sentido da viagem.
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
    # O JSON transfere os dados Python para o JavaScript. Não admite NaN/infinito.
    serializado = json.dumps(dados, ensure_ascii=False, allow_nan=False)
    # Escapa caracteres que poderiam encerrar/interferir no bloco <script>,
    # preservando seu valor quando o navegador interpretar o JSON.
    for caractere, escape in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"), ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        serializado = serializado.replace(caractere, escape)
    arquivo = Path(arquivo).resolve()
    # Substitui apenas o marcador reservado do modelo; não calcula rotas no HTML.
    arquivo.write_text(_HTML.replace("__DADOS_MAPA__", serializado), encoding="utf-8")
    return arquivo


class ServidorMapa:
    """Disponibiliza somente o HTML do mapa por HTTP no próprio computador.

    O servidor local entrega o documento ao navegador. As imagens do mapa e a
    biblioteca Leaflet ainda são carregadas de serviços externos pela página.
    """

    def __init__(self, arquivo: Path) -> None:
        """Inicia o servidor em uma porta livre e expõe seu endereço em ``url``."""
        arquivo = Path(arquivo).resolve()

        class Handler(BaseHTTPRequestHandler):
            """Atende as requisições HTTP usando o arquivo capturado no construtor."""

            def do_GET(self):
                """Entrega o mapa em / ou /mapa; recusa os demais caminhos."""
                # Considera apenas o caminho, sem parâmetros de consulta da URL.
                caminho = urlsplit(self.path).path
                if caminho == "/favicon.ico":
                    # O navegador costuma pedir um ícone; 204 significa sem conteúdo.
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
                # Relê o arquivo a cada acesso e pede ao navegador para não
                # reutilizar uma versão antiga depois de o mapa ser regenerado.
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(conteudo)

            def log_message(self, format, *args):
                """Silencia o log HTTP padrão para não poluir o terminal da aplicação."""
                pass

        # 127.0.0.1 restringe o acesso à própria máquina; porta 0 deixa o sistema
        # operacional escolher uma porta disponível, evitando um número fixo.
        self._servidor = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._servidor.daemon_threads = True
        self.url = f"http://127.0.0.1:{self._servidor.server_port}/mapa"
        # A thread atende o navegador sem bloquear a janela principal. daemon
        # permite encerrar o processo sem depender de uma requisição terminar.
        self._thread = Thread(target=self._servidor.serve_forever, daemon=True)
        self._thread.start()
        self._fechado = False

    def fechar(self) -> None:
        """Interrompe o atendimento e libera a porta; pode ser chamada novamente."""
        if not self._fechado:
            self._servidor.shutdown()
            self._servidor.server_close()
            # Aguarda brevemente a thread, sem prender indefinidamente o fechamento.
            self._thread.join(timeout=2)
            self._fechado = True


# Modelo de página: o Python injeta os dados; Leaflet desenha e trata zoom/cliques.
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
// Os dados abaixo foram serializados pelo Python. O navegador só os representa;
// o algoritmo de Dijkstra já foi executado em grafo.py quando uma rota é informada.
const dados = __DADOS_MAPA__;
const aviso = document.getElementById('aviso');
// Exibe mensagens de carregamento, indisponibilidade ou ausência de cidades.
const avisar = texto => { aviso.textContent = texto; aviso.hidden = false; };
// Formata números conforme o padrão brasileiro, com até uma casa decimal.
const numero = valor => Number(valor).toLocaleString('pt-BR', {maximumFractionDigits: 1});
// Cria conteúdo para popups/tooltips usando texto literal, sem interpretar HTML
// que eventualmente esteja presente no nome de uma cidade.
const popup = texto => {const elemento = document.createElement('div'); elemento.className = 'popup'; elemento.textContent = texto; return elemento;};
// Monta o resumo a partir do resultado recebido, incluindo os três totais da rota.
let resumo = dados.cidades.length + ' cidades no mapa • ' + dados.estradas.length + ' ligações';
if (dados.semCoordenadas) resumo += '\\n' + dados.semCoordenadas + ' cidade(s) sem coordenadas não aparecem no mapa.';
if (dados.rota) resumo += '\\n' + dados.rota.cidades.join(' → ') + '\\n' + numero(dados.rota.distancia) + ' km • ' + numero(dados.rota.tempo) + ' min estimados • R$ ' + numero(dados.rota.custo);
document.getElementById('resumo').textContent = resumo;
// L é o objeto global do Leaflet. Se a biblioteca ou seu estilo não carregar,
// mostra uma mensagem em vez de tentar desenhar com recursos indisponíveis.
if (typeof L === 'undefined' || window.falhaEstilo) {
    avisar('Não foi possível carregar o Leaflet. Verifique a conexão e recarregue esta página.');
} else {
    aviso.hidden = true;
    // Começa com uma visão do Brasil; fitBounds ajustará o enquadramento aos dados.
    const mapa = L.map('mapa').setView([-14.235, -51.9253], 4);
    // As peças de imagem (tiles) do OpenStreetMap formam o mapa de fundo.
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors | Estradas: <a href="https://project-osrm.org/">OSRM</a>'
    }).on('tileerror', () => avisar('Algumas imagens do mapa não carregaram. Verifique a conexão e recarregue a página.')).addTo(mapa);
    // Reúne coordenadas para enquadrar estradas e cidades no final do desenho.
    const limites = [];
    // Desenha uma aresta: pontos unidos por linhas, descrição e estilo opcional
    // de destaque. A seta no texto indica direção; a função não calcula caminhos.
    function desenhar(estrada, selecionada) {
        const sentido = estrada.bidirecional ? ' ↔ ' : ' → ';
        const texto = estrada.origem + sentido + estrada.destino + '\\n' + numero(estrada.distancia) + ' km • ' + numero(estrada.tempo) + ' min estimados' + '\\nCusto cadastrado: R$ ' + numero(estrada.custo) + '\\n' + (estrada.importada ? 'Percurso rodoviário importado do OSRM.' : 'Ligação cadastrada: linha reta ilustrativa, sem traçado rodoviário.');
        // Importadas são azuis; manuais, cinzas tracejadas; a rota é laranja.
        L.polyline(estrada.pontos, {
            color: selecionada ? '#e66d22' : (estrada.importada ? '#2775b9' : '#788796'),
            weight: selecionada ? 6 : 4, opacity: selecionada ? 1 : .7,
            dashArray: estrada.importada ? null : '8 7'
        }).bindPopup(popup(texto)).addTo(mapa);
        for (const ponto of estrada.pontos) limites.push(ponto);
    }
    // O destaque é desenhado depois das demais estradas para ficar por cima.
    dados.estradas.forEach(estrada => desenhar(estrada, false));
    dados.destaque.forEach(estrada => desenhar(estrada, true));
    // Cada cidade com coordenadas representa um vértice visível. Se estiver na
    // rota, recebe outra cor/tamanho e o número de sua posição no percurso.
    dados.cidades.forEach(cidade => {
        const ordem = dados.rota ? dados.rota.cidades.indexOf(cidade.nome) : -1;
        const texto = (ordem < 0 ? '' : (ordem + 1) + '. ') + cidade.nome;
        L.circleMarker(cidade.ponto, {
            radius: ordem < 0 ? 7 : 9, color: '#fff', weight: 3,
            fillColor: ordem < 0 ? '#17324d' : '#e66d22', fillOpacity: 1
        }).bindPopup(popup(texto)).bindTooltip(popup(texto)).addTo(mapa);
        limites.push(cidade.ponto);
    });
    // Enquadra todos os pontos com margem para o painel. maxZoom evita ampliar
    // demais quando há poucos pontos ou cidades muito próximas umas das outras.
    if (limites.length) mapa.fitBounds(limites, {paddingTopLeft: [40, 180], paddingBottomRight: [40, 40], maxZoom: 12});
    else avisar('Busque e cadastre cidades reais para exibi-las neste mapa.');
}
</script>
</body>
</html>
"""
