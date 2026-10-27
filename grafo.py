"""Modelo do mapa e algoritmo de menor caminho do projeto.

Na linguagem da Teoria dos Grafos, cada cidade é um vértice e cada estrada
é uma aresta. As arestas possuem três pesos: distância em quilômetros,
custo em reais e tempo em minutos. Uma busca usa somente um desses pesos
como critério; os outros dois são somados para informar os totais da rota.

O grafo pode misturar estradas de mão única (arestas direcionadas) e de
dois sentidos. Ele usa uma lista de adjacência implementada com dicionários:
para cada cidade, guarda apenas os vizinhos aos quais existe ligação direta.
As coordenadas e a geometria servem para desenhar o mapa; o Dijkstra usa
os pesos cadastrados, sem calcular distâncias a partir dessas coordenadas.
"""

from __future__ import annotations

from dataclasses import dataclass
import heapq
import math


@dataclass(frozen=True)
class Estrada:
    """Dados de uma aresta que liga duas cidades já cadastradas.

    ``distancia`` está em km, ``custo`` em R$ e ``tempo`` em minutos.
    ``bidirecional=True`` permite percorrer a mesma estrada nos dois sentidos;
    ``False`` permite somente ``origem -> destino``. ``geometria`` contém
    pares (latitude, longitude) ordenados da origem para o destino e é usada
    no desenho da rodovia, sem interferir no cálculo do menor caminho.

    ``frozen=True`` impede reatribuir os campos após a criação. As validações
    dos dados são feitas por ``Grafo.adicionar_estrada``, não pela dataclass.
    """

    origem: str
    destino: str
    distancia: float
    custo: float
    tempo: float
    bidirecional: bool = True
    geometria: tuple[tuple[float, float], ...] = ()


@dataclass(frozen=True)
class Rota:
    """Resultado da busca: sequência de cidades e totais dos seus trechos.

    ``cidades`` inclui a origem, os pontos intermediários e o destino.
    Os totais são distância em km, custo em R$ e tempo em minutos. Apenas
    o critério escolhido em ``menor_rota`` é minimizado: a menor distância
    não garante, ao mesmo tempo, o menor custo ou o menor tempo.

    Os campos não podem ser reatribuídos, mas a lista ``cidades`` continua
    sendo uma lista mutável; ``frozen=True`` não congela seus elementos.
    """

    cidades: list[str]
    distancia: float
    custo: float
    tempo: float


def validar_coordenadas(latitude: float, longitude: float) -> tuple[float, float]:
    """Converte e devolve um par geográfico válido em graus decimais.

    A latitude deve ficar entre -90 e 90; a longitude, entre -180 e 180.
    Aceita valores conversíveis para ``float`` e rejeita booleanos, infinito,
    NaN e coordenadas fora dos limites, lançando ``ValueError``. Essa função
    valida um ponto do mapa; ela não mede a distância entre cidades.
    """
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
    """Guarda cidades, suas conexões e operações sobre a rede de estradas.

    ``_adjacencia[A][B]`` representa a ligação direta que permite sair de A
    e chegar a B. Se a estrada for bidirecional, as entradas A -> B e B -> A
    apontam para o mesmo objeto ``Estrada``. Em uma mão única, a ligação
    inversa só existe se tiver sido cadastrada separadamente.

    Há no máximo uma ligação por par ordenado de cidades: um novo cadastro
    substitui o anterior. Não são permitidas estradas de uma cidade para
    ela mesma. A representação ocupa espaço proporcional às cidades e às
    conexões existentes, ao contrário da matriz, que reserva V x V posições.
    """

    def __init__(self) -> None:
        """Cria um grafo vazio e os três índices usados pelas demais operações.

        A adjacência guarda as ligações, ``_nomes`` permite encontrar cidades
        sem diferenciar maiúsculas de minúsculas e ``_coordenadas`` guarda
        apenas as cidades que possuem localização geográfica cadastrada.
        """
        # Dicionário principal de adjacência: {origem: {destino: Estrada}}
        self._adjacencia: dict[str, dict[str, Estrada]] = {}
        # casefold normaliza maiúsculas/minúsculas, sem remover acentos.
        # Exemplo: 'são paulo' -> 'São Paulo', preservando a grafia cadastrada.
        self._nomes: dict[str, str] = {}
        # Coordenadas geográficas: {cidade: (latitude, longitude)}
        self._coordenadas: dict[str, tuple[float, float]] = {}

    # @property permite consultar grafo.cidades, sem chamar grafo.cidades().
    @property
    def cidades(self) -> list[str]:
        """Devolve uma nova lista com os nomes dos vértices em ordem alfabética.

        A ordenação ignora diferenças entre maiúsculas e minúsculas. Alterar
        essa lista retornada não adiciona nem remove cidades do grafo.
        """
        return sorted(self._adjacencia, key=str.casefold)

    @property
    def coordenadas(self) -> dict[str, tuple[float, float]]:
        """Devolve uma cópia de ``{cidade: (latitude, longitude)}``, em graus.

        Uma cidade sem coordenadas não aparece nesse dicionário, mas ainda
        pode participar das rotas. A cópia evita alterar o cadastro interno
        ao adicionar ou remover itens no resultado.
        """
        return self._coordenadas.copy()

    @property
    def estradas(self) -> list[Estrada]:
        """Lista as estradas ordenadas por origem e destino, sem repetir ida e volta.

        Uma estrada bidirecional está em duas listas de vizinhos, mas o
        filtro ``estrada.origem == cidade`` a inclui apenas uma vez.
        Duas mãos únicas opostas são objetos distintos e aparecem ambas.
        Esse formato também evita duplicar as estradas ao salvar o grafo.
        """
        return sorted(
            (
                estrada
                for cidade, vizinhos in self._adjacencia.items()
                for estrada in vizinhos.values()
                if estrada.origem == cidade
            ),
            key=lambda estrada: (estrada.origem.casefold(), estrada.destino.casefold()),
        )

    # @staticmethod identifica uma validação que não precisa de self ou cls.
    @staticmethod
    def _validar_nome(nome: str) -> str:
        """Exige texto não vazio e remove espaços somente das extremidades.

        Retorna o nome tratado ou lança ``ValueError``. A grafia e os
        espaços no meio do nome são preservados; a comparação entre nomes
        sem diferenciar maiúsculas e minúsculas acontece em outro passo.
        """
        if not isinstance(nome, str) or not nome.strip():
            raise ValueError("Informe um nome de cidade válido.")
        return nome.strip()

    def _resolver_cidade(self, nome: str) -> str:
        """Localiza o vértice e devolve sua grafia original do cadastro.

        Por exemplo, ``'  SÃO PAULO  '`` encontra ``'São Paulo'``. Usa o
        índice ``_nomes`` com ``casefold`` e lança ``ValueError`` se o nome
        for inválido ou a cidade não existir. Acentos continuam relevantes.
        """
        nome = self._validar_nome(nome)
        cidade = self._nomes.get(nome.casefold())
        if cidade is None:
            raise ValueError(f"A cidade '{nome}' não está cadastrada.")
        return cidade

    @staticmethod
    def _validar_criterio(criterio: str) -> None:
        """Confere qual peso será usado: ``distancia``, ``custo`` ou ``tempo``.

        A lista, a matriz e o Dijkstra consultam o mesmo atributo da estrada
        por meio de ``getattr``. Lança ``ValueError`` para qualquer outra
        opção; não combina nem converte as três unidades entre si.
        """
        if criterio not in ("distancia", "custo", "tempo"):
            raise ValueError("O critério deve ser distancia, custo ou tempo.")

    @staticmethod
    def _validar_peso(valor: float, campo: str, permite_zero: bool = False) -> float:
        """Converte um peso em ``float`` e rejeita valores inválidos ou negativos.

        Com ``permite_zero=False``, exige valor estritamente positivo
        (distância). Com ``True``, aceita zero (custo ou tempo). Booleanos,
        NaN e infinito geram ``ValueError`` com o nome de ``campo``.
        Pesos não negativos são necessários para a garantia do Dijkstra:
        continuar um caminho não pode diminuir seu peso acumulado.
        """
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

    # Reutiliza a validação pública como método estático, sem receber self.
    _validar_ponto = staticmethod(validar_coordenadas)

    # @classmethod recebe a própria classe (cls), não uma instância (self).
    @classmethod
    def _validar_geometria(cls, geometria) -> tuple[tuple[float, float], ...]:
        """Valida o traçado da estrada e devolve uma tupla de pontos geográficos.

        Aceita lista ou tupla vazia (traçado desconhecido) ou com pelo menos
        dois pares de latitude e longitude. Cada par passa pela validação
        de coordenadas; formatos incorretos geram ``ValueError``. ``cls``
        permite reutilizar o validador da classe sem precisar de uma instância.
        """
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
        """Cadastra um vértice inicialmente sem arestas de saída.

        O nome deve ser novo, inclusive ao comparar maiúsculas/minúsculas.
        As coordenadas são opcionais; se algum valor for informado, o par
        completo precisa ser válido. Nomes repetidos ou coordenadas inválidas
        geram ``ValueError`` antes de alterar os dicionários do grafo.
        """
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
        """Define a localização de um vértice existente, em graus decimais.

        Resolve o nome cadastrado e valida o par antes de substituir a
        localização. Essa mudança afeta o mapa, mas não recalcula pesos
        das estradas nem altera o resultado da busca por si só.
        """
        nome = self._resolver_cidade(nome)
        self._coordenadas[nome] = self._validar_ponto(latitude, longitude)

    def limpar(self) -> None:
        """Esvazia o grafo, removendo vértices, arestas e coordenadas.

        Os três dicionários são limpos juntos para não deixar nomes ou
        posições referentes a cidades que já não existem.
        """
        self._adjacencia.clear()
        self._nomes.clear()
        self._coordenadas.clear()

    def adicionar_ou_atualizar_cidade(
        self, nome: str, latitude: float | None = None, longitude: float | None = None
    ) -> None:
        """Reaproveita um vértice existente ou cadastra um novo.

        Se a cidade já existe, atualiza a localização somente quando
        latitude e longitude são ambas diferentes de ``None``; caso contrário,
        mantém a localização atual. Para uma cidade nova, delega o cadastro
        a ``adicionar_cidade``, que exige um par completo se houver coordenadas.
        """
        nome_limpo = self._validar_nome(nome)
        if nome_limpo.casefold() in self._nomes:
            if latitude is not None and longitude is not None:
                self.definir_coordenadas(nome_limpo, latitude, longitude)
        else:
            self.adicionar_cidade(nome_limpo, latitude, longitude)

    def remover_cidade(self, nome: str) -> None:
        """Remove um vértice existente e todas as ligações que entram ou saem dele.

        Apaga sua lista de saída, seu nome e sua posição. Depois percorre
        as demais listas para retirar também as arestas que chegavam à
        cidade, evitando conexões com um vértice que deixou de existir.
        """
        cidade = self._resolver_cidade(nome)
        del self._adjacencia[cidade]
        del self._nomes[cidade.casefold()]
        self._coordenadas.pop(cidade, None)
        for vizinhos in self._adjacencia.values():
            vizinhos.pop(cidade, None)

    def remover_estrada(self, origem: str, destino: str) -> None:
        """Apaga a ligação origem -> destino entre duas cidades existentes.

        Se ela era bidirecional, remove também a entrada de retorno que
        representava a mesma estrada. Se era mão única, uma eventual mão
        única inversa é preservada. Não faz nada quando a ligação não existe;
        nomes de cidades inexistentes ainda geram ``ValueError``.
        """
        origem_res = self._resolver_cidade(origem)
        destino_res = self._resolver_cidade(destino)
        estrada = self._adjacencia[origem_res].pop(destino_res, None)
        if estrada is not None and estrada.bidirecional:
            self._adjacencia[destino_res].pop(origem_res, None)

    def obter_geometria(self, origem: str, destino: str) -> tuple[tuple[float, float], ...]:
        """Obtém o traçado de uma ligação direta na direção solicitada.

        Devolve uma tupla vazia se algum nome for inválido, a cidade não
        existir, a ligação não existir ou não houver traçado cadastrado.
        Na volta de uma estrada bidirecional, inverte os pontos, pois o
        objeto compartilhado guarda a geometria no sentido de sua origem.
        Esse método não procura uma rota passando por cidades intermediárias.
        """
        try:
            origem_res = self._resolver_cidade(origem)
            destino_res = self._resolver_cidade(destino)
        except ValueError:
            return ()
        estrada = self._adjacencia[origem_res].get(destino_res)
        if estrada is None:
            return ()
        if estrada.origem == origem_res:
            return estrada.geometria
        return estrada.geometria[::-1]

    def adicionar_estrada(
        self,
        origem: str,
        destino: str,
        distancia: float,
        custo: float = 0.0,
        tempo: float = 0.0,
        bidirecional: bool = True,
        geometria: tuple[tuple[float, float], ...] = (),
    ) -> None:
        """Cadastra ou substitui a aresta entre duas cidades existentes.

        ``distancia`` é positiva e medida em km; ``custo`` (R$) e ``tempo``
        (minutos) podem ser zero. ``bidirecional=True`` permite ida e volta,
        enquanto ``False`` representa apenas origem -> destino. A geometria
        opcional guarda o traçado para desenho, sem alterar esses pesos.

        Para uma estrada de dois sentidos, normaliza origem e destino pela
        ordem dos nomes e inverte a geometria quando necessário. Isso dá uma
        orientação única ao objeto, que é compartilhado pelas duas entradas
        da adjacência. O percurso continua permitido em ambas as direções.

        Ao substituir só um sentido de uma estrada antes bidirecional,
        preserva o retorno como uma mão única independente, com os pesos
        antigos. Um novo cadastro bidirecional substitui os dois sentidos.
        A função rejeita laços (origem igual ao destino) e dados inválidos.
        """
        origem = self._resolver_cidade(origem)
        destino = self._resolver_cidade(destino)
        if origem == destino:
            raise ValueError("A estrada deve ligar duas cidades diferentes.")
        distancia = self._validar_peso(distancia, "A distância")
        custo = self._validar_peso(custo, "O custo", permite_zero=True)
        tempo = self._validar_peso(tempo, "O tempo", permite_zero=True)
        if not isinstance(bidirecional, bool):
            raise ValueError("O campo bidirecional deve ser verdadeiro ou falso.")
        geometria = self._validar_geometria(geometria)
        if bidirecional:
            # A orientação do objeto é padronizada; ela não restringe a viagem.
            if origem.casefold() > destino.casefold():
                origem, destino = destino, origem
                # Ao trocar as pontas, o traçado também precisa trocar de sentido.
                geometria = geometria[::-1]
        else:
            anterior = self._adjacencia[origem].get(destino)
            if anterior is not None and anterior.bidirecional:
                # Desfaz o compartilhamento: o sentido inverso vira outra aresta
                # para manter seus dados enquanto este sentido será atualizado.
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
            # Duas entradas de vizinhança apontam para um único objeto Estrada.
            self._adjacencia[destino][origem] = estrada

    # -----------------------------------------------------------------------
    # REPRESENTAÇÕES DO GRAFO (TEORIA DOS GRAFOS)
    # -----------------------------------------------------------------------

    def lista_adjacencia(self, criterio: str = "distancia") -> dict[str, list[tuple[str, float]]]:
        """Produz ``{cidade: [(vizinho, peso), ...]}`` com ligações diretas.

        O parâmetro ``criterio`` escolhe qual peso mostrar: distância em km,
        custo em R$ ou tempo em minutos. Cidades sem saídas aparecem com
        lista vazia; as cidades e seus vizinhos são ordenados para exibição.
        Exemplo: ``{'A': [('B', 5.0)], 'B': []}`` representa A -> B de peso 5,
        sem permitir B -> A. A lista não inclui caminhos indiretos como A -> C -> B.

        A quantidade de elementos representados é proporcional a V + A
        (vértices e ligações de saída), útil em redes com poucas conexões
        diante de todos os pares possíveis. A ordenação também tem custo
        de execução; essa função não é uma cópia sem processamento.
        """
        self._validar_criterio(criterio)
        return {
            cidade: [
                # getattr(estrada, criterio) lê o peso escolhido pelo seu nome.
                (vizinho, getattr(self._adjacencia[cidade][vizinho], criterio))
                for vizinho in sorted(self._adjacencia[cidade], key=str.casefold)
            ]
            for cidade in self.cidades
        }

    def matriz_adjacencia(
        self, criterio: str = "distancia"
    ) -> tuple[list[str], list[list[float]]]:
        """Devolve ``(cidades, matriz)`` para representar as ligações em V x V.

        A mesma lista ``cidades`` define a ordem das linhas e das colunas:
        ``matriz[i][j]`` é o peso da ligação direta cidades[i] -> cidades[j].
        A diagonal contém zero, pois ficar na própria cidade não exige
        deslocamento; as demais posições sem ligação recebem ``math.inf``.
        Infinito diferencia a ausência de estrada de um peso válido zero,
        possível para custo e tempo.

        Uma rede inteiramente bidirecional gera matriz simétrica; mãos únicas
        podem torná-la assimétrica. A construção e o armazenamento da tabela
        exigem O(V²), mesmo quando há poucas estradas. Ela mostra arestas
        diretas, não os pesos dos menores caminhos entre todos os pares.
        """
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

    # -----------------------------------------------------------------------
    # ALGORITMO DE DIJKSTRA (MENOR CAMINHO)
    # -----------------------------------------------------------------------

    def menor_rota(
        self, origem: str, destino: str, criterio: str = "distancia"
    ) -> Rota | None:
        """Encontra uma rota de peso mínimo usando o algoritmo de Dijkstra.

        ``criterio`` escolhe um único objetivo: ``distancia`` (km), ``custo``
        (R$) ou ``tempo`` (minutos). Os pesos não negativos validados no
        cadastro permitem confirmar a melhor distância de uma cidade ao
        retirá-la da fila com seu menor peso conhecido.

        Etapas principais:
        1. A origem começa com peso zero. Cidades ainda não descobertas
           ficam ausentes do dicionário, equivalendo a peso infinito.
        2. A fila de prioridade (min-heap) entrega o menor peso acumulado
           disponível; não se escolhe apenas a menor estrada isolada.
        3. Para cada vizinho, o relaxamento testa se passar pela cidade atual
           melhora o peso conhecido. Se melhorar, atualiza o peso, registra
           o antecessor e coloca uma nova entrada na fila.
        4. Ao retirar uma entrada válida do destino, segue os antecessores
           de trás para frente, inverte o caminho e soma os três tipos de peso.

        Exemplo: com A -> B = 10, A -> C = 3 e C -> B = 2, a descoberta
        inicial de B com peso 10 é substituída por A -> C -> B, de peso 5.
        A entrada antiga de B pode continuar no heap, mas será ignorada
        caso seja retirada depois de uma melhora.

        Retorna ``Rota`` com todas as cidades e totais, ou ``None`` quando
        não há caminho respeitando as direções das estradas. Se origem e
        destino forem iguais, retorna só essa cidade e totais zero. Nomes
        inexistentes e critérios inválidos geram ``ValueError``.

        Apenas o peso escolhido é ótimo; os outros totais descrevem esse
        mesmo percurso. Empates não são resolvidos por um segundo critério:
        um caminho de mesmo peso não substitui o que já foi descoberto.
        """
        self._validar_criterio(criterio)
        origem = self._resolver_cidade(origem)
        destino = self._resolver_cidade(destino)

        # "distancias" guarda o peso acumulado do critério escolhido, que
        # também pode ser custo ou tempo. Só inclui cidades já descobertas.
        distancias = {origem: 0.0}
        # anteriores[B] = C indica que a melhor chegada conhecida a B veio de C.
        # Esse mapa evita guardar cópias de todo o caminho dentro da fila.
        anteriores: dict[str, str] = {}
        # O heap ordena tuplas por (peso_acumulado, nome_da_cidade).
        # Nomes servem de desempate na fila, sem otimizar outro tipo de peso.
        fila = [(0.0, origem)]

        while fila:
            # heappop remove a cidade com o menor peso acumulado da fila.
            peso_atual, cidade = heapq.heappop(fila)

            # Não há remoção da entrada antiga ao melhorar uma cidade. Quando
            # ela aparece, a comparação detecta que está obsoleta e a ignora.
            if peso_atual != distancias[cidade]:
                continue

            # O destino é confirmado ao sair do heap com uma entrada válida,
            # não quando é descoberto pela primeira vez entre os vizinhos.
            if cidade == destino:
                caminho = [destino]
                while caminho[-1] != origem:
                    caminho.append(anteriores[caminho[-1]])
                # Até aqui: destino -> ... -> origem. O resultado é ida normal.
                caminho.reverse()

                # zip forma os pares consecutivos: [A, C, B] -> (A,C), (C,B).
                # Para origem == destino não há trechos; as três somas dão 0.
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

            # Só percorre as saídas existentes, respeitando estradas de mão única.
            for vizinho, estrada in self._adjacencia[cidade].items():
                # Proposta de rota: peso até a cidade + peso deste trecho.
                novo_peso = peso_atual + getattr(estrada, criterio)
                # Relaxar é aceitar uma descoberta ou uma melhora estrita.
                # O uso de < mantém o caminho anterior nos empates e evita
                # reinserções sem melhora, inclusive em ciclos de peso zero.
                if vizinho not in distancias or novo_peso < distancias[vizinho]:
                    distancias[vizinho] = novo_peso
                    anteriores[vizinho] = cidade
                    heapq.heappush(fila, (novo_peso, vizinho))

        # A fila esvaziou sem confirmar o destino: ele não é alcançável da
        # origem por essas ligações, embora ambas as cidades estejam cadastradas.
        return None

    # -----------------------------------------------------------------------
    # SERIALIZAÇÃO E PERSISTÊNCIA (JSON)
    # -----------------------------------------------------------------------

    def para_dict(self) -> dict:
        """Converte o cadastro em um dicionário compatível com o formato JSON.

        Inclui cidades, coordenadas existentes e todos os dados das estradas.
        As tuplas geográficas viram listas, e uma estrada bidirecional é
        emitida uma única vez pela propriedade ``estradas``. Essa função
        prepara os dados; quem chama decide onde e como salvar o arquivo.
        """
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
        """Reconstrói e devolve um novo grafo a partir dos dados de um JSON.

        Confere o formato básico e cadastra primeiro os vértices, depois
        suas coordenadas e finalmente as arestas, que dependem das cidades
        existentes. Reutiliza os métodos de cadastro para aplicar as mesmas
        validações usadas na edição manual. Dados inválidos geram ``ValueError``.

        ``cls()`` cria uma instância da classe que recebeu a chamada.
        Arquivos antigos sem ``bidirecional`` ou ``geometria`` recebem,
        respectivamente, ``True`` e uma sequência vazia. O método recebe
        o dicionário já carregado; ele não abre um arquivo por conta própria.
        """
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
