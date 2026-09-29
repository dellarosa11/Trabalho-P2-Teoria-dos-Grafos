from copy import deepcopy
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen

from grafo import Grafo
from mapas import ErroMapa, ServicoMapas, ServidorMapa, gerar_mapa


class TestServicoMapas(unittest.TestCase):
    def setUp(self):
        self.servico = ServicoMapas()
        self.pausa = patch("mapas.time.sleep")
        self.pausa.start()
        self.addCleanup(self.pausa.stop)

    @staticmethod
    def resposta(dados):
        resposta = MagicMock()
        resposta.__enter__.return_value = resposta
        resposta.read.return_value = json.dumps(dados).encode("utf-8")
        return resposta

    @staticmethod
    def cidade():
        return {
            "properties": {"name": "Campinas", "state": "São Paulo", "country": "Brasil"},
            "geometry": {"type": "Point", "coordinates": [-47.06, -22.9]},
        }

    @staticmethod
    def percurso():
        return {"code": "Ok", "routes": [{
            "distance": 12345,
            "duration": 750,
            "geometry": {"type": "LineString", "coordinates": [
                [-46.6, -23.5], [-46.8, -23.1], [-47.06, -22.9],
            ]},
        }]}

    @staticmethod
    def extremos():
        return {"latitude": -23.5, "longitude": -46.6}, {"latitude": -22.9, "longitude": -47.06}

    def test_photon_coordenadas_duplicatas_filtros_e_cache(self):
        cidade = self.cidade()
        invalida = deepcopy(cidade)
        invalida["geometry"]["coordinates"] = [200, 95]
        dados = {"features": [None, invalida, cidade, deepcopy(cidade)]}
        with patch("mapas.urlopen", return_value=self.resposta(dados)) as abrir:
            cidades = self.servico.buscar_cidades("  Campinas   Brasil ")
            self.assertEqual(cidades, [{
                "nome": "Campinas, São Paulo, Brasil", "latitude": -22.9, "longitude": -47.06,
            }])
            cidades[0]["nome"] = "Nome alterado pelo consumidor"
            novamente = self.servico.buscar_cidades("Campinas Brasil")

        self.assertEqual(novamente[0]["nome"], "Campinas, São Paulo, Brasil")
        abrir.assert_called_once()
        pedido = abrir.call_args.args[0]
        self.assertEqual(urlsplit(pedido.full_url).netloc, "photon.komoot.io")
        self.assertEqual(parse_qs(urlsplit(pedido.full_url).query)["q"], ["Campinas Brasil"])
        self.assertEqual(parse_qs(urlsplit(pedido.full_url).query)["layer"], ["city"])

    def test_osrm_converte_unidades_ordem_de_coordenadas_e_cache(self):
        with patch("mapas.urlopen", return_value=self.resposta(self.percurso())) as abrir:
            estrada = self.servico.consultar_estrada(*self.extremos())
            self.assertEqual(estrada, {
                "distancia": 12.345, "tempo": 12.5,
                "geometria": ((-23.5, -46.6), (-23.1, -46.8), (-22.9, -47.06)),
            })
            estrada["distancia"] = 0
            novamente = self.servico.consultar_estrada(*self.extremos())

        self.assertEqual(novamente["distancia"], 12.345)
        abrir.assert_called_once()
        url = urlsplit(abrir.call_args.args[0].full_url)
        self.assertEqual(url.path, "/route/v1/driving/-46.6,-23.5;-47.06,-22.9")
        self.assertEqual(parse_qs(url.query)["geometries"], ["geojson"])

    def test_osrm_cache_distingue_os_sentidos(self):
        with patch("mapas.urlopen", return_value=self.resposta(self.percurso())) as abrir:
            origem, destino = self.extremos()
            self.servico.consultar_estrada(origem, destino)
            self.servico.consultar_estrada(destino, origem)

        self.assertEqual(abrir.call_count, 2)
        urls = [chamada.args[0].full_url for chamada in abrir.call_args_list]
        self.assertNotEqual(urls[0], urls[1])
        self.assertIn("/-47.06,-22.9;-46.6,-23.5?", urls[1])

    def test_entradas_invalidas_nao_consultam_a_rede(self):
        with patch("mapas.urlopen") as abrir:
            for consulta in ["", "a", "x" * 201, None]:
                with self.subTest(consulta=consulta), self.assertRaises(ValueError):
                    self.servico.buscar_cidades(consulta)
            origem, destino = self.extremos()
            for inicio, fim in [
                (origem, origem), ({}, destino),
                ({"latitude": 91, "longitude": 0}, destino),
                ({"latitude": True, "longitude": 0}, destino),
            ]:
                with self.subTest(inicio=inicio), self.assertRaises(ValueError):
                    self.servico.consultar_estrada(inicio, fim)
        abrir.assert_not_called()

    def test_falha_de_rede_nao_e_armazenada_no_cache(self):
        dados = {"features": [self.cidade()]}
        with patch("mapas.urlopen", side_effect=[URLError("offline"), self.resposta(dados)]) as abrir:
            with self.assertRaises(ErroMapa):
                self.servico.buscar_cidades("Campinas")
            self.assertEqual(len(self.servico.buscar_cidades("Campinas")), 1)
        self.assertEqual(abrir.call_count, 2)

    def test_erros_http_e_json_sao_apresentados_como_erro_mapa(self):
        erros = [
            HTTPError("https://example.invalid", 429, "limite", {}, io.BytesIO(b"")),
            HTTPError("https://example.invalid", 400, "sem rota", {}, io.BytesIO(b'{"code":"NoRoute"}')),
            TimeoutError("timeout"),
        ]
        for erro in erros:
            with self.subTest(erro=erro), patch("mapas.urlopen", side_effect=erro):
                with self.assertRaises(ErroMapa):
                    ServicoMapas().buscar_cidades("Campinas")
        resposta = self.resposta({})
        resposta.read.return_value = b"<html>indisponivel</html>"
        with patch("mapas.urlopen", return_value=resposta), self.assertRaises(ErroMapa):
            self.servico.buscar_cidades("Campinas")

    def test_photon_formato_invalido(self):
        for dados in [[], {"features": None}]:
            with self.subTest(dados=dados), patch("mapas.urlopen", return_value=self.resposta(dados)):
                with self.assertRaises(ErroMapa):
                    ServicoMapas().buscar_cidades("Campinas")

    def test_osrm_rejeita_rota_ausente_unidades_invalidas_e_geometria_invalida(self):
        respostas = [{"code": "NoRoute"}, {"code": "NoSegment"}, {"code": "Ok", "routes": []}]
        for campo, valor in [("distance", 0), ("duration", -1), ("distance", float("inf"))]:
            dados = self.percurso()
            dados["routes"][0][campo] = valor
            respostas.append(dados)
        for geometria in [
            {"type": "Point", "coordinates": [[0, 0], [1, 1]]},
            {"type": "LineString", "coordinates": [[0, 0]]},
            {"type": "LineString", "coordinates": [[0, 0], [181, 0]]},
        ]:
            dados = self.percurso()
            dados["routes"][0]["geometry"] = geometria
            respostas.append(dados)
        for dados in respostas:
            with self.subTest(dados=dados), patch("mapas.urlopen", return_value=self.resposta(dados)):
                with self.assertRaises(ErroMapa):
                    ServicoMapas().consultar_estrada(*self.extremos())


class TestMapaHtml(unittest.TestCase):
    @staticmethod
    def dados_html(conteudo):
        serializado = conteudo.split("const dados = ", 1)[1].split(";\nconst aviso", 1)[0]
        return json.loads(serializado)

    def test_nome_de_cidade_nao_fecha_script(self):
        nome = '</script><script>alert("teste")</script>&\u2028fim'
        grafo = Grafo()
        grafo.adicionar_cidade(nome, 0, 0)
        with TemporaryDirectory() as pasta:
            arquivo = Path(pasta) / "mapa.html"
            self.assertEqual(gerar_mapa(grafo, arquivo), arquivo.resolve())
            conteudo = arquivo.read_text(encoding="utf-8")

        self.assertNotIn(nome, conteudo)
        self.assertEqual(conteudo.lower().count("</script>"), 2)
        self.assertIn("\\u003c/script\\u003e", conteudo)
        self.assertIn("\\u0026", conteudo)
        self.assertIn("\\u2028", conteudo)
        self.assertEqual(self.dados_html(conteudo)["cidades"][0]["nome"], nome)

    def test_destaque_inverte_geometria_e_preserva_pontos_intermediarios(self):
        grafo = Grafo()
        grafo.adicionar_cidade("A", 0, 0)
        grafo.adicionar_cidade("B", 1, 1)
        grafo.adicionar_cidade("Isolada")
        grafo.adicionar_estrada("A", "B", 1, 2, 3, geometria=((0, 0), (0.2, 0.7), (1, 1)))
        rota = grafo.menor_rota("B", "A")
        with TemporaryDirectory() as pasta:
            arquivo = gerar_mapa(grafo, Path(pasta) / "mapa.html", rota)
            dados = self.dados_html(arquivo.read_text(encoding="utf-8"))

        self.assertEqual(dados["semCoordenadas"], 1)
        self.assertEqual(dados["rota"]["cidades"], ["B", "A"])
        self.assertEqual(len(dados["destaque"]), 1)
        trecho = dados["destaque"][0]
        self.assertEqual((trecho["origem"], trecho["destino"]), ("B", "A"))
        self.assertEqual(trecho["pontos"], [[1, 1], [0.2, 0.7], [0, 0]])
        self.assertFalse(trecho["bidirecional"])

    def test_servidor_publica_somente_o_mapa(self):
        with TemporaryDirectory() as pasta:
            arquivo = Path(pasta) / "mapa.html"
            arquivo.write_text("<html>Mapa de teste</html>", encoding="utf-8")
            (Path(pasta) / "segredo.txt").write_text("não publicar", encoding="utf-8")
            servidor = ServidorMapa(arquivo)
            try:
                endereco = urlsplit(servidor.url)
                self.assertEqual(endereco.hostname, "127.0.0.1")
                base = f"{endereco.scheme}://{endereco.netloc}"
                for caminho in ["/", "/mapa"]:
                    with self.subTest(caminho=caminho), urlopen(base + caminho, timeout=3) as resposta:
                        self.assertEqual(resposta.read(), arquivo.read_bytes())
                        self.assertEqual(resposta.headers["Cache-Control"], "no-store")
                        self.assertEqual(resposta.headers["X-Content-Type-Options"], "nosniff")
                for caminho in ["/segredo.txt", "/../segredo.txt", "/mapa.html"]:
                    with self.subTest(caminho=caminho):
                        with self.assertRaises(HTTPError) as erro:
                            urlopen(base + caminho, timeout=3)
                        self.assertEqual(erro.exception.code, 404)
                        erro.exception.close()
                with urlopen(base + "/favicon.ico", timeout=3) as resposta:
                    self.assertEqual(resposta.status, 204)
            finally:
                servidor.fechar()
                servidor.fechar()


if __name__ == "__main__":
    unittest.main()
