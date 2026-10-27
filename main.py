"""Sistema de Rotas entre Cidades — Teoria dos Grafos.

Como o programa se organiza:
  - grafo.py guarda as cidades (vértices), as estradas (arestas) e Dijkstra.
  - main.py recebe as ações do usuário e apresenta os resultados com Tkinter.
  - mapas.py consulta dados geográficos e desenha o resultado no navegador.

Abas:
  1. Calcular Rota  — seleciona origem/destino e executa Dijkstra.
  2. Gerenciar      — adiciona/remove cidades e estradas (manual ou via mapa real).
  3. Adjacência     — lista e matriz de adjacência do grafo.

Fluxo de uma busca: botão Calcular Rota -> _calcular_rota ->
Grafo.menor_rota -> objeto Rota -> texto na tela e, se solicitado, mapa.
As funções de interface chamam o algoritmo; a implementação dele fica em grafo.py.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
import tkinter as tk
from tkinter import messagebox, ttk
import webbrowser

from grafo import Grafo
from mapas import ErroMapa, ServicoMapas, ServidorMapa, gerar_mapa


# Usa a pasta deste código, mesmo quando o programa é iniciado de outra pasta.
ARQUIVO_DADOS = Path(__file__).with_name("rotas.json")

# Traduz o texto visível na interface para o atributo da aresta usado como peso.
# O modelo Grafo também aceita "custo", mas esta interface oferece km e minutos.
CRITERIOS = {
    "Menor distância (km)": "distancia",
    "Menor tempo (min)":    "tempo",
}

UNIDADES = {"distancia": "km", "tempo": "min"}


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def fmt(valor: float) -> str:
    """Retorna um número como texto com duas casas e vírgula decimal.

    Exemplo: fmt(12.5) produz '12,50'. A formatação é apenas visual;
    os pesos usados por Dijkstra continuam sendo números no grafo.
    """
    return f"{valor:.2f}".replace(".", ",")


def parse_float(texto: str, campo: str) -> float:
    """Converte um campo de texto em número finito maior ou igual a zero.

    Aceita vírgula ou ponto decimal. 'campo' identifica o dado na mensagem
    de ValueError. A exigência de distância/tempo estritamente positivos
    é feita depois por _salvar_estrada; esta função também aceita zero.
    """
    try:
        v = float(texto.strip().replace(",", "."))
    except (ValueError, AttributeError, OverflowError):
        raise ValueError(f"Valor inválido para «{campo}».")
    if not math.isfinite(v) or v < 0:
        raise ValueError(f"«{campo}» deve ser um número positivo.")
    return v


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Estima a distância em km pela superfície terrestre entre dois pontos.

    Recebe latitude e longitude dos dois pontos em graus. A fórmula de
    Haversine aproxima a Terra por uma esfera e calcula o arco mais curto
    entre eles. Não acompanha as rodovias nem executa uma busca no grafo;
    serve de estimativa quando a consulta rodoviária não funciona.
    """
    # Raio médio aproximado da Terra em km. As funções trigonométricas
    # recebem radianos, por isso os ângulos em graus são convertidos.
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Aplicação
# ---------------------------------------------------------------------------

class Aplicacao:
    """Conecta a janela Tkinter ao grafo, ao arquivo JSON e ao serviço de mapas.

    Cada método associado a um botão lê os campos da tela e chama o modelo
    Grafo. 'self' é esta instância da aplicação; nomes iniciados com '_'
    indicam métodos de apoio usados internamente pela interface.
    """

    def __init__(self, janela: tk.Tk, arquivo: Path | str = ARQUIVO_DADOS) -> None:
        """Carrega o cadastro de 'arquivo', cria as três abas e liga os eventos.

        'janela' é a janela principal já criada por main(). Se não existir
        arquivo, começa com um grafo vazio. Também prepara o estado das
        consultas de rede e a rotina de encerramento da aplicação.
        """
        self.janela = janela
        self.arquivo = Path(arquivo)
        self.grafo = Grafo()
        self.servico_mapas = ServicoMapas()
        self.servidor_mapa: ServidorMapa | None = None
        # A fila transporta o resultado da busca feita em outra thread até
        # a interface; a janela consulta a fila sem bloquear o usuário.
        self.fila_mapa: Queue = Queue()
        self.ocupado_mapa = False
        self.ultima_rota = None
        self.resultados_busca: list[dict] = []
        self._geometria_pendente: tuple | None = None
        self._consulta_estrada_seq = 0

        # de_dict reconstrói o grafo e valida o conteúdo lido do JSON.
        if self.arquivo.exists():
            try:
                self.grafo = Grafo.de_dict(
                    json.loads(self.arquivo.read_text(encoding="utf-8"))
                )
            except Exception as e:
                print(f"Aviso ao carregar {self.arquivo.name}: {e}")

        janela.title("Rotas entre Cidades — Dijkstra")
        janela.geometry("1000x680")
        janela.minsize(860, 580)
        self._estilos(janela)

        corpo = ttk.Frame(janela, padding=14)
        corpo.pack(fill="both", expand=True)

        # Título
        ttk.Label(corpo, text="Sistema de Rotas", style="Titulo.TLabel").pack(anchor="w")
        ttk.Label(
            corpo,
            text="Gerencie cidades e estradas, depois calcule a melhor rota pelo algoritmo de Dijkstra.",
        ).pack(anchor="w", pady=(2, 10))

        # ---- Abas ----
        nb = ttk.Notebook(corpo)
        nb.pack(fill="both", expand=True)

        self._seletores: list[ttk.Combobox] = []

        for titulo, montar in (
            ("  📍 Calcular Rota  ", self._aba_rota),
            ("  🏙️ Gerenciar Cidades e Estradas  ", self._aba_gerenciar),
            ("  📊 Adjacência do Grafo  ", self._aba_adjacencia),
        ):
            aba = ttk.Frame(nb, padding=14)
            nb.add(aba, text=titulo)
            montar(aba)

        # Status bar
        self.status = tk.StringVar()
        ttk.Label(corpo, textvariable=self.status, foreground="#666").pack(anchor="w", pady=(8, 0))

        self._atualizar()
        # Intercepta o X da janela para também encerrar o servidor do mapa.
        janela.protocol("WM_DELETE_WINDOW", self._fechar)

    # -----------------------------------------------------------------------
    # Estilos
    # -----------------------------------------------------------------------

    def _estilos(self, janela: tk.Tk) -> None:
        """Define tema, fontes e cores dos componentes da janela recebida."""
        s = ttk.Style(janela)
        s.theme_use("clam")
        s.configure("TLabel",          font=("Segoe UI", 10))
        s.configure("TButton",         font=("Segoe UI", 10), padding=5)
        s.configure("Treeview",        font=("Segoe UI", 10), rowheight=25)
        s.configure("Treeview.Heading",font=("Segoe UI", 10, "bold"))
        s.configure("Titulo.TLabel",   font=("Segoe UI", 17, "bold"))
        s.configure("Dest.TLabel",     font=("Segoe UI", 11, "bold"), foreground="#0d6efd")

    # -----------------------------------------------------------------------
    # Componentes reutilizáveis
    # -----------------------------------------------------------------------

    def _combobox_cidade(self, pai, label: str, col: int) -> ttk.Combobox:
        """Cria um seletor de cidades em 'pai' e devolve o componente.

        'label' é o título e 'col' é a coluna no formulário. Guarda o seletor
        em _seletores para atualizar suas opções quando o grafo mudar.
        """
        ttk.Label(pai, text=label).grid(row=0, column=col, sticky="w")
        cb = ttk.Combobox(pai, state="readonly", width=22)
        cb.grid(row=1, column=col, sticky="ew", padx=(0, 8), pady=(4, 8))
        pai.columnconfigure(col, weight=1)
        self._seletores.append(cb)
        return cb

    @staticmethod
    def _adicionar_rolagem(componente: tk.Text | ttk.Treeview) -> None:
        """Posiciona o componente e suas barras de rolagem no frame que o contém."""
        frame = componente.master
        vertical = ttk.Scrollbar(frame, orient="vertical", command=componente.yview)
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=componente.xview)
        componente.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        componente.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

    @classmethod
    def _treeview(cls, pai, colunas: dict[str, str], altura: int = 7) -> ttk.Treeview:
        """Retorna uma tabela rolável dentro do componente 'pai'.

        'colunas' associa os identificadores internos aos títulos visíveis;
        'altura' indica a quantidade inicial de linhas exibidas.
        """
        f = ttk.Frame(pai)
        f.pack(fill="both", expand=True)
        tv = ttk.Treeview(f, columns=list(colunas), show="headings", height=altura)
        for k, v in colunas.items():
            tv.heading(k, text=v)
            tv.column(k, width=110, minwidth=60)
        cls._adicionar_rolagem(tv)
        return tv

    @classmethod
    def _caixa_texto(cls, pai) -> tk.Text:
        """Retorna uma caixa de texto rolável, inicialmente somente para leitura.

        É criada dentro de 'pai' e usa fonte monoespaçada para alinhar as
        colunas da matriz de adjacência e os textos dos resultados.
        """
        f = ttk.Frame(pai)
        f.pack(fill="both", expand=True, pady=(6, 0))
        t = tk.Text(
            f, wrap="none", font=("Consolas", 10), height=10,
            bg="#ffffff", fg="#172b4d", relief="solid", bd=1,
            padx=8, pady=8, state="disabled",
        )
        cls._adicionar_rolagem(t)
        return t

    @staticmethod
    def _escrever(t: tk.Text, texto: str) -> None:
        """Substitui o conteúdo de 't' por 'texto' e bloqueia a edição novamente."""
        t.configure(state="normal")
        t.delete("1.0", "end")
        t.insert("1.0", texto)
        t.configure(state="disabled")

    # -----------------------------------------------------------------------
    # ABA 1 — Calcular Rota (Execução do Algoritmo de Dijkstra)
    # -----------------------------------------------------------------------

    def _aba_rota(self, pai: ttk.Frame) -> None:
        """Monta em 'pai' os seletores, os botões e a saída da busca de rotas.

        Alterar um seletor apaga o resultado anterior. O botão de cálculo
        chama _calcular_rota; montar a aba ainda não executa Dijkstra.
        """
        # Linha de seletores de cidades e critério
        form = ttk.Frame(pai)
        form.pack(fill="x")

        self.cb_origem  = self._combobox_cidade(form, "Cidade de Origem",  0)
        self.cb_destino = self._combobox_cidade(form, "Cidade de Destino", 1)

        ttk.Label(form, text="Otimizar por").grid(row=0, column=2, sticky="w")
        self.cb_criterio = ttk.Combobox(form, values=list(CRITERIOS), state="readonly", width=22)
        self.cb_criterio.current(0)
        self.cb_criterio.grid(row=1, column=2, sticky="ew", pady=(4, 8))
        form.columnconfigure(2, weight=1)

        for w in (self.cb_origem, self.cb_destino, self.cb_criterio):
            # bind registra uma reação a um evento. A lambda recebe o
            # evento como '_' porque só precisamos chamar _limpar_rota.
            w.bind("<<ComboboxSelected>>", lambda _: self._limpar_rota())

        # Botões
        bts = ttk.Frame(pai)
        bts.pack(fill="x", pady=(0, 10))

        ttk.Button(bts, text="🔍 Calcular Rota",
                   command=self._calcular_rota).pack(side="left", padx=(0, 6))
        ttk.Button(bts, text="⇄ Inverter",
                   command=self._inverter_rota).pack(side="left", padx=(0, 6))
        self.btn_ver_mapa = ttk.Button(
            bts, text="🗺️ Ver no Mapa",
            command=lambda: self._abrir_mapa(self.ultima_rota), state="disabled"
        )
        self.btn_ver_mapa.pack(side="left", padx=(0, 6))
        ttk.Button(bts, text="🌐 Mapa Geral",
                   command=lambda: self._abrir_mapa()).pack(side="left")

        # Resumo e resultado
        self.var_resumo = tk.StringVar()
        ttk.Label(pai, textvariable=self.var_resumo, style="Dest.TLabel").pack(
            anchor="w", pady=(4, 2)
        )
        self.txt_rota = self._caixa_texto(pai)

    def _limpar_rota(self) -> None:
        """Descarta a rota exibida e desabilita seu mapa até um novo cálculo.

        Não remove cidades nem estradas: limpa apenas o resultado da busca.
        """
        self.ultima_rota = None
        self.btn_ver_mapa.configure(state="disabled")
        if not self.grafo.cidades:
            self.var_resumo.set("💡 Nenhuma cidade cadastrada. Use a aba 'Gerenciar' para começar.")
        else:
            self.var_resumo.set("Selecione origem e destino, depois clique em 'Calcular Rota'.")
        self._escrever(self.txt_rota, "")

    def _inverter_rota(self) -> None:
        """Troca origem e destino nos seletores e descarta o resultado anterior.

        Em estradas de mão única, a viagem de volta pode ter outro caminho
        ou nem existir. Por isso é necessário calcular a rota novamente.
        """
        a, b = self.cb_origem.get(), self.cb_destino.get()
        self.cb_origem.set(b)
        self.cb_destino.set(a)
        self._limpar_rota()

    def _calcular_rota(self) -> None:
        """Valida a seleção, chama Grafo.menor_rota e apresenta o percurso.

        O critério determina qual peso Dijkstra minimiza: soma das distâncias
        ou soma dos tempos. Os outros totais descrevem o caminho escolhido,
        sem necessariamente serem os menores possíveis. None significa
        que o destino não é alcançável seguindo as estradas cadastradas.
        """
        self._limpar_rota()
        if len(self.grafo.cidades) < 2:
            messagebox.showwarning(
                "Sem cidades",
                "Cadastre ao menos 2 cidades e uma estrada antes de calcular rotas.",
                parent=self.janela,
            )
            return

        origem, destino = self.cb_origem.get(), self.cb_destino.get()
        if not origem or not destino:
            messagebox.showwarning("Atenção", "Selecione a origem e o destino.", parent=self.janela)
            return
        if origem == destino:
            messagebox.showwarning("Atenção", "Origem e destino devem ser cidades diferentes.", parent=self.janela)
            return

        criterio = CRITERIOS[self.cb_criterio.get()]
        try:
            # Este é o ponto em que a interface chama Dijkstra. O algoritmo
            # está em grafo.py; aqui apenas usamos o objeto Rota devolvido.
            rota = self.grafo.menor_rota(origem, destino, criterio)
        except ValueError as e:
            messagebox.showerror("Erro", str(e), parent=self.janela)
            return

        if rota is None:
            self.var_resumo.set("Sem caminho entre essas cidades.")
            self._escrever(self.txt_rota, "Não há estradas conectando essas cidades no grafo atual.")
            return

        self.ultima_rota = rota
        self.btn_ver_mapa.configure(state="normal")
        self.var_resumo.set(
            f"Distância: {fmt(rota.distancia)} km   |   "
            f"Tempo: {fmt(rota.tempo)} min"
        )
        self._escrever(
            self.txt_rota,
            f"Critério: {self.cb_criterio.get()}   |   Algoritmo: Dijkstra\n\n"
            f"Percurso:  {' → '.join(rota.cidades)}\n\n"
            f"Passo a passo:\n"
            + "\n".join(f"  {i}. {c}" for i, c in enumerate(rota.cidades, 1)),
        )
        # O "Passo a passo" exibido enumera as cidades da rota final;
        # não é um registro de todas as explorações feitas por Dijkstra.

    # -----------------------------------------------------------------------
    # ABA 2 — Gerenciar Cidades e Estradas
    # (Cadastro manual, busca no OpenStreetMap e medição automática via OSRM)
    # -----------------------------------------------------------------------

    def _aba_gerenciar(self, pai: ttk.Frame) -> None:
        """
        Monta em 'pai' os formulários de edição dos vértices e das arestas.

        O painel esquerdo cadastra cidades por nome ou por busca geográfica.
        O direito edita as estradas, seus pesos e a permissão de ida e volta.
        Os botões chamam outros métodos; a gravação fica concentrada em
        _salvar para manter o grafo em memória e o JSON sincronizados.
        """
        # Painel dividido em esquerda (cidades) e direita (estradas)
        painel = ttk.Frame(pai)
        painel.pack(fill="both", expand=True)

        # ── ESQUERDA: Cidades ────────────────────────────────────────────────
        esq = ttk.LabelFrame(painel, text=" Cidades ", padding=10)
        esq.pack(side="left", fill="both", padx=(0, 10))

        # Entrada manual
        ttk.Label(esq, text="Nome da cidade:").pack(anchor="w")
        self.ent_cidade = ttk.Entry(esq, width=24)
        self.ent_cidade.pack(fill="x", pady=(3, 5))
        self.ent_cidade.bind("<Return>", lambda _: self._add_cidade())

        bts_cid = ttk.Frame(esq)
        bts_cid.pack(fill="x", pady=(0, 8))
        ttk.Button(bts_cid, text="➕ Adicionar", command=self._add_cidade).pack(
            side="left", fill="x", expand=True, padx=(0, 3))
        ttk.Button(bts_cid, text="🗑️ Excluir", command=self._rem_cidade).pack(
            side="left", fill="x", expand=True)

        self.tv_cidades = self._treeview(esq, {"nome": "Cidades cadastradas"}, altura=6)

        ttk.Separator(esq, orient="horizontal").pack(fill="x", pady=8)

        # Busca de cidades reais (OpenStreetMap)
        ttk.Label(esq, text="🔎 Buscar cidade real (OpenStreetMap):").pack(anchor="w")
        busca_f = ttk.Frame(esq)
        busca_f.pack(fill="x", pady=(3, 4))
        self.ent_busca = ttk.Entry(busca_f)
        self.ent_busca.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.ent_busca.bind("<Return>", lambda _: self._buscar_cidade())
        self.btn_busca = ttk.Button(busca_f, text="Buscar", command=self._buscar_cidade)
        self.btn_busca.pack(side="left")

        self.tv_busca = self._treeview(esq, {"cidade": "Resultados da busca"}, altura=5)
        self.tv_busca.bind("<Double-1>", lambda _: self._add_cidade_real())

        bts_real = ttk.Frame(esq)
        bts_real.pack(fill="x", pady=(4, 0))
        self.btn_add_real = ttk.Button(bts_real, text="➕ Adicionar ao Grafo", command=self._add_cidade_real)
        self.btn_add_real.pack(side="left", padx=(0, 4))

        self.var_status_busca = tk.StringVar(value="Digite uma cidade e clique em Buscar.")
        ttk.Label(esq, textvariable=self.var_status_busca, foreground="#0a6efd",
                  wraplength=240, font=("Segoe UI", 9)).pack(anchor="w", pady=(5, 0))
        # Todos os botões que ficam desabilitados durante consulta de rede
        self._botoes_rede = [self.btn_busca, self.btn_add_real]

        # Botão de zerar cadastro
        ttk.Separator(esq, orient="horizontal").pack(fill="x", pady=8)
        ttk.Button(esq, text="⚠️ Zerar Todo o Cadastro",
                   command=self._zerar).pack(fill="x")

        # ── DIREITA: Estradas ────────────────────────────────────────────────
        dir = ttk.LabelFrame(painel, text=" Estradas ", padding=10)
        dir.pack(side="left", fill="both", expand=True)

        form_e = ttk.Frame(dir)
        form_e.pack(fill="x")
        self.cb_est_orig = self._combobox_cidade(form_e, "Origem", 0)
        self.cb_est_dest = self._combobox_cidade(form_e, "Destino", 1)

        # Preenche km automaticamente ao selecionar origem/destino
        for cb in (self.cb_est_orig, self.cb_est_dest):
            cb.bind("<<ComboboxSelected>>", lambda _: self._auto_preencher_estrada())

        pesos_f = ttk.Frame(dir)
        pesos_f.pack(fill="x", pady=(0, 5))
        self._campos_peso: dict[str, ttk.Entry] = {}
        for col, (chave, label) in enumerate(
            [("distancia", "Distância (km)"),
             ("tempo",     "Tempo (min)")]
        ):
            ttk.Label(pesos_f, text=label).grid(row=0, column=col, sticky="w")
            e = ttk.Entry(pesos_f, width=18)
            e.grid(row=1, column=col, sticky="ew", padx=(0, 7), pady=(3, 5))
            pesos_f.columnconfigure(col, weight=1)
            self._campos_peso[chave] = e

        self.lbl_dica_dist = ttk.Label(
            dir,
            text="Selecione origem e destino para consultar a distância real pelas rodovias.",
            foreground="#0a6efd", font=("Segoe UI", 9), wraplength=400,
        )
        self.lbl_dica_dist.pack(anchor="w", pady=(0, 4))

        self.var_maodupla = tk.BooleanVar(value=True)
        # Esta opção define se a aresta pode ser percorrida nos dois
        # sentidos. Se desmarcada, existe somente origem -> destino.
        ttk.Checkbutton(dir, text="Estrada de mão dupla (ida e volta)",
                        variable=self.var_maodupla).pack(anchor="w", pady=(0, 6))

        bts_est = ttk.Frame(dir)
        bts_est.pack(fill="x", pady=(0, 8))
        ttk.Button(bts_est, text="💾 Salvar Estrada", command=self._salvar_estrada).pack(side="left", padx=(0, 5))
        ttk.Button(bts_est, text="🔄 Medir Rodovia",  command=self._auto_preencher_estrada).pack(side="left", padx=(0, 5))
        ttk.Button(bts_est, text="🗑️ Excluir Estrada", command=self._rem_estrada).pack(side="left", padx=(0, 5))
        ttk.Button(bts_est, text="Limpar Campos",     command=self._limpar_campos_estrada).pack(side="left")

        ttk.Label(dir, text="Clique numa estrada para editar:",
                  foreground="#555", font=("Segoe UI", 9)).pack(anchor="w", pady=(0, 3))

        self.tv_estradas = self._treeview(
            dir,
            {"orig": "Origem", "dest": "Destino",
             "dist": "km", "tempo": "min", "sentido": "Sentido"},
            altura=12,
        )
        self.tv_estradas.bind("<<TreeviewSelect>>", self._sel_estrada)

    # ─── Cidades (manual) ────────────────────────────────────────────────────

    def _add_cidade(self) -> None:
        """Cadastra pelo nome um vértice, inicialmente sem coordenadas.

        _salvar aplica a inclusão em uma cópia do grafo e grava o resultado.
        O vértice já pode participar de rotas com estradas cadastradas
        manualmente, mesmo sem ter uma posição para exibir no mapa.
        """
        nome = self.ent_cidade.get().strip()
        if not nome:
            messagebox.showwarning("Atenção", "Digite o nome da cidade.", parent=self.janela)
            return
        if self._salvar(lambda g: g.adicionar_cidade(nome)):
            self.ent_cidade.delete(0, "end")
            self.ent_cidade.focus_set()

    def _rem_cidade(self) -> None:
        """Pede confirmação e exclui o vértice selecionado e suas arestas.

        Grafo.remover_cidade apaga tanto as conexões de saída quanto as de
        entrada, para nenhuma estrada continuar apontando à cidade removida.
        """
        sel = self.tv_cidades.selection()
        if not sel:
            messagebox.showinfo("Excluir", "Selecione uma cidade na lista.", parent=self.janela)
            return
        cidade = self.tv_cidades.item(sel[0])["values"][0]
        if messagebox.askyesno(
            "Confirmar",
            f"Apagar a cidade '{cidade}'? Todas as estradas conectadas serão removidas.",
            parent=self.janela,
        ):
            self._salvar(lambda g: g.remover_cidade(cidade))

    def _zerar(self) -> None:
        """Pede confirmação para esvaziar o grafo e persistir o cadastro vazio."""
        if not self.grafo.cidades:
            messagebox.showinfo("Aviso", "O cadastro já está vazio.", parent=self.janela)
            return
        if messagebox.askyesno(
            "Zerar Cadastro",
            "Apagar TODAS as cidades e estradas? Esta ação não pode ser desfeita.",
            parent=self.janela,
        ):
            self._salvar(lambda g: g.limpar())
            self._limpar_campos_estrada()

    # ─── Busca de cidades reais ───────────────────────────────────────────────

    def _tarefa_rede(self, tarefa, ao_concluir) -> None:
        """Executa 'tarefa()' fora da interface e entrega o resultado a 'ao_concluir'.

        Uma thread faz a consulta e coloca resultado ou erro na fila.
        A janela verifica a fila periodicamente com after(), mantendo a
        interface responsiva. Enquanto essa tarefa está ativa, os botões
        de busca ficam bloqueados para evitar buscas sobrepostas por aqui.
        """
        if self.ocupado_mapa:
            return
        self.ocupado_mapa = True
        for b in self._botoes_rede:
            b.configure(state="disabled")

        def run():
            """Produz na thread um par (resultado, erro) para a fila da janela."""
            try:
                self.fila_mapa.put((tarefa(), None))
            except Exception as e:
                self.fila_mapa.put((None, e))

        def poll():
            """Verifica a fila sem esperar e atualiza a interface ao obter a resposta."""
            try:
                resultado, erro = self.fila_mapa.get_nowait()
            except Empty:
                # Agenda uma nova verificação em 100 ms; não usa uma espera
                # bloqueante que impediria a janela de processar eventos.
                self.janela.after(100, poll)
                return
            self.ocupado_mapa = False
            for b in self._botoes_rede:
                b.configure(state="normal")
            if erro:
                self.var_status_busca.set(f"Erro: {erro}")
            else:
                ao_concluir(resultado)

        Thread(target=run, daemon=True).start()
        self.janela.after(100, poll)

    def _buscar_cidade(self) -> None:
        """Envia o nome digitado ao serviço Photon e lista as sugestões recebidas.

        A consulta obtém nomes e coordenadas; não inclui cidades no grafo
        até o usuário selecionar um resultado e chamar _add_cidade_real.
        """
        consulta = self.ent_busca.get().strip()
        if len(consulta) < 2:
            self.var_status_busca.set("Digite ao menos 2 caracteres para buscar.")
            return
        self.tv_busca.delete(*self.tv_busca.get_children())
        self.resultados_busca = []
        self.var_status_busca.set("Buscando…")

        def mostrar(cidades: list[dict]):
            """Preenche a tabela e associa o identificador da linha à sugestão original."""
            self.resultados_busca = cidades
            for i, c in enumerate(cidades):
                self.tv_busca.insert("", "end", iid=str(i), values=(c["nome"],))
            self.var_status_busca.set(
                f"{len(cidades)} cidade(s). Duplo-clique ou use '➕ Adicionar ao Grafo'."
            )

        self._tarefa_rede(lambda: self.servico_mapas.buscar_cidades(consulta), mostrar)

    def _add_cidade_real(self) -> None:
        """Cadastra a sugestão selecionada com sua latitude e longitude.

        Se o nome já existir, atualiza suas coordenadas. Ter coordenadas
        permite medir rodovias e exibir o vértice no mapa, mas não cria
        automaticamente arestas ligando essa cidade às demais.
        """
        sel = self.tv_busca.selection()
        if not sel:
            self.var_status_busca.set("Selecione uma cidade nos resultados.")
            return
        c = self.resultados_busca[int(sel[0])]
        if self._salvar(lambda g: g.adicionar_ou_atualizar_cidade(c["nome"], c["latitude"], c["longitude"])):
            self.var_status_busca.set(f"'{c['nome']}' adicionada ao grafo!")

    # ─── Estradas e Medição Automática ───────────────────────────────────────

    def _preencher_pesos(self, distancia: str = "", tempo: str = "") -> None:
        """Substitui os textos dos pesos; sem argumentos, limpa os dois campos."""
        for chave, texto in (("distancia", distancia), ("tempo", tempo)):
            campo = self._campos_peso[chave]
            campo.delete(0, "end")
            campo.insert(0, texto)

    def _auto_preencher_estrada(self) -> None:
        """
        Consulta o OSRM em outra thread e preenche os pesos do formulário.

        Usa as coordenadas das cidades selecionadas para obter distância
        rodoviária em km, duração estimada em minutos e pontos da estrada.
        Se a consulta falhar, estima a distância com Haversine e o tempo
        supondo 80 km/h. Ainda é necessário salvar a estrada para que
        esses dados virem uma aresta usada pelo Dijkstra local.
        """
        orig, dest = self.cb_est_orig.get(), self.cb_est_dest.get()
        if not orig or not dest or orig == dest:
            return

        coords = self.grafo.coordenadas
        if orig not in coords or dest not in coords:
            self._geometria_pendente = None
            self.lbl_dica_dist.configure(
                text="⚠️ Uma ou ambas as cidades não têm coordenadas cadastradas. Digite a distância manualmente.",
                foreground="#cc6600",
            )
            return

        lat1, lon1 = coords[orig]
        lat2, lon2 = coords[dest]

        # Controle de sequência para evitar que respostas antigas sobrescrevam seleções novas
        self._consulta_estrada_seq += 1
        seq_atual = self._consulta_estrada_seq
        self._geometria_pendente = None

        self.lbl_dica_dist.configure(
            text="⏳ Medindo distância real pelas estradas (via OSRM)...",
            foreground="#0a6efd",
        )

        def buscar():
            """Consulta a rodovia e agenda a entrega do resultado à janela."""
            try:
                dados = self.servico_mapas.consultar_estrada(
                    {"latitude": lat1, "longitude": lon1},
                    {"latitude": lat2, "longitude": lon2},
                )
                erro = None
            except Exception as e:
                dados = None
                erro = e

            def atualizar_ui():
                """Preenche os campos apenas se a resposta ainda corresponder à seleção atual."""
                # A rede pode responder fora da ordem de envio. Uma resposta
                # antiga não deve preencher o formulário de outro par de cidades.
                if seq_atual != self._consulta_estrada_seq:
                    return
                if self.cb_est_orig.get() != orig or self.cb_est_dest.get() != dest:
                    return

                if dados:
                    dist_km = round(dados["distancia"], 2)
                    tempo_min = round(dados["tempo"], 1)
                    self._geometria_pendente = dados.get("geometria")

                    self._preencher_pesos(fmt(dist_km), fmt(tempo_min))

                    self.lbl_dica_dist.configure(
                        text=f"🛣️ Estrada real: {fmt(dist_km)} km • Tempo estimado: {fmt(tempo_min)} min (via rodovias).",
                        foreground="#1a7a1a",
                    )
                else:
                    # Estimativa geográfica, sem considerar curvas ou desvios
                    # das rodovias. O tempo assume velocidade constante de 80 km/h.
                    dist_reta = round(haversine(lat1, lon1, lat2, lon2), 2)
                    tempo_reta = round(dist_reta / 80 * 60, 1)

                    self._preencher_pesos(fmt(dist_reta), fmt(tempo_reta))

                    msg_err = str(erro) if erro else "serviço indisponível"
                    self.lbl_dica_dist.configure(
                        text=f"⚠️ Não foi possível obter trajeto rodoviário ({msg_err}). Usando linha reta: {fmt(dist_reta)} km.",
                        foreground="#cc6600",
                    )

            self.janela.after(0, atualizar_ui)

        Thread(target=buscar, daemon=True).start()

    def _salvar_estrada(self) -> None:
        """Valida os campos e grava uma aresta entre as cidades selecionadas.

        Os pesos podem ter sido digitados ou preenchidos pela consulta.
        Sem tempo informado, estima 1 min/km. A geometria serve ao desenho;
        os pesos distância e tempo são os números usados na busca de rotas.
        A opção de mão dupla determina os sentidos permitidos no grafo.
        """
        orig, dest = self.cb_est_orig.get(), self.cb_est_dest.get()
        if not orig or not dest:
            messagebox.showwarning("Atenção", "Selecione origem e destino.", parent=self.janela)
            return
        if orig == dest:
            messagebox.showwarning("Atenção", "Origem e destino devem ser diferentes.", parent=self.janela)
            return

        txt_d = self._campos_peso["distancia"].get().strip()
        if not txt_d:
            # Solicita o preenchimento automático. Como a consulta é
            # assíncrona, ela pode terminar apenas após esta tentativa de salvar.
            self._auto_preencher_estrada()
            txt_d = self._campos_peso["distancia"].get().strip()
        if not txt_d:
            messagebox.showwarning(
                "Distância não preenchida",
                "Não foi possível calcular a distância automaticamente (cidades sem coordenadas).\n"
                "Digite a distância em km no campo correspondente.",
                parent=self.janela,
            )
            self._campos_peso["distancia"].focus_set()
            return
        try:
            dist = parse_float(txt_d, "distância")
            if dist <= 0:
                raise ValueError("A distância deve ser maior que zero.")
            # Tempo opcional: aqui a estimativa é 1 min/km (equivale a 60 km/h).
            # É uma regra diferente dos 80 km/h usados no fallback da consulta.
            txt_t = self._campos_peso["tempo"].get().strip()
            if txt_t:
                tempo = parse_float(txt_t, "tempo")
                if tempo <= 0:
                    raise ValueError("O tempo deve ser maior que zero.")
            else:
                tempo = round(dist, 1)
                self._campos_peso["tempo"].delete(0, "end")
                self._campos_peso["tempo"].insert(0, fmt(tempo))
        except ValueError as e:
            messagebox.showerror("Valor inválido", str(e), parent=self.janela)
            return

        # Recupera a geometria das curvas gravada na medição do OSRM
        geo = self._geometria_pendente or self.grafo.obter_geometria(orig, dest)
        if self._salvar(
            lambda g: g.adicionar_estrada(
                orig, dest, distancia=dist, custo=0.0, tempo=tempo,
                bidirecional=self.var_maodupla.get(), geometria=geo,
            )
        ):
            self._limpar_campos_estrada()

    def _rem_estrada(self) -> None:
        """Pede confirmação e remove a aresta selecionada no cadastro.

        Uma estrada bidirecional tem as duas conexões removidas pelo grafo;
        uma de mão única remove apenas o sentido cadastrado.
        """
        sel = self.tv_estradas.selection()
        if not sel:
            messagebox.showinfo("Excluir", "Selecione uma estrada na lista.", parent=self.janela)
            return
        e = self.grafo.estradas[int(sel[0])]
        if messagebox.askyesno("Confirmar", f"Apagar estrada '{e.origem}' → '{e.destino}'?", parent=self.janela):
            self._salvar(lambda g: g.remover_estrada(e.origem, e.destino))
            self._limpar_campos_estrada()

    def _limpar_campos_estrada(self) -> None:
        """Limpa pesos, geometria pendente e seleção, restaurando a opção de mão dupla.

        Não apaga estradas do grafo; prepara o formulário para outra edição.
        """
        self._geometria_pendente = None
        self.tv_estradas.selection_remove(*self.tv_estradas.selection())
        self._preencher_pesos()
        self.var_maodupla.set(True)

    def _sel_estrada(self, _=None) -> None:
        """Copia os dados da estrada selecionada para os campos de edição.

        '_' recebe o evento do Treeview, cujo conteúdo não é necessário.
        O identificador da linha é o índice da estrada na lista ordenada.
        """
        sel = self.tv_estradas.selection()
        if not sel:
            return
        e = self.grafo.estradas[int(sel[0])]
        self.cb_est_orig.set(e.origem)
        self.cb_est_dest.set(e.destino)
        self.var_maodupla.set(e.bidirecional)
        self._preencher_pesos(
            str(e.distancia).replace(".", ","), str(e.tempo).replace(".", ","),
        )

    # -----------------------------------------------------------------------
    # ABA 3 — Adjacência (Representação Matemática)
    # -----------------------------------------------------------------------

    def _aba_adjacencia(self, pai: ttk.Frame) -> None:
        """Cria em 'pai' duas visualizações das conexões diretas do mesmo grafo.

        A lista enumera os vizinhos de cada cidade; a matriz cruza todas as
        origens e destinos. O seletor escolhe se o peso mostrado é distância
        ou tempo. São representações dos dados, sem executar Dijkstra.
        """
        crit_f = ttk.Frame(pai)
        crit_f.pack(fill="x", pady=(0, 6))
        ttk.Label(crit_f, text="Peso exibido:").pack(side="left", padx=(0, 6))
        self.cb_adj_criterio = ttk.Combobox(crit_f, values=list(CRITERIOS), state="readonly", width=24)
        self.cb_adj_criterio.current(0)
        self.cb_adj_criterio.pack(side="left")
        self.cb_adj_criterio.bind("<<ComboboxSelected>>", lambda _: self._atualizar_adjacencia())

        ttk.Label(
            pai,
            text="∞ = sem aresta direta • diagonal = 0 • a lista mostra as conexões de saída de cada vértice.",
            foreground="#555", font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(0, 8))

        sub = ttk.Notebook(pai)
        sub.pack(fill="both", expand=True)

        lista_f  = ttk.Frame(sub, padding=6)
        matriz_f = ttk.Frame(sub, padding=6)
        sub.add(lista_f,  text=" Lista de Adjacência ")
        sub.add(matriz_f, text=" Matriz de Adjacência ")

        self.txt_lista  = self._caixa_texto(lista_f)
        self.txt_matriz = self._caixa_texto(matriz_f)

    # -----------------------------------------------------------------------
    # Mapa Interativo no Navegador
    # -----------------------------------------------------------------------

    def _abrir_mapa(self, rota=None) -> None:
        """Abre o mapa do grafo no navegador, destacando 'rota' quando fornecida.

        gerar_mapa monta o HTML e ServidorMapa o disponibiliza em endereço
        local. Sem rota, mostra o cadastro geral. Coordenadas são necessárias
        para posicionar cidades; esta função não calcula outro caminho.
        """
        coordenadas = self.grafo.coordenadas
        if rota and any(c not in coordenadas for c in rota.cidades):
            messagebox.showinfo(
                "Mapa",
                "Algumas cidades da rota não têm coordenadas. "
                "Importe-as via busca real para visualizá-las no mapa.",
                parent=self.janela,
            )
            return
        try:
            html = gerar_mapa(self.grafo, self.arquivo.with_name("rotas_mapa.html"), rota)
            if self.servidor_mapa is None:
                self.servidor_mapa = ServidorMapa(html)
            if not webbrowser.open(self.servidor_mapa.url):
                messagebox.showinfo("Mapa", f"Abra no navegador: {self.servidor_mapa.url}", parent=self.janela)
        except (OSError, ValueError, ErroMapa) as e:
            messagebox.showerror("Erro ao abrir mapa", str(e), parent=self.janela)

    # -----------------------------------------------------------------------
    # Persistência e atualização da UI
    # -----------------------------------------------------------------------

    def _salvar(self, alterar) -> bool:
        """
        Aplica 'alterar(grafo_copia)' e salva o cadastro, retornando sucesso.

        'alterar' é uma função recebida como argumento, por exemplo uma
        lambda que adiciona uma cidade. Faz a mudança numa cópia validada,
        escreve um arquivo temporário e substitui o JSON por ele. Só então
        troca o grafo da aplicação e atualiza a tela. Em caso de ValueError
        ou OSError, exibe o erro e retorna False sem trocar o grafo em memória.
        """
        try:
            # Serializar e reconstruir cria uma cópia independente do modelo.
            # Assim, uma alteração rejeitada não modifica o cadastro em uso.
            copia = Grafo.de_dict(self.grafo.para_dict())
            alterar(copia)
            conteudo = json.dumps(copia.para_dict(), ensure_ascii=False, indent=2, allow_nan=False)
            tmp = self.arquivo.with_suffix(".tmp")
            tmp.write_text(conteudo, encoding="utf-8")
            # Substituir o arquivo pronto evita gravar o JSON definitivo
            # parcialmente; isso não equivale a uma garantia contra toda falha de disco.
            tmp.replace(self.arquivo)
        except (ValueError, OSError) as e:
            messagebox.showerror("Erro ao salvar", str(e), parent=self.janela)
            return False
        self.grafo = copia
        self._atualizar()
        return True

    def _atualizar(self) -> None:
        """
        Sincroniza seletores, tabelas, status e adjacência com o grafo atual.

        É chamada na abertura e após um salvamento bem-sucedido. Descarta
        o resultado anterior de Dijkstra, pois mudar vértices, arestas ou
        pesos pode tornar aquela rota inválida ou deixar de ser a melhor.
        """
        cidades = self.grafo.cidades
        estradas = self.grafo.estradas

        # Comboboxes de seleção de cidade
        for cb in self._seletores:
            atual = cb.get()
            cb.configure(values=cidades)
            if atual not in cidades:
                cb.set(cidades[0] if cidades else "")

        # Tabela de cidades
        self.tv_cidades.delete(*self.tv_cidades.get_children())
        for c in cidades:
            self.tv_cidades.insert("", "end", values=(c,))

        # Tabela de estradas
        self.tv_estradas.delete(*self.tv_estradas.get_children())
        for idx, e in enumerate(estradas):
            sentido = "↔ Ida e volta" if e.bidirecional else "→ Só ida"
            self.tv_estradas.insert("", "end", iid=str(idx), values=(
                e.origem, e.destino,
                fmt(e.distancia), fmt(e.tempo),
                sentido,
            ))

        # Status bar
        if cidades:
            self.status.set(
                f"{len(cidades)} cidade(s) • {len(estradas)} estrada(s) • {self.arquivo.name}"
            )
        else:
            self.status.set(
                "Grafo vazio — cadastre cidades na aba 'Gerenciar Cidades e Estradas'."
            )

        self._limpar_rota()
        self._atualizar_adjacencia()

    def _atualizar_adjacencia(self) -> None:
        """Formata a lista e a matriz usando o peso escolhido pelo usuário.

        A célula [origem][destino] contém o peso de uma estrada direta, 0
        na diagonal e infinito quando não existe essa conexão direta.
        Infinito não impede que exista uma rota passando por outras cidades.
        Não é uma matriz de menores caminhos: esses são calculados por Dijkstra.
        """
        criterio = CRITERIOS[self.cb_adj_criterio.get()]
        unidade  = UNIDADES[criterio]

        # Lista de adjacência: mostra apenas os vizinhos alcançáveis por
        # uma aresta de saída, respeitando as estradas de mão única.
        lista = self.grafo.lista_adjacencia(criterio)
        linhas = [
            f"• {cidade}:\n   ↳ " + (
                ", ".join(f"{v} ({fmt(p)} {unidade})" for v, p in vizinhos)
                or "sem conexões"
            )
            for cidade, vizinhos in lista.items()
        ]
        self._escrever(
            self.txt_lista,
            f"Lista de Adjacência  [{self.cb_adj_criterio.get()}]\n\n"
            + ("\n\n".join(linhas) if linhas else "Nenhuma cidade cadastrada."),
        )

        # Matriz de adjacência: cidades dá a ordem comum de linhas e colunas.
        # Estradas de mão única podem tornar a matriz assimétrica.
        cidades, matriz = self.grafo.matriz_adjacencia(criterio)
        if not cidades:
            self._escrever(self.txt_matriz, "Nenhuma cidade cadastrada.")
            return

        vals = [["∞" if math.isinf(p) else fmt(p) for p in row] for row in matriz]
        # A largura considera nomes e valores para alinhar a tabela na
        # fonte monoespaçada da caixa de texto, mesmo com cidades de nomes longos.
        col_w = max(8, *(len(c) for c in cidades), *(len(v) for row in vals for v in row)) + 2
        cab = " " * col_w + "".join(c.rjust(col_w) for c in cidades)
        sep = "-" * len(cab)
        rows = [c.ljust(col_w) + "".join(v.rjust(col_w) for v in row) for c, row in zip(cidades, vals)]
        self._escrever(
            self.txt_matriz,
            f"Matriz de Adjacência  [{unidade}]\n\n{cab}\n{sep}\n" + "\n".join(rows),
        )

    def _fechar(self) -> None:
        """Encerra o servidor de mapa, se houver, e destrói a janela principal."""
        if self.servidor_mapa:
            self.servidor_mapa.fechar()
        self.janela.destroy()


# ---------------------------------------------------------------------------
# Ponto de entrada do programa (Main)
# ---------------------------------------------------------------------------

def main() -> None:
    """Cria a janela, inicializa Aplicacao e inicia o laço de eventos do Tkinter.

    A janela fica oculta durante a montagem. mainloop() passa a processar
    cliques, teclas e rotinas agendadas até o encerramento da aplicação.
    """
    janela = tk.Tk()
    janela.withdraw()
    try:
        Aplicacao(janela)
    except (OSError, ValueError, TypeError) as e:
        messagebox.showerror(
            "Erro ao abrir dados",
            f"Não foi possível carregar {ARQUIVO_DADOS.name}:\n{e}\n\n"
            "Renomeie ou apague o arquivo para iniciar um cadastro novo.",
            parent=janela,
        )
        janela.destroy()
        return
    janela.deiconify()
    janela.mainloop()


if __name__ == "__main__":
    # Abre a interface quando este arquivo é executado diretamente;
    # importar main.py em outro módulo apenas disponibiliza suas definições.
    main()
