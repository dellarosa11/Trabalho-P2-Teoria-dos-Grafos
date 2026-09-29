import math
import unittest

from grafo import Grafo


class TestGrafo(unittest.TestCase):
    def criar_grafo(self, *cidades):
        grafo = Grafo()
        for cidade in cidades:
            grafo.adicionar_cidade(cidade)
        return grafo

    def test_melhor_rota_por_metrica_e_totais(self):
        grafo = self.criar_grafo("A", "B", "C", "D")
        grafo.adicionar_estrada("A", "B", 2, 9, 6)
        grafo.adicionar_estrada("B", "D", 2, 9, 6)
        grafo.adicionar_estrada("A", "C", 5, 1, 4)
        grafo.adicionar_estrada("C", "D", 5, 1, 4)
        grafo.adicionar_estrada("A", "D", 20, 30, 1)

        casos = [
            ("distancia", ["A", "B", "D"], (4, 18, 12)),
            ("custo", ["A", "C", "D"], (10, 2, 8)),
            ("tempo", ["A", "D"], (20, 30, 1)),
        ]
        for criterio, cidades, totais in casos:
            with self.subTest(criterio=criterio):
                rota = grafo.menor_rota("A", "D", criterio)
                self.assertIsNotNone(rota)
                self.assertEqual(rota.cidades, cidades)
                self.assertEqual((rota.distancia, rota.custo, rota.tempo), totais)

    def test_dijkstra_nao_escolhe_apenas_a_menor_aresta_local(self):
        grafo = self.criar_grafo("A", "B", "C", "D")
        grafo.adicionar_estrada("A", "B", 1, 1, 1)
        grafo.adicionar_estrada("B", "D", 100, 1, 1)
        grafo.adicionar_estrada("A", "C", 4, 1, 1)
        grafo.adicionar_estrada("C", "D", 4, 1, 1)

        rota = grafo.menor_rota("A", "D")

        self.assertEqual(rota.cidades, ["A", "C", "D"])
        self.assertEqual(rota.distancia, 8)

    def test_representacoes_e_rota_bidirecional(self):
        grafo = self.criar_grafo("C", "B", "A")
        grafo.adicionar_estrada("A", "B", 7, 2, 3)

        self.assertEqual(grafo.cidades, ["A", "B", "C"])
        self.assertEqual(
            grafo.lista_adjacencia(),
            {"A": [("B", 7)], "B": [("A", 7)], "C": []},
        )
        cidades, matriz = grafo.matriz_adjacencia()
        self.assertEqual(cidades, ["A", "B", "C"])
        self.assertEqual(
            matriz,
            [[0, 7, math.inf], [7, 0, math.inf], [math.inf, math.inf, 0]],
        )
        rota = grafo.menor_rota("B", "A")
        self.assertEqual(rota.cidades, ["B", "A"])
        self.assertEqual((rota.distancia, rota.custo, rota.tempo), (7, 2, 3))

    def test_cidades_desconectadas(self):
        grafo = self.criar_grafo("A", "B")

        self.assertIsNone(grafo.menor_rota("A", "B"))

    def test_origem_igual_destino(self):
        grafo = self.criar_grafo("A")

        rota = grafo.menor_rota("A", "A")

        self.assertEqual(rota.cidades, ["A"])
        self.assertEqual((rota.distancia, rota.custo, rota.tempo), (0, 0, 0))

    def test_atualizacao_da_estrada_sem_duplicar(self):
        grafo = self.criar_grafo("A", "B")
        grafo.adicionar_estrada("A", "B", 10, 20, 30)

        grafo.adicionar_estrada("B", "A", 4, 5, 6)

        self.assertEqual(len(grafo.estradas), 1)
        for origem, destino in [("A", "B"), ("B", "A")]:
            with self.subTest(origem=origem):
                rota = grafo.menor_rota(origem, destino)
                self.assertEqual((rota.distancia, rota.custo, rota.tempo), (4, 5, 6))
        self.assertEqual(grafo.lista_adjacencia(), {"A": [("B", 4)], "B": [("A", 4)]})

    def test_rejeita_pesos_negativos_ou_nao_finitos(self):
        grafo = self.criar_grafo("A", "B")
        for campo in range(3):
            for valor in [-1, math.nan, math.inf, -math.inf]:
                with self.subTest(campo=campo, valor=valor):
                    pesos = [1, 1, 1]
                    pesos[campo] = valor
                    with self.assertRaises(ValueError):
                        grafo.adicionar_estrada("A", "B", *pesos)
                    self.assertEqual(len(grafo.estradas), 0)

    def test_aceita_custo_zero(self):
        grafo = self.criar_grafo("A", "B", "C")
        grafo.adicionar_estrada("A", "B", 1, 0, 1)
        grafo.adicionar_estrada("B", "C", 1, 0, 2)

        rota = grafo.menor_rota("A", "C", "custo")

        self.assertEqual(rota.cidades, ["A", "B", "C"])
        self.assertEqual((rota.distancia, rota.custo, rota.tempo), (2, 0, 3))

    def test_rejeita_cidade_inexistente(self):
        grafo = self.criar_grafo("A")
        for origem, destino in [("A", "B"), ("B", "A"), ("B", "B")]:
            with self.subTest(origem=origem, destino=destino):
                with self.assertRaises(ValueError):
                    grafo.adicionar_estrada(origem, destino, 1, 1, 1)
                with self.assertRaises(ValueError):
                    grafo.menor_rota(origem, destino)

    def test_serializacao_preserva_cidades_isoladas_e_estradas(self):
        grafo = self.criar_grafo("São Paulo", "Campinas", "Cidade isolada")
        grafo.adicionar_estrada("São Paulo", "Campinas", 99.5, 12.4, 1.25)

        restaurado = Grafo.de_dict(grafo.para_dict())

        self.assertEqual(restaurado.para_dict(), grafo.para_dict())
        self.assertEqual(restaurado.lista_adjacencia(), grafo.lista_adjacencia())
        self.assertIsNone(restaurado.menor_rota("São Paulo", "Cidade isolada"))
        rota = restaurado.menor_rota("Campinas", "São Paulo")
        self.assertEqual(rota.cidades, ["Campinas", "São Paulo"])
        self.assertEqual((rota.distancia, rota.custo, rota.tempo), (99.5, 12.4, 1.25))

    def test_coordenadas_validacao_e_atualizacao(self):
        grafo = Grafo()
        pontos_invalidos = [
            (91, 0), (-91, 0), (0, 181), (0, -181),
            (math.nan, 0), (0, math.inf), (True, 0), (0, None),
        ]
        for ponto in pontos_invalidos:
            with self.subTest(ponto=ponto):
                with self.assertRaises(ValueError):
                    grafo.adicionar_cidade("A", *ponto)
                self.assertEqual(grafo.cidades, [])

        grafo.adicionar_cidade("A", -90, 180)
        grafo.definir_coordenadas(" a ", -23.5, -46.6)
        self.assertEqual(grafo.coordenadas, {"A": (-23.5, -46.6)})
        copia = grafo.coordenadas
        copia["A"] = (0, 0)
        self.assertEqual(grafo.coordenadas["A"], (-23.5, -46.6))
        with self.assertRaises(ValueError):
            grafo.definir_coordenadas("A", 100, 0)
        self.assertEqual(grafo.coordenadas["A"], (-23.5, -46.6))
        with self.assertRaises(ValueError):
            grafo.definir_coordenadas("B", 0, 0)

    def test_geometria_invalida_nao_altera_estrada(self):
        grafo = self.criar_grafo("A", "B")
        grafo.adicionar_estrada("A", "B", 1, 2, 3)
        original = grafo.para_dict()
        for geometria in [None, "0,0", [(0, 0)], [(0, 0), (1,)],
                          [(0, 0), (91, 0)], [(0, 0), (0, math.nan)]]:
            with self.subTest(geometria=geometria):
                with self.assertRaises(ValueError):
                    grafo.adicionar_estrada(
                        "A", "B", 4, 5, 6, bidirecional=False, geometria=geometria
                    )
                self.assertEqual(grafo.para_dict(), original)
        with self.assertRaises(ValueError):
            grafo.adicionar_estrada("A", "B", 4, 5, 6, bidirecional="false")
        self.assertEqual(grafo.para_dict(), original)

    def test_estrada_direcionada_nao_permite_retorno(self):
        grafo = self.criar_grafo("A", "B", "C")
        grafo.adicionar_estrada("A", "B", 2, 3, 4, bidirecional=False)
        grafo.adicionar_estrada("B", "C", 5, 6, 7, bidirecional=False)

        rota = grafo.menor_rota("A", "C")

        self.assertEqual(rota.cidades, ["A", "B", "C"])
        self.assertEqual((rota.distancia, rota.custo, rota.tempo), (7, 9, 11))
        self.assertIsNone(grafo.menor_rota("C", "A"))
        self.assertEqual(grafo.lista_adjacencia(), {"A": [("B", 2)], "B": [("C", 5)], "C": []})
        self.assertEqual(
            grafo.matriz_adjacencia()[1],
            [[0, 2, math.inf], [math.inf, 0, 5], [math.inf, math.inf, 0]],
        )

    def test_geometria_bidirecional_acompanha_ordem_das_cidades(self):
        grafo = self.criar_grafo("A", "B")
        geometria_b_a = ((1, 1), (0.5, 0.5), (0, 0))

        grafo.adicionar_estrada("B", "A", 1, 2, 3, geometria=geometria_b_a)

        estrada = grafo.estradas[0]
        self.assertEqual((estrada.origem, estrada.destino), ("A", "B"))
        self.assertEqual(estrada.geometria, geometria_b_a[::-1])

    def test_substituir_um_sentido_preserva_retorno_e_sua_geometria(self):
        geometria_a_b = ((0, 0), (0.5, 0.5), (1, 1))
        for origem, destino in [("A", "B"), ("B", "A")]:
            with self.subTest(origem=origem):
                grafo = self.criar_grafo("A", "B")
                grafo.adicionar_estrada("A", "B", 1, 2, 3, geometria=geometria_a_b)
                nova_geometria = ((0, 0), (0.4, 0.6), (1, 1))
                if origem == "B":
                    nova_geometria = nova_geometria[::-1]

                grafo.adicionar_estrada(
                    origem, destino, 4, 5, 6, bidirecional=False, geometria=nova_geometria
                )

                self.assertEqual(len(grafo.estradas), 2)
                estradas = {(e.origem, e.destino): e for e in grafo.estradas}
                ida = estradas[(origem, destino)]
                volta = estradas[(destino, origem)]
                self.assertFalse(ida.bidirecional)
                self.assertFalse(volta.bidirecional)
                self.assertEqual(ida.geometria, nova_geometria)
                self.assertEqual(
                    volta.geometria,
                    geometria_a_b[::-1] if destino == "B" else geometria_a_b,
                )
                ida_rota = grafo.menor_rota(origem, destino)
                volta_rota = grafo.menor_rota(destino, origem)
                self.assertEqual((ida_rota.distancia, ida_rota.custo, ida_rota.tempo), (4, 5, 6))
                self.assertEqual((volta_rota.distancia, volta_rota.custo, volta_rota.tempo), (1, 2, 3))
                self.assertEqual(Grafo.de_dict(grafo.para_dict()).para_dict(), grafo.para_dict())

    def test_serializacao_de_coordenadas_geometria_e_sentidos(self):
        grafo = Grafo()
        grafo.adicionar_cidade("A", -23.5, -46.6)
        grafo.adicionar_cidade("B", -22.9, -47.1)
        grafo.adicionar_cidade("Sem coordenadas")
        grafo.adicionar_estrada(
            "A", "B", 99, 10, 1, bidirecional=False,
            geometria=[[-23.5, -46.6], [-23.1, -46.8], [-22.9, -47.1]],
        )

        restaurado = Grafo.de_dict(grafo.para_dict())

        self.assertEqual(restaurado.para_dict(), grafo.para_dict())
        self.assertEqual(restaurado.coordenadas, grafo.coordenadas)
        self.assertEqual(restaurado.estradas, grafo.estradas)
        self.assertIsNone(restaurado.menor_rota("B", "A"))

    def test_carrega_formato_legado_sem_coordenadas_ou_sentido(self):
        grafo = Grafo.de_dict({
            "cidades": ["A", "B"],
            "estradas": [{"origem": "B", "destino": "A", "distancia": 1, "custo": 2, "tempo": 3}],
        })

        self.assertEqual(grafo.coordenadas, {})
        self.assertTrue(grafo.estradas[0].bidirecional)
        self.assertEqual(grafo.estradas[0].geometria, ())
        self.assertEqual(grafo.menor_rota("A", "B").cidades, ["A", "B"])
        self.assertEqual(grafo.menor_rota("B", "A").cidades, ["B", "A"])


if __name__ == "__main__":
    unittest.main()
