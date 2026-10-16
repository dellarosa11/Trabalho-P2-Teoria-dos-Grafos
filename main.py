"""Sistema de Rotas entre Cidades — Teoria dos Grafos.

Abas:
  1. Calcular Rota  — seleciona origem/destino e executa Dijkstra.
  2. Gerenciar      — adiciona/remove cidades e estradas (manual ou via mapa real).
  3. Adjacência     — lista e matriz de adjacência do grafo.
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


ARQUIVO_DADOS = Path(__file__).with_name("rotas.json")

CRITERIOS = {
    "Menor distância (km)": "distancia",
    "Menor tempo (min)":    "tempo",
}

UNIDADES = {"distancia": "km", "tempo": "min"}


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def fmt(valor: float) -> str:
    """Número com 2 casas decimais no padrão pt-BR (vírgula decimal)."""
    return f"{valor:.2f}".replace(".", ",")


def parse_float(texto: str, campo: str) -> float:
    """Converte texto com vírgula/ponto para float >= 0 ou levanta ValueError."""
    try:
        v = float(texto.strip().replace(",", "."))
    except (ValueError, AttributeError, OverflowError):
        raise ValueError(f"Valor inválido para «{campo}».")
    if not math.isfinite(v) or v < 0:
        raise ValueError(f"«{campo}» deve ser um número positivo.")
    return v


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distância em km entre dois pontos geográficos (linha reta sobre a Terra)."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Aplicação
# ---------------------------------------------------------------------------

class Aplicacao:
    """Interface gráfica com 3 abas: Rota | Gerenciar | Adjacência."""

    def __init__(self, janela: tk.Tk, arquivo: Path | str = ARQUIVO_DADOS) -> None:
        self.janela = janela
        self.arquivo = Path(arquivo)
        self.grafo = Grafo()
        self.servico_mapas = ServicoMapas()
        self.servidor_mapa: ServidorMapa | None = None
        self.fila_mapa: Queue = Queue()
        self.ocupado_mapa = False
        self.ultima_rota = None
        self.resultados_busca: list[dict] = []
        self._geometria_pendente: tuple | None = None
        self._consulta_estrada_seq = 0

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

        aba_rota   = ttk.Frame(nb, padding=14)
        aba_ger    = ttk.Frame(nb, padding=14)
        aba_adj    = ttk.Frame(nb, padding=14)

        nb.add(aba_rota, text="  📍 Calcular Rota  ")
        nb.add(aba_ger,  text="  🏙️ Gerenciar Cidades e Estradas  ")
        nb.add(aba_adj,  text="  📊 Adjacência do Grafo  ")

        self._aba_rota(aba_rota)
        self._aba_gerenciar(aba_ger)
        self._aba_adjacencia(aba_adj)

        # Status bar
        self.status = tk.StringVar()
        ttk.Label(corpo, textvariable=self.status, foreground="#666").pack(anchor="w", pady=(8, 0))

        self._atualizar()
        janela.protocol("WM_DELETE_WINDOW", self._fechar)

    # -----------------------------------------------------------------------
    # Estilos
    # -----------------------------------------------------------------------

    def _estilos(self, janela: tk.Tk) -> None:
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
        ttk.Label(pai, text=label).grid(row=0, column=col, sticky="w")
        cb = ttk.Combobox(pai, state="readonly", width=22)
        cb.grid(row=1, column=col, sticky="ew", padx=(0, 8), pady=(4, 8))
        pai.columnconfigure(col, weight=1)
        self._seletores.append(cb)
        return cb

    @staticmethod
    def _treeview(pai, colunas: dict[str, str], altura: int = 7) -> ttk.Treeview:
        f = ttk.Frame(pai)
        f.pack(fill="both", expand=True)
        tv = ttk.Treeview(f, columns=list(colunas), show="headings", height=altura)
        for k, v in colunas.items():
            tv.heading(k, text=v)
            tv.column(k, width=110, minwidth=60)
        sv = ttk.Scrollbar(f, orient="vertical",   command=tv.yview)
        sh = ttk.Scrollbar(f, orient="horizontal",  command=tv.xview)
        tv.configure(yscrollcommand=sv.set, xscrollcommand=sh.set)
        tv.grid(row=0, column=0, sticky="nsew")
        sv.grid(row=0, column=1, sticky="ns")
        sh.grid(row=1, column=0, sticky="ew")
        f.rowconfigure(0, weight=1)
        f.columnconfigure(0, weight=1)
        return tv

    @staticmethod
    def _caixa_texto(pai) -> tk.Text:
        f = ttk.Frame(pai)
        f.pack(fill="both", expand=True, pady=(6, 0))
        t = tk.Text(
            f, wrap="none", font=("Consolas", 10), height=10,
            bg="#ffffff", fg="#172b4d", relief="solid", bd=1,
            padx=8, pady=8, state="disabled",
        )
        sv = ttk.Scrollbar(f, orient="vertical",   command=t.yview)
        sh = ttk.Scrollbar(f, orient="horizontal",  command=t.xview)
        t.configure(yscrollcommand=sv.set, xscrollcommand=sh.set)
        t.grid(row=0, column=0, sticky="nsew")
        sv.grid(row=0, column=1, sticky="ns")
        sh.grid(row=1, column=0, sticky="ew")
        f.rowconfigure(0, weight=1)
        f.columnconfigure(0, weight=1)
        return t

    @staticmethod
    def _escrever(t: tk.Text, texto: str) -> None:
        t.configure(state="normal")
        t.delete("1.0", "end")
        t.insert("1.0", texto)
        t.configure(state="disabled")

    # -----------------------------------------------------------------------
    # ABA 1 — Calcular Rota
    # -----------------------------------------------------------------------

    def _aba_rota(self, pai: ttk.Frame) -> None:
        # Linha de seletores
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
        self.ultima_rota = None
        self.btn_ver_mapa.configure(state="disabled")
        if not self.grafo.cidades:
            self.var_resumo.set("💡 Nenhuma cidade cadastrada. Use a aba 'Gerenciar' para começar.")
        else:
            self.var_resumo.set("Selecione origem e destino, depois clique em 'Calcular Rota'.")
        self._escrever(self.txt_rota, "")

    def _inverter_rota(self) -> None:
        a, b = self.cb_origem.get(), self.cb_destino.get()
        self.cb_origem.set(b)
        self.cb_destino.set(a)
        self._limpar_rota()

    def _calcular_rota(self) -> None:
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

    # -----------------------------------------------------------------------
    # ABA 2 — Gerenciar Cidades e Estradas
    # (inclui busca de cidades reais e importação de trajetos)
    # -----------------------------------------------------------------------

    def _aba_gerenciar(self, pai: ttk.Frame) -> None:
        """
        Aba unificada dividida em três seções na vertical:
          A) Cidades (lista + adicionar/remover + busca real)
          B) Estradas (formulário + lista)
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
        nome = self.ent_cidade.get().strip()
        if not nome:
            messagebox.showwarning("Atenção", "Digite o nome da cidade.", parent=self.janela)
            return
        if self._salvar(lambda g: g.adicionar_cidade(nome)):
            self.ent_cidade.delete(0, "end")
            self.ent_cidade.focus_set()

    def _rem_cidade(self) -> None:
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
        """Executa tarefa de rede em segundo plano sem travar a UI."""
        if self.ocupado_mapa:
            return
        self.ocupado_mapa = True
        for b in self._botoes_rede:
            b.configure(state="disabled")

        def run():
            try:
                self.fila_mapa.put((tarefa(), None))
            except Exception as e:
                self.fila_mapa.put((None, e))

        def poll():
            try:
                resultado, erro = self.fila_mapa.get_nowait()
            except Empty:
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
        consulta = self.ent_busca.get().strip()
        if len(consulta) < 2:
            self.var_status_busca.set("Digite ao menos 2 caracteres para buscar.")
            return
        self.tv_busca.delete(*self.tv_busca.get_children())
        self.resultados_busca = []
        self.var_status_busca.set("Buscando…")

        def mostrar(cidades: list[dict]):
            self.resultados_busca = cidades
            for i, c in enumerate(cidades):
                self.tv_busca.insert("", "end", iid=str(i), values=(c["nome"],))
            self.var_status_busca.set(
                f"{len(cidades)} cidade(s). Duplo-clique ou use '➕ Adicionar ao Grafo'."
            )

        self._tarefa_rede(lambda: self.servico_mapas.buscar_cidades(consulta), mostrar)

    def _add_cidade_real(self) -> None:
        sel = self.tv_busca.selection()
        if not sel:
            self.var_status_busca.set("Selecione uma cidade nos resultados.")
            return
        c = self.resultados_busca[int(sel[0])]
        if self._salvar(lambda g: g.adicionar_ou_atualizar_cidade(c["nome"], c["latitude"], c["longitude"])):
            self.var_status_busca.set(f"'{c['nome']}' adicionada ao grafo!")

    # ─── Estradas ────────────────────────────────────────────────────────────

    def _auto_preencher_estrada(self) -> None:
        """Consulta e preenche a distância real por rodovias (OSRM) ao escolher origem e destino."""
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

        self._consulta_estrada_seq += 1
        seq_atual = self._consulta_estrada_seq
        self._geometria_pendente = None

        self.lbl_dica_dist.configure(
            text="⏳ Medindo distância real pelas estradas (via OSRM)...",
            foreground="#0a6efd",
        )

        def buscar():
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
                if seq_atual != self._consulta_estrada_seq:
                    return
                if self.cb_est_orig.get() != orig or self.cb_est_dest.get() != dest:
                    return

                if dados:
                    dist_km = round(dados["distancia"], 2)
                    tempo_min = round(dados["tempo"], 1)
                    self._geometria_pendente = dados.get("geometria")

                    for campo in self._campos_peso.values():
                        campo.delete(0, "end")
                    self._campos_peso["distancia"].insert(0, fmt(dist_km))
                    self._campos_peso["tempo"].insert(0, fmt(tempo_min))

                    self.lbl_dica_dist.configure(
                        text=f"🛣️ Estrada real: {fmt(dist_km)} km • Tempo estimado: {fmt(tempo_min)} min (via rodovias).",
                        foreground="#1a7a1a",
                    )
                else:
                    dist_reta = round(haversine(lat1, lon1, lat2, lon2), 2)
                    tempo_reta = round(dist_reta / 80 * 60, 1)

                    for campo in self._campos_peso.values():
                        campo.delete(0, "end")
                    self._campos_peso["distancia"].insert(0, fmt(dist_reta))
                    self._campos_peso["tempo"].insert(0, fmt(tempo_reta))

                    msg_err = str(erro) if erro else "serviço indisponível"
                    self.lbl_dica_dist.configure(
                        text=f"⚠️ Não foi possível obter trajeto rodoviário ({msg_err}). Usando linha reta: {fmt(dist_reta)} km.",
                        foreground="#cc6600",
                    )

            self.janela.after(0, atualizar_ui)

        Thread(target=buscar, daemon=True).start()

    def _salvar_estrada(self) -> None:
        orig, dest = self.cb_est_orig.get(), self.cb_est_dest.get()
        if not orig or not dest:
            messagebox.showwarning("Atenção", "Selecione origem e destino.", parent=self.janela)
            return
        if orig == dest:
            messagebox.showwarning("Atenção", "Origem e destino devem ser diferentes.", parent=self.janela)
            return

        txt_d = self._campos_peso["distancia"].get().strip()
        if not txt_d:
            # Tenta calcular automaticamente antes de reclamar
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
        except ValueError as e:
            messagebox.showerror("Valor inválido", str(e), parent=self.janela)
            return

        # Tempo: opcional (estimativa 1 min/km)
        txt_t = self._campos_peso["tempo"].get().strip()
        if txt_t:
            try:
                tempo = parse_float(txt_t, "tempo")
                if tempo <= 0:
                    raise ValueError("O tempo deve ser maior que zero.")
            except ValueError as e:
                messagebox.showerror("Valor inválido", str(e), parent=self.janela)
                return
        else:
            tempo = round(dist, 1)
            self._campos_peso["tempo"].delete(0, "end")
            self._campos_peso["tempo"].insert(0, fmt(tempo))

        geo = self._geometria_pendente or self.grafo.obter_geometria(orig, dest)
        if self._salvar(
            lambda g: g.adicionar_estrada(
                orig, dest, distancia=dist, custo=0.0, tempo=tempo,
                bidirecional=self.var_maodupla.get(), geometria=geo,
            )
        ):
            self._geometria_pendente = None
            self._limpar_campos_estrada()

    def _rem_estrada(self) -> None:
        sel = self.tv_estradas.selection()
        if not sel:
            messagebox.showinfo("Excluir", "Selecione uma estrada na lista.", parent=self.janela)
            return
        e = self.grafo.estradas[int(sel[0])]
        if messagebox.askyesno("Confirmar", f"Apagar estrada '{e.origem}' → '{e.destino}'?", parent=self.janela):
            self._salvar(lambda g: g.remover_estrada(e.origem, e.destino))
            self._limpar_campos_estrada()

    def _limpar_campos_estrada(self) -> None:
        self._geometria_pendente = None
        self.tv_estradas.selection_remove(*self.tv_estradas.selection())
        for campo in self._campos_peso.values():
            campo.delete(0, "end")
        self.var_maodupla.set(True)

    def _sel_estrada(self, _=None) -> None:
        sel = self.tv_estradas.selection()
        if not sel:
            return
        e = self.grafo.estradas[int(sel[0])]
        self.cb_est_orig.set(e.origem)
        self.cb_est_dest.set(e.destino)
        self.var_maodupla.set(e.bidirecional)
        for k, campo in self._campos_peso.items():
            campo.delete(0, "end")
            campo.insert(0, str(getattr(e, k)).replace(".", ","))

    # -----------------------------------------------------------------------
    # ABA 3 — Adjacência
    # -----------------------------------------------------------------------

    def _aba_adjacencia(self, pai: ttk.Frame) -> None:
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
    # Mapa
    # -----------------------------------------------------------------------

    def _abrir_mapa(self, rota=None) -> None:
        if rota and any(c not in self.grafo.coordenadas for c in rota.cidades):
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
        """Aplica `alterar(grafo_cópia)` e grava o JSON atomicamente."""
        try:
            copia = Grafo.de_dict(self.grafo.para_dict())
            alterar(copia)
            conteudo = json.dumps(copia.para_dict(), ensure_ascii=False, indent=2, allow_nan=False)
            tmp = self.arquivo.with_suffix(".tmp")
            tmp.write_text(conteudo, encoding="utf-8")
            tmp.replace(self.arquivo)
        except (ValueError, OSError) as e:
            messagebox.showerror("Erro ao salvar", str(e), parent=self.janela)
            return False
        self.grafo = copia
        self._atualizar()
        return True

    def _atualizar(self) -> None:
        """Sincroniza toda a interface com o estado atual do grafo."""
        cidades = self.grafo.cidades

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
        for idx, e in enumerate(self.grafo.estradas):
            sentido = "↔ Ida e volta" if e.bidirecional else "→ Só ida"
            self.tv_estradas.insert("", "end", iid=str(idx), values=(
                e.origem, e.destino,
                fmt(e.distancia), fmt(e.tempo),
                sentido,
            ))

        # Status bar
        if cidades:
            self.status.set(
                f"{len(cidades)} cidade(s) • {len(self.grafo.estradas)} estrada(s) • {self.arquivo.name}"
            )
        else:
            self.status.set(
                "Grafo vazio — cadastre cidades na aba 'Gerenciar Cidades e Estradas'."
            )

        self._limpar_rota()
        self._atualizar_adjacencia()

    def _atualizar_adjacencia(self) -> None:
        criterio = CRITERIOS[self.cb_adj_criterio.get()]
        unidade  = UNIDADES[criterio]

        # Lista
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

        # Matriz
        cidades, matriz = self.grafo.matriz_adjacencia(criterio)
        if not cidades:
            self._escrever(self.txt_matriz, "Nenhuma cidade cadastrada.")
            return

        vals = [["∞" if math.isinf(p) else fmt(p) for p in row] for row in matriz]
        col_w = max(8, *(len(c) for c in cidades), *(len(v) for row in vals for v in row)) + 2
        cab = " " * col_w + "".join(c.rjust(col_w) for c in cidades)
        sep = "-" * len(cab)
        rows = [c.ljust(col_w) + "".join(v.rjust(col_w) for v in row) for c, row in zip(cidades, vals)]
        self._escrever(
            self.txt_matriz,
            f"Matriz de Adjacência  [{unidade}]\n\n{cab}\n{sep}\n" + "\n".join(rows),
        )

    def _fechar(self) -> None:
        if self.servidor_mapa:
            self.servidor_mapa.fechar()
        self.janela.destroy()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
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
    main()
