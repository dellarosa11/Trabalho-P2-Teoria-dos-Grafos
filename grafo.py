"""Grafo ponderado com coordenadas e estradas de um ou dois sentidos."""

from __future__ import annotations

from dataclasses import dataclass
import heapq
import math


@dataclass(frozen=True)
class Estrada:
    origem: str
    destino: str
    distancia: float
    custo: float
    tempo: float
    bidirecional: bool = True
    geometria: tuple[tuple[float, float], ...] = ()


@dataclass(frozen=True)
class Rota:
    cidades: list[str]
    distancia: float
    custo: float
    tempo: float


def validar_coordenadas(latitude: float, longitude: float) -> tuple[float, float]:
    """Valida se latitude (-90 a 90) e longitude (-180 a 180) são números finitos válidos."""
    ponto = []
    for valor, campo, limite in ((latitude, "Latitude", 90), (longitude, "Longitude", 180)):
        mensagem = f"{campo} deve ser um número finito entre {-limite} e {limite}."
        if isinstance(valor, bool):
            raise ValueError(mensagem)
        try:
            numero = float(valor)
        except (TypeError, ValueError, OverflowError) as erro:
            raise ValueError(mensagem) from erro
        if not math.isfinite(numero) or not -limite <= numero <= limite:
            raise ValueError(mensagem)
        ponto.append(numero)
    return ponto[0], ponto[1]


class Grafo:
    def __init__(self) -> None:
        self._adjacencia: dict[str, dict[str, Estrada]] = {}
        self._nomes: dict[str, str] = {}
        self._coordenadas: dict[str, tuple[float, float]] = {}

    @property
    def cidades(self) -> list[str]:
        return sorted(self._adjacencia, key=str.casefold)

    @property
    def coordenadas(self) -> dict[str, tuple[float, float]]:
        return self._coordenadas.copy()

    @property
    def estradas(self) -> list[Estrada]:
        return sorted(
            (
                estrada
                for cidade, vizinhos in self._adjacencia.items()
                for estrada in vizinhos.values()
                if estrada.origem == cidade
            ),
            key=lambda estrada: (estrada.origem.casefold(), estrada.destino.casefold()),
        )

    @staticmethod
    def _validar_nome(nome: str) -> str:
        if not isinstance(nome, str) or not nome.strip():
            raise ValueError("Informe um nome de cidade válido.")
        return nome.strip()

    def _resolver_cidade(self, nome: str) -> str:
        nome = self._validar_nome(nome)
        cidade = self._nomes.get(nome.casefold())
        if cidade is None:
            raise ValueError(f"A cidade '{nome}' não está cadastrada.")
        return cidade

    @staticmethod
    def _validar_criterio(criterio: str) -> None:
        if criterio not in ("distancia", "custo", "tempo"):
            raise ValueError("O critério deve ser distancia, custo ou tempo.")

    @staticmethod
    def _validar_peso(valor: float, campo: str, permite_zero: bool = False) -> float:
        regra = "maior ou igual a zero" if permite_zero else "maior que zero"
        mensagem = f"{campo} deve ser um número finito {regra}."
        if isinstance(valor, bool):
            raise ValueError(mensagem)
        try:
            numero = float(valor)
        except (TypeError, ValueError, OverflowError) as erro:
            raise ValueError(mensagem) from erro
        if not math.isfinite(numero) or numero < 0 or (numero == 0 and not permite_zero):
            raise ValueError(mensagem)
        return numero

    _validar_ponto = staticmethod(validar_coordenadas)

    @classmethod
    def _validar_geometria(cls, geometria) -> tuple[tuple[float, float], ...]:
        if not isinstance(geometria, (list, tuple)):
            raise ValueError("A geometria deve ser uma lista de pares de latitude e longitude.")
        if len(geometria) == 1:
            raise ValueError("A geometria deve ter pelo menos dois pontos ou estar vazia.")
        pontos = []
        for ponto in geometria:
            if not isinstance(ponto, (list, tuple)) or len(ponto) != 2:
                raise ValueError("Cada ponto da geometria deve conter latitude e longitude.")
            pontos.append(cls._validar_ponto(*ponto))
        return tuple(pontos)

    def adicionar_cidade(
        self, nome: str, latitude: float | None = None, longitude: float | None = None
    ) -> None:
        nome = self._validar_nome(nome)
        if nome.casefold() in self._nomes:
            raise ValueError(f"A cidade '{nome}' já está cadastrada.")
        ponto = None
        if latitude is not None or longitude is not None:
            ponto = self._validar_ponto(latitude, longitude)
        self._adjacencia[nome] = {}
        self._nomes[nome.casefold()] = nome
        if ponto is not None:
            self._coordenadas[nome] = ponto

    def definir_coordenadas(self, nome: str, latitude: float, longitude: float) -> None:
        nome = self._resolver_cidade(nome)
        self._coordenadas[nome] = self._validar_ponto(latitude, longitude)

    def limpar(self) -> None:
        """Remove todas as cidades, estradas e coordenadas do grafo."""
        self._adjacencia.clear()
        self._nomes.clear()
        self._coordenadas.clear()

    def adicionar_ou_atualizar_cidade(
        self, nome: str, latitude: float | None = None, longitude: float | None = None
    ) -> None:
        """Cadastra a cidade ou atualiza suas coordenadas caso já exista."""
        nome_limpo = self._validar_nome(nome)
        if nome_limpo.casefold() in self._nomes:
            if latitude is not None and longitude is not None:
                self.definir_coordenadas(nome_limpo, latitude, longitude)
        else:
            self.adicionar_cidade(nome_limpo, latitude, longitude)

    def remover_cidade(self, nome: str) -> None:
        """Remove a cidade do grafo, suas coordenadas e todas as estradas ligadas a ela."""
        cidade = self._resolver_cidade(nome)
        del self._adjacencia[cidade]
        del self._nomes[cidade.casefold()]
        self._coordenadas.pop(cidade, None)
        for vizinhos in self._adjacencia.values():
            vizinhos.pop(cidade, None)

    def remover_estrada(self, origem: str, destino: str) -> None:
        """Remove a estrada entre duas cidades."""
        origem_res = self._resolver_cidade(origem)
        destino_res = self._resolver_cidade(destino)
        estrada = self._adjacencia[origem_res].pop(destino_res, None)
        if estrada is not None and estrada.bidirecional:
            self._adjacencia[destino_res].pop(origem_res, None)

    def obter_geometria(self, origem: str, destino: str) -> tuple[tuple[float, float], ...]:
        """Retorna a geometria cadastrada para a ligação entre origem e destino, se houver."""
        origem_res = self._nomes.get(self._validar_nome(origem).casefold()) if isinstance(origem, str) and origem.strip() else None
        destino_res = self._nomes.get(self._validar_nome(destino).casefold()) if isinstance(destino, str) and destino.strip() else None
        if not origem_res or not destino_res:
            return ()
        estrada = self._adjacencia.get(origem_res, {}).get(destino_res)
        if estrada is not None:
            if estrada.origem == origem_res:
                return estrada.geometria
            return estrada.geometria[::-1]
        return ()

    def adicionar_estrada(
        self,
        origem: str,
        destino: str,
        distancia: float,
        custo: float,
        tempo: float,
        bidirecional: bool = True,
        geometria: tuple[tuple[float, float], ...] = (),
    ) -> None:
        """Atualiza o sentido solicitado, preservando um retorno já cadastrado."""
        origem = self._resolver_cidade(origem)
        destino = self._resolver_cidade(destino)
        if origem == destino:
            raise ValueError("A estrada deve ligar duas cidades diferentes.")
        distancia = self._validar_peso(distancia, "A distância")
        custo = self._validar_peso(custo, "O custo", permite_zero=True)
        tempo = self._validar_peso(tempo, "O tempo")
        if not isinstance(bidirecional, bool):
            raise ValueError("O campo bidirecional deve ser verdadeiro ou falso.")
        geometria = self._validar_geometria(geometria)
        if bidirecional:
            if origem.casefold() > destino.casefold():
                origem, destino = destino, origem
                geometria = geometria[::-1]
        else:
            anterior = self._adjacencia[origem].get(destino)
            if anterior is not None and anterior.bidirecional:
                geometria_retorno = anterior.geometria
                if anterior.origem != destino:
                    geometria_retorno = geometria_retorno[::-1]
                self._adjacencia[destino][origem] = Estrada(
                    destino, origem, anterior.distancia, anterior.custo,
                    anterior.tempo, False, geometria_retorno,
                )
        estrada = Estrada(origem, destino, distancia, custo, tempo, bidirecional, geometria)
        self._adjacencia[origem][destino] = estrada
        if bidirecional:
            self._adjacencia[destino][origem] = estrada

    def lista_adjacencia(self, criterio: str = "distancia") -> dict[str, list[tuple[str, float]]]:
        self._validar_criterio(criterio)
        return {
            cidade: [
                (vizinho, getattr(self._adjacencia[cidade][vizinho], criterio))
                for vizinho in sorted(self._adjacencia[cidade], key=str.casefold)
            ]
            for cidade in self.cidades
        }

    def matriz_adjacencia(
        self, criterio: str = "distancia"
    ) -> tuple[list[str], list[list[float]]]:
        self._validar_criterio(criterio)
        cidades = self.cidades
        matriz = []
        for origem in cidades:
            linha = []
            for destino in cidades:
                estrada = self._adjacencia[origem].get(destino)
                peso = getattr(estrada, criterio) if estrada is not None else math.inf
                linha.append(0.0 if origem == destino else peso)
            matriz.append(linha)
        return cidades, matriz

    def menor_rota(
        self, origem: str, destino: str, criterio: str = "distancia"
    ) -> Rota | None:
        self._validar_criterio(criterio)
        origem = self._resolver_cidade(origem)
        destino = self._resolver_cidade(destino)
        distancias = {origem: 0.0}
        anteriores: dict[str, str] = {}
        fila = [(0.0, origem)]

        while fila:
            peso_atual, cidade = heapq.heappop(fila)
            if peso_atual != distancias[cidade]:
                continue
            if cidade == destino:
                caminho = [destino]
                while caminho[-1] != origem:
                    caminho.append(anteriores[caminho[-1]])
                caminho.reverse()
                trechos = [
                    self._adjacencia[inicio][fim]
                    for inicio, fim in zip(caminho, caminho[1:])
                ]
                return Rota(
                    cidades=caminho,
                    distancia=sum((trecho.distancia for trecho in trechos), 0.0),
                    custo=sum((trecho.custo for trecho in trechos), 0.0),
                    tempo=sum((trecho.tempo for trecho in trechos), 0.0),
                )
            for vizinho, estrada in self._adjacencia[cidade].items():
                novo_peso = peso_atual + getattr(estrada, criterio)
                if vizinho not in distancias or novo_peso < distancias[vizinho]:
                    distancias[vizinho] = novo_peso
                    anteriores[vizinho] = cidade
                    heapq.heappush(fila, (novo_peso, vizinho))
        return None

    def para_dict(self) -> dict:
        return {
            "cidades": self.cidades,
            "coordenadas": {
                cidade: list(self._coordenadas[cidade])
                for cidade in self.cidades if cidade in self._coordenadas
            },
            "estradas": [
                {
                    "origem": estrada.origem,
                    "destino": estrada.destino,
                    "distancia": estrada.distancia,
                    "custo": estrada.custo,
                    "tempo": estrada.tempo,
                    "bidirecional": estrada.bidirecional,
                    "geometria": [list(ponto) for ponto in estrada.geometria],
                }
                for estrada in self.estradas
            ],
        }

    @classmethod
    def de_dict(cls, dados: dict) -> "Grafo":
        if not isinstance(dados, dict):
            raise ValueError("Os dados do grafo devem ser um objeto.")
        if not isinstance(dados.get("cidades"), list) or not isinstance(dados.get("estradas"), list):
            raise ValueError("Os dados devem conter as listas 'cidades' e 'estradas'.")
        grafo = cls()
        for cidade in dados["cidades"]:
            grafo.adicionar_cidade(cidade)
        coordenadas = dados.get("coordenadas", {})
        if not isinstance(coordenadas, dict):
            raise ValueError("As coordenadas devem associar cidades a pares de latitude e longitude.")
        for cidade, ponto in coordenadas.items():
            if not isinstance(ponto, (list, tuple)) or len(ponto) != 2:
                raise ValueError("Cada coordenada deve conter latitude e longitude.")
            grafo.definir_coordenadas(cidade, *ponto)
        campos = ("origem", "destino", "distancia", "custo", "tempo")
        for estrada in dados["estradas"]:
            if not isinstance(estrada, dict) or any(campo not in estrada for campo in campos):
                raise ValueError("Cada estrada deve conter origem, destino, distancia, custo e tempo.")
            grafo.adicionar_estrada(
                *(estrada[campo] for campo in campos),
                bidirecional=estrada.get("bidirecional", True),
                geometria=estrada.get("geometria", ()),
            )
        return grafo
