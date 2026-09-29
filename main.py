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
    "Menor custo (R$)": "custo",
    "Menor tempo (min)": "tempo",
}
UNIDADES = {"distancia": "km", "custo": "R$", "tempo": "min"}


def numero(valor):
    return f"{valor:.2f}".replace(".", ",")


class Aplicacao:
    def __init__(self, janela, arquivo=ARQUIVO_DADOS):
        self.janela = janela
        self.arquivo = Path(arquivo)
        self.grafo = Grafo()
        self.servico_mapas = ServicoMapas()
        self.servidor_mapa = None
        self.fila_mapa = Queue()
        self.ocupado_mapa = False
        self.ultima_rota = None
        if self.arquivo.exists():
            self.grafo = Grafo.de_dict(
                json.loads(self.arquivo.read_text(encoding="utf-8"))
            )

        janela.title("Sistema de Rotas entre Cidades")
        janela.geometry("1040x720")
        janela.minsize(880, 620)
        estilo = ttk.Style(janela)
        estilo.theme_use("clam")
        estilo.configure("TLabel", font=("Segoe UI", 10))
        estilo.configure("TButton", font=("Segoe UI", 10), padding=7)
        estilo.configure("Treeview", font=("Segoe UI", 10), rowheight=27)
        estilo.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
        estilo.configure("Titulo.TLabel", font=("Segoe UI", 21, "bold"))
        estilo.configure("Resumo.TLabel", font=("Segoe UI", 12))

        corpo = ttk.Frame(janela, padding=20)
        corpo.pack(fill="both", expand=True)
        ttk.Label(corpo, text="Rotas entre cidades", style="Titulo.TLabel").pack(
            anchor="w"
        )
        ttk.Label(
            corpo, text="Pesquise cidades no mapa ou cadastre sua própria rede de estradas."
        ).pack(anchor="w", pady=(4, 16))

        abas = ttk.Notebook(corpo)
        abas.pack(fill="both", expand=True)
        cadastro = ttk.Frame(abas, padding=16)
        rotas = ttk.Frame(abas, padding=16)
        adjacencia = ttk.Frame(abas, padding=16)
        mapa = ttk.Frame(abas, padding=16)
        abas.add(mapa, text="  Mapa e cidades reais  ")
        abas.add(cadastro, text="  Cidades e estradas  ")
        abas.add(rotas, text="  Melhor rota  ")
        abas.add(adjacencia, text="  Lista e matriz de adjacência  ")

        self.seletores_cidades = []
        self.criar_cadastro(cadastro)
        self.criar_rotas(rotas)
        self.criar_adjacencia(adjacencia)
        self.criar_mapa(mapa)
        self.status = tk.StringVar()
        ttk.Label(corpo, textvariable=self.status).pack(anchor="w", pady=(12, 0))
        self.atualizar()
        self.janela.protocol("WM_DELETE_WINDOW", self.fechar)

    def seletor_cidade(self, pai, titulo, coluna):
        ttk.Label(pai, text=titulo).grid(row=0, column=coluna, sticky="w")
        seletor = ttk.Combobox(pai, state="readonly", width=23)
        seletor.grid(row=1, column=coluna, sticky="ew", padx=(0, 12), pady=(5, 12))
        pai.columnconfigure(coluna, weight=1)
        self.seletores_cidades.append(seletor)
        return seletor

    @staticmethod
    def tabela(pai, colunas, altura=8):
        quadro = ttk.Frame(pai)
        quadro.pack(fill="both", expand=True)
        tabela = ttk.Treeview(quadro, columns=tuple(colunas), show="headings", height=altura)
        for chave, titulo in colunas.items():
            tabela.heading(chave, text=titulo)
            tabela.column(chave, width=120, minwidth=70, anchor="w")
        vertical = ttk.Scrollbar(quadro, orient="vertical", command=tabela.yview)
        horizontal = ttk.Scrollbar(quadro, orient="horizontal", command=tabela.xview)
        tabela.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        tabela.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        quadro.rowconfigure(0, weight=1)
        quadro.columnconfigure(0, weight=1)
        return tabela

    @staticmethod
    def texto(pai):
        quadro = ttk.Frame(pai)
        quadro.pack(fill="both", expand=True, pady=(10, 0))
        texto = tk.Text(
            quadro, wrap="none", font=("Consolas", 11), height=10,
            background="#ffffff", foreground="#172b4d", relief="flat",
            padx=12, pady=12, state="disabled",
        )
        vertical = ttk.Scrollbar(quadro, orient="vertical", command=texto.yview)
        horizontal = ttk.Scrollbar(quadro, orient="horizontal", command=texto.xview)
        texto.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        texto.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        quadro.rowconfigure(0, weight=1)
        quadro.columnconfigure(0, weight=1)
        return texto

    @staticmethod
    def escrever(campo, texto):
        campo.configure(state="normal")
        campo.delete("1.0", "end")
        campo.insert("1.0", texto)
        campo.configure(state="disabled")

    def criar_cadastro(self, pai):
        cidades = ttk.LabelFrame(pai, text="Cidades", padding=12)
        cidades.pack(side="left", fill="both", padx=(0, 16))
        ttk.Label(cidades, text="Nome da cidade").pack(anchor="w")
        self.nome_cidade = ttk.Entry(cidades, width=25)
        self.nome_cidade.pack(fill="x", pady=(5, 8))
        self.nome_cidade.bind("<Return>", lambda evento: self.adicionar_cidade())
        ttk.Button(cidades, text="Adicionar cidade", command=self.adicionar_cidade).pack(
            fill="x", pady=(0, 12)
        )
        self.tabela_cidades = self.tabela(cidades, {"nome": "Cidades cadastradas"})

        estradas = ttk.LabelFrame(pai, text="Estradas", padding=12)
        estradas.pack(side="left", fill="both", expand=True)
        form = ttk.Frame(estradas)
        form.pack(fill="x")
        self.estrada_origem = self.seletor_cidade(form, "Origem", 0)
        self.estrada_destino = self.seletor_cidade(form, "Destino", 1)
        medidas = ttk.Frame(estradas)
        medidas.pack(fill="x")
        self.pesos = {}
        for coluna, (chave, titulo) in enumerate(
            (("distancia", "Distância (km)"), ("custo", "Custo (R$)"), ("tempo", "Tempo (min)"))
        ):
            ttk.Label(medidas, text=titulo).grid(row=0, column=coluna, sticky="w")
            campo = ttk.Entry(medidas, width=14)
            campo.grid(row=1, column=coluna, sticky="ew", padx=(0, 8), pady=(5, 8))
            medidas.columnconfigure(coluna, weight=1)
            self.pesos[chave] = campo
        ttk.Label(estradas, text="Use vírgula ou ponto decimal, sem separador de milhares.").pack(
            anchor="w"
        )
        self.mao_dupla = tk.BooleanVar(value=True)
        ttk.Checkbutton(estradas, text="Estrada de mão dupla", variable=self.mao_dupla).pack(anchor="w")
        ttk.Button(estradas, text="Salvar estrada", command=self.salvar_estrada).pack(
            anchor="w", pady=(8, 10)
        )
        ttk.Label(estradas, text="Selecione uma estrada para editar seus valores.").pack(
            anchor="w", pady=(0, 8)
        )
        self.tabela_estradas = self.tabela(
            estradas,
            {"origem": "Origem", "destino": "Destino", "distancia": "km", "custo": "R$", "tempo": "min", "sentido": "Sentido"},
        )
        self.tabela_estradas.bind("<<TreeviewSelect>>", self.selecionar_estrada)

    def criar_rotas(self, pai):
        form = ttk.Frame(pai)
        form.pack(fill="x")
        self.rota_origem = self.seletor_cidade(form, "Cidade de origem", 0)
        self.rota_destino = self.seletor_cidade(form, "Cidade de destino", 1)
        ttk.Label(form, text="Otimizar por").grid(row=0, column=2, sticky="w")
        self.criterio_rota = ttk.Combobox(form, values=list(CRITERIOS), state="readonly", width=25)
        self.criterio_rota.current(0)
        self.criterio_rota.grid(row=1, column=2, sticky="ew", pady=(5, 12))
        form.columnconfigure(2, weight=1)
        for seletor in (self.rota_origem, self.rota_destino, self.criterio_rota):
            seletor.bind("<<ComboboxSelected>>", lambda evento: self.limpar_rota())
        ttk.Button(pai, text="Calcular melhor rota", command=self.calcular_rota).pack(anchor="w")
        self.botao_rota_mapa = ttk.Button(
            pai, text="Ver rota no mapa", command=lambda: self.abrir_mapa(self.ultima_rota), state="disabled"
        )
        self.botao_rota_mapa.pack(anchor="w", pady=(6, 0))
        self.resumo_rota = tk.StringVar()
        ttk.Label(pai, textvariable=self.resumo_rota, style="Resumo.TLabel").pack(
            anchor="w", pady=(20, 8)
        )
        self.resultado_rota = self.texto(pai)

    def criar_adjacencia(self, pai):
        ttk.Label(pai, text="Peso exibido nas conexões").pack(anchor="w")
        self.criterio_adjacencia = ttk.Combobox(
            pai, values=list(CRITERIOS), state="readonly", width=28
        )
        self.criterio_adjacencia.current(0)
        self.criterio_adjacencia.pack(anchor="w", pady=(5, 12))
        self.criterio_adjacencia.bind("<<ComboboxSelected>>", lambda evento: self.atualizar_adjacencia())
        ttk.Label(pai, text="∞ indica ausência de estrada direta; a diagonal da matriz vale zero.").pack(anchor="w")
        abas = ttk.Notebook(pai)
        abas.pack(fill="both", expand=True, pady=(12, 0))
        lista = ttk.Frame(abas)
        matriz = ttk.Frame(abas)
        abas.add(lista, text=" Lista de adjacência ")
        abas.add(matriz, text=" Matriz de adjacência ")
        self.texto_lista = self.texto(lista)
        self.texto_matriz = self.texto(matriz)

    def criar_mapa(self, pai):
        ttk.Label(pai, text="Pesquise cidades reais e importe o trajeto de carro sugerido pelo OSRM.", style="Resumo.TLabel").pack(anchor="w")
        busca = ttk.Frame(pai)
        busca.pack(fill="x", pady=(12, 8))
        ttk.Label(busca, text="Cidade / estado / país:").pack(side="left", padx=(0, 8))
        self.busca_cidade = ttk.Entry(busca)
        self.busca_cidade.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.busca_cidade.bind("<Return>", lambda evento: self.buscar_cidade_mapa())
        botao_busca = ttk.Button(busca, text="Buscar", command=self.buscar_cidade_mapa)
        botao_busca.pack(side="left")
        self.resultados_busca = []
        self.lista_busca = self.tabela(pai, {"cidade": "Resultados — selecione uma cidade"}, altura=5)
        selecao = ttk.Frame(pai)
        selecao.pack(fill="x", pady=8)
        self.botoes_mapa = [botao_busca]
        for tipo in ("origem", "destino"):
            botao = ttk.Button(selecao, text=f"Usar como {tipo}", command=lambda t=tipo: self.selecionar_cidade_mapa(t))
            botao.pack(side="left", padx=(0, 8))
            self.botoes_mapa.append(botao)
        self.cidades_mapa = {"origem": None, "destino": None}
        self.rotulos_mapa = {}
        for tipo in self.cidades_mapa:
            texto = tk.StringVar(value=f"{tipo.capitalize()}: selecione uma cidade")
            self.rotulos_mapa[tipo] = texto
            ttk.Label(pai, textvariable=texto, wraplength=880).pack(anchor="w", pady=3)
        custo = ttk.Frame(pai)
        custo.pack(fill="x", pady=(12, 4))
        ttk.Label(custo, text="Custo estimado por km (R$):").pack(side="left", padx=(0, 8))
        self.custo_km = ttk.Entry(custo, width=12)
        self.custo_km.insert(0, "0,50")
        self.custo_km.pack(side="left")
        ttk.Label(pai, text="Ajuste o custo por km. A estimativa não inclui pedágios; o tempo não inclui trânsito ao vivo.").pack(anchor="w")
        acoes = ttk.Frame(pai)
        acoes.pack(fill="x", pady=10)
        importar = ttk.Button(acoes, text="Consultar trajeto e cadastrar", command=self.importar_trajeto)
        importar.pack(side="left", padx=(0, 8))
        self.botoes_mapa.append(importar)
        ttk.Button(acoes, text="Abrir mapa do cadastro", command=self.abrir_mapa).pack(side="left")
        self.status_mapa = tk.StringVar(value="Busca: Photon • Estradas: OSRM • Mapa: Leaflet / OpenStreetMap • Requer internet")
        ttk.Label(pai, textvariable=self.status_mapa, wraplength=880).pack(anchor="w", pady=4)
        ttk.Label(
            pai, wraplength=880,
            text="Cada consulta importa o trajeto no sentido escolhido. Consulte o sentido inverso para cadastrar a volta. "
                 "Dijkstra compara os caminhos da rede que você importou ou cadastrou.",
        ).pack(anchor="w", pady=(5, 0))

    def tarefa_mapa(self, tarefa, ao_concluir):
        if self.ocupado_mapa:
            return
        self.ocupado_mapa = True
        for botao in self.botoes_mapa:
            botao.configure(state="disabled")

        def executar():
            try:
                self.fila_mapa.put((tarefa(), None))
            except Exception as erro:
                self.fila_mapa.put((None, erro))

        def verificar():
            try:
                resultado, erro = self.fila_mapa.get_nowait()
            except Empty:
                self.janela.after(100, verificar)
                return
            self.ocupado_mapa = False
            for botao in self.botoes_mapa:
                botao.configure(state="normal")
            if erro is not None:
                self.status_mapa.set(f"Não foi possível concluir: {erro}")
            else:
                ao_concluir(resultado)

        Thread(target=executar, daemon=True).start()
        self.janela.after(100, verificar)

    def buscar_cidade_mapa(self):
        if self.ocupado_mapa:
            return
        consulta = self.busca_cidade.get().strip()
        if len(consulta) < 2:
            self.status_mapa.set("Digite o nome da cidade; acrescente estado e país para refinar a busca.")
            return
        self.lista_busca.delete(*self.lista_busca.get_children())
        self.resultados_busca = []
        self.status_mapa.set("Pesquisando cidades…")

        def mostrar(cidades):
            self.resultados_busca = cidades
            for indice, cidade in enumerate(cidades):
                self.lista_busca.insert("", "end", iid=str(indice), values=(cidade["nome"],))
            self.status_mapa.set(f"{len(cidades)} cidade(s) encontrada(s). Selecione a origem ou o destino.")

        self.tarefa_mapa(lambda: self.servico_mapas.buscar_cidades(consulta), mostrar)

    def selecionar_cidade_mapa(self, tipo):
        selecao = self.lista_busca.selection()
        if not selecao:
            self.status_mapa.set("Selecione uma cidade nos resultados da busca.")
            return
        cidade = self.resultados_busca[int(selecao[0])]
        self.cidades_mapa[tipo] = cidade
        self.rotulos_mapa[tipo].set(f"{tipo.capitalize()}: {cidade['nome']}")

    def importar_trajeto(self):
        if self.ocupado_mapa:
            return
        origem, destino = self.cidades_mapa.values()
        if origem is None or destino is None or origem == destino:
            self.status_mapa.set("Selecione duas cidades diferentes para consultar o trajeto.")
            return
        try:
            custo_km = float(self.custo_km.get().strip().replace(",", "."))
            if not math.isfinite(custo_km) or custo_km < 0:
                raise ValueError
        except ValueError:
            self.status_mapa.set("Informe um custo por km válido, maior ou igual a zero.")
            return
        self.status_mapa.set("Consultando o trajeto rodoviário…")

        def importar(estrada):
            def alterar(grafo):
                for cidade in (origem, destino):
                    if cidade["nome"].casefold() not in {nome.casefold() for nome in grafo.cidades}:
                        grafo.adicionar_cidade(cidade["nome"], cidade["latitude"], cidade["longitude"])
                    else:
                        grafo.definir_coordenadas(cidade["nome"], cidade["latitude"], cidade["longitude"])
                grafo.adicionar_estrada(
                    origem["nome"], destino["nome"], estrada["distancia"],
                    estrada["distancia"] * custo_km, estrada["tempo"],
                    bidirecional=False, geometria=estrada["geometria"],
                )
            if self.salvar_alteracao(alterar):
                self.status_mapa.set(
                    f"Trajeto cadastrado: {numero(estrada['distancia'])} km • {numero(estrada['tempo'])} min estimados. "
                    "Use ‘Abrir mapa do cadastro’ para visualizar."
                )
            else:
                self.status_mapa.set("O trajeto foi consultado, mas não foi salvo. Verifique o erro informado.")

        self.tarefa_mapa(lambda: self.servico_mapas.consultar_estrada(origem, destino), importar)

    def abrir_mapa(self, rota=None):
        if rota and any(cidade not in self.grafo.coordenadas for cidade in rota.cidades):
            messagebox.showinfo("Mapa", "Há cidades sem coordenadas nesta rota. Importe suas localizações na aba do mapa.", parent=self.janela)
            return
        try:
            arquivo = gerar_mapa(self.grafo, self.arquivo.with_name("rotas_mapa.html"), rota)
            if self.servidor_mapa is None:
                self.servidor_mapa = ServidorMapa(arquivo)
            if not webbrowser.open(self.servidor_mapa.url):
                messagebox.showinfo("Mapa", f"Abra no navegador: {self.servidor_mapa.url}", parent=self.janela)
        except (OSError, ValueError, ErroMapa) as erro:
            messagebox.showerror("Não foi possível abrir o mapa", str(erro), parent=self.janela)

    def fechar(self):
        if self.servidor_mapa is not None:
            self.servidor_mapa.fechar()
        self.janela.destroy()

    def salvar_alteracao(self, alterar):
        try:
            atualizado = Grafo.de_dict(self.grafo.para_dict())
            alterar(atualizado)
            conteudo = json.dumps(atualizado.para_dict(), ensure_ascii=False, indent=2, allow_nan=False)
            temporario = self.arquivo.with_suffix(".tmp")
            temporario.write_text(conteudo, encoding="utf-8")
            temporario.replace(self.arquivo)
        except (ValueError, OSError) as erro:
            messagebox.showerror("Não foi possível salvar", str(erro), parent=self.janela)
            return False
        self.grafo = atualizado
        self.atualizar()
        return True

    def adicionar_cidade(self):
        if self.salvar_alteracao(lambda grafo: grafo.adicionar_cidade(self.nome_cidade.get())):
            self.nome_cidade.delete(0, "end")
            self.nome_cidade.focus_set()

    def salvar_estrada(self):
        valores = {}
        for chave, campo in self.pesos.items():
            try:
                valores[chave] = float(campo.get().strip().replace(",", "."))
            except ValueError:
                messagebox.showerror(
                    "Valor inválido", f"Informe um número válido no campo {chave}.", parent=self.janela
                )
                campo.focus_set()
                return
        origem, destino = self.estrada_origem.get(), self.estrada_destino.get()
        geometria = ()
        for estrada in self.grafo.estradas:
            if (estrada.origem, estrada.destino) == (origem, destino):
                geometria = estrada.geometria
                break
            if estrada.bidirecional and (estrada.destino, estrada.origem) == (origem, destino):
                geometria = estrada.geometria[::-1]
                break
        self.salvar_alteracao(lambda grafo: grafo.adicionar_estrada(
            origem, destino, **valores, bidirecional=self.mao_dupla.get(), geometria=geometria,
        ))

    def selecionar_estrada(self, evento=None):
        selecao = self.tabela_estradas.selection()
        if not selecao:
            return
        estrada = self.grafo.estradas[int(selecao[0])]
        self.estrada_origem.set(estrada.origem)
        self.estrada_destino.set(estrada.destino)
        self.mao_dupla.set(estrada.bidirecional)
        for chave, campo in self.pesos.items():
            campo.delete(0, "end")
            campo.insert(0, str(getattr(estrada, chave)).replace(".", ","))

    def limpar_rota(self):
        self.ultima_rota = None
        self.botao_rota_mapa.configure(state="disabled")
        self.resumo_rota.set("Selecione duas cidades e calcule uma rota.")
        self.escrever(self.resultado_rota, "")

    def calcular_rota(self):
        self.limpar_rota()
        criterio = CRITERIOS[self.criterio_rota.get()]
        try:
            rota = self.grafo.menor_rota(self.rota_origem.get(), self.rota_destino.get(), criterio)
        except ValueError as erro:
            messagebox.showerror("Não foi possível calcular", str(erro), parent=self.janela)
            return
        if rota is None:
            self.resumo_rota.set("Não existe caminho entre essas cidades.")
            self.escrever(self.resultado_rota, "Cadastre estradas que conectem as cidades escolhidas.")
            return
        self.ultima_rota = rota
        self.botao_rota_mapa.configure(state="normal")
        self.resumo_rota.set(
            f"Distância: {numero(rota.distancia)} km   |   Custo: R$ {numero(rota.custo)}   |   Tempo: {numero(rota.tempo)} min"
        )
        self.escrever(
            self.resultado_rota,
            f"{self.criterio_rota.get()} — algoritmo de Dijkstra\n\n"
            + " → ".join(rota.cidades)
            + "\n\nPercurso:\n"
            + "\n".join(f"{indice}. {cidade}" for indice, cidade in enumerate(rota.cidades, 1)),
        )

    def atualizar(self):
        cidades = self.grafo.cidades
        for seletor in self.seletores_cidades:
            seletor.configure(values=cidades)
            if seletor.get() not in cidades:
                seletor.set(cidades[0] if cidades else "")
        self.tabela_cidades.delete(*self.tabela_cidades.get_children())
        for cidade in cidades:
            self.tabela_cidades.insert("", "end", values=(cidade,))
        self.tabela_estradas.delete(*self.tabela_estradas.get_children())
        for indice, estrada in enumerate(self.grafo.estradas):
            self.tabela_estradas.insert("", "end", iid=str(indice), values=(
                estrada.origem, estrada.destino, numero(estrada.distancia), numero(estrada.custo), numero(estrada.tempo),
                "Ida e volta" if estrada.bidirecional else "Origem → destino",
            ))
        self.status.set(f"{len(cidades)} cidade(s) • {len(self.grafo.estradas)} estrada(s) • Dados salvos em {self.arquivo.name}")
        self.limpar_rota()
        self.atualizar_adjacencia()

    def atualizar_adjacencia(self):
        criterio = CRITERIOS[self.criterio_adjacencia.get()]
        unidade = UNIDADES[criterio]
        lista = self.grafo.lista_adjacencia(criterio)
        linhas = [
            f"{cidade} → " + (", ".join(f"{vizinho} ({numero(peso)} {unidade})" for vizinho, peso in vizinhos) or "sem conexões")
            for cidade, vizinhos in lista.items()
        ]
        self.escrever(self.texto_lista, "\n\n".join(linhas) or "Cadastre cidades para visualizar o grafo.")
        cidades, matriz = self.grafo.matriz_adjacencia(criterio)
        if not cidades:
            self.escrever(self.texto_matriz, "Cadastre cidades para visualizar o grafo.")
            return
        tabela = [["∞" if math.isinf(peso) else numero(peso) for peso in linha] for linha in matriz]
        largura = max(8, *(len(cidade) for cidade in cidades), *(len(valor) for linha in tabela for valor in linha)) + 2
        linhas = [f"Pesos em {unidade}\n", "".ljust(largura) + "".join(cidade.rjust(largura) for cidade in cidades)]
        linhas.extend(cidade.ljust(largura) + "".join(valor.rjust(largura) for valor in linha) for cidade, linha in zip(cidades, tabela))
        self.escrever(self.texto_matriz, "\n".join(linhas))


def main():
    janela = tk.Tk()
    janela.withdraw()
    try:
        Aplicacao(janela)
    except (OSError, ValueError, TypeError) as erro:
        messagebox.showerror(
            "Erro ao abrir os dados",
            f"Não foi possível carregar {ARQUIVO_DADOS.name}:\n{erro}\n\n"
            "Verifique ou renomeie o arquivo para iniciar um cadastro vazio.",
            parent=janela,
        )
        janela.destroy()
        return
    janela.deiconify()
    janela.mainloop()


if __name__ == "__main__":
    main()
