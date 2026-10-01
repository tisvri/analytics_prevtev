"""Página Streamlit de analytics da PREVTEV.

Executa scripts/Python/export.py para atualizar o export/export.json a partir
do Firestore e exibe as mesmas análises construídas em analytics.ipynb.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Optional

import pandas as pd
import plotly.express as px
import streamlit as st

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPORT_SCRIPT = PROJECT_ROOT / "scripts" / "Python" / "export.py"
EXPORT_JSON = PROJECT_ROOT / "export" / "export.json"

COLORS = ["#EC0E73", "#041266", "#C830A0", "#FAAFB1"]
CALCULADORAS = ["SCORE Caprini", "Padua", "Improve", "ImproveDD", "RCOG"]
# (rótulo, palavras-chave p/ localizar a pergunta, tipo de gráfico)
SOCIO_DEMO_CONFIG = [
    ("Sexo", ["sexo"], "pie"),
    ("Idade", ["idade"], "bar"),
    ("Altura", ["altura"], "hist"),
    ("Peso", ["peso"], "hist"),
    ("IMC / Obesidade", ["imc", "obesidade"], "bar"),
]

st.set_page_config(page_title="Analytics PREVTEV", layout="wide")


# ── Execução do export ────────────────────────────────────────────────────────
def run_export() -> tuple[bool, str]:
    """Roda scripts/Python/export.py e retorna (sucesso, saida/erro)."""
    process_env = os.environ.copy()
    secret_names = (
        "TYPE",
        "PROJECT_ID",
        "PRIVATE_KEY_ID",
        "PRIVATE_KEY",
        "CLIENT_EMAIL",
        "CLIENT_ID",
        "AUTH_URI",
        "TOKEN_URI",
        "AUTH_PROVIDER_X509_CERT_URL",
        "CLIENT_X509_CERT_URL",
        "UNIVERSE_DOMAIN",
        "OUT_FILE",
        "ROOT_COLLECTION",
        "FIRESTORE_TRANSPORT",
        "EXPORT_SUBCOLLECTIONS",
    )

    try:
        for name in secret_names:
            if name in st.secrets:
                process_env[name] = str(st.secrets[name])
    except Exception:  # noqa: BLE001
        pass

    process_env["FIRESTORE_TRANSPORT"] = "grpc"
    process_env.setdefault("EXPORT_SUBCOLLECTIONS", "false")

    try:
        result = subprocess.run(
            [sys.executable, str(EXPORT_SCRIPT)],
            cwd=PROJECT_ROOT,
            env=process_env,
            capture_output=True,
            text=True,
            timeout=900,
        )
        if result.returncode != 0:
            return False, result.stderr or result.stdout
        return True, result.stdout
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


# ── Carga e preparação dos dados ──────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def load_raw_data(json_path: str, mtime: float) -> dict:
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_df_usuarios(data: dict) -> pd.DataFrame:
    df = pd.DataFrame.from_dict(data["usuarios"], orient="index").reset_index()
    df = df.rename(columns={"index": "user_id"})
    df = df.dropna(subset=["createdAt"])
    df = df[df["questions"].apply(str) != "[]"]
    df["preenchimento_dt"] = pd.to_datetime(df["createdAt"], format="ISO8601", errors="coerce")
    df["preenchimento_dt"] = df["preenchimento_dt"].dt.strftime("%Y-%m-%d")
    df = df.astype({"preenchimento_dt": "datetime64[ns]"})
    df = df[df["preenchimento_dt"] >= datetime.strptime("2025-07-01T01:26:36.610Z", "%Y-%m-%dT%H:%M:%S.%fZ")]
    return df


def build_resultado(df_usuarios: pd.DataFrame) -> pd.DataFrame:
    df_aux = df_usuarios[["user_id", "questions", "nomeCalculadora", "preenchimento_dt", "resultado"]].explode(
        "questions"
    )
    df_pergunta_resp = pd.json_normalize(df_aux["questions"])
    df_pergunta_resp["user_id"] = df_aux["user_id"].values
    df_pergunta_resp["nomeCalculadora"] = df_aux["nomeCalculadora"].values
    df_pergunta_resp["preenchimento_dt"] = df_aux["preenchimento_dt"].values
    df_pergunta_resp["Answer"] = df_pergunta_resp["selectedOption"].combine_first(df_pergunta_resp["selectedValue"])
    df_pergunta_resp["resultado"] = df_aux["resultado"].values

    resultado = df_pergunta_resp[
        ["user_id", "nomeCalculadora", "preenchimento_dt", "question", "Answer", "resultado"]
    ].copy()
    resultado = resultado[
        ~resultado["question"].isin(
            ["Fatores de risco pré-existentes", "Fatores de risco obstétricos", "Fatores de risco transitórios"]
        )
    ]
    resultado["question"] = resultado["question"].str.strip()
    resultado = resultado.dropna(subset=["nomeCalculadora"])
    resultado["mes_ano"] = resultado["preenchimento_dt"].dt.to_period("M").astype(str)
    return resultado


# ── Seções da página ──────────────────────────────────────────────────────────
# 3. Lista de médicos
def filtrar_medicos_unicos(df_usuarios: pd.DataFrame) -> pd.DataFrame:
    """Retorna apenas profissionais de saúde com email válido, deduplicados por crmCpf."""
    df_medicos = df_usuarios[
        ["user_id", "nomeCompleto", "email", "profissionalSaude", "crmCpf", "nomeCalculadora", "preenchimento_dt"]
    ].copy()
    df_medicos = df_medicos[df_medicos["profissionalSaude"] == True]  # noqa: E712
    df_medicos = df_medicos[df_medicos["email"].notnull()]
    df_medicos = df_medicos[df_medicos["email"] != "NI"]
    df_medicos = df_medicos.drop_duplicates(subset="crmCpf", keep="first")
    return df_medicos


def secao_lista_medicos(df_usuarios: pd.DataFrame) -> None:
    st.header("3. Lista de Médicos")

    st.subheader("3.1 Médicos únicos (deduplicados por CRM/CPF)")
    df_lista_medicos = filtrar_medicos_unicos(df_usuarios)
    df_lista_medicos = df_lista_medicos.drop(columns=["user_id", "nomeCalculadora"])
    df_lista_medicos = df_lista_medicos.rename(columns={
        "nomeCompleto": "Nome Completo",
        "email": "Email",
        "profissionalSaude": "Profissional de Saúde",
        "crmCpf": "CRM/CPF",
        "preenchimento_dt": "Data de Preenchimento",
    })

    st.dataframe(df_lista_medicos, width='stretch')

    buffer = BytesIO()
    df_lista_medicos.to_excel(buffer, index=False)
    st.download_button(
        "Baixar lista de médicos (.xlsx)",
        data=buffer.getvalue(),
        file_name=f"lista_medicos_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="download_medicos_unicos",
    )

    st.subheader("3.2 Todos os registros (sem exclusão de duplicatas)")
    df_todos_registros = df_usuarios[
        ["user_id", "nomeCompleto", "email", "profissionalSaude", "crmCpf", "nomeCalculadora", "preenchimento_dt"]
    ].copy()
    df_todos_registros = df_todos_registros.rename(columns={
        "user_id": "Usuário",
        "nomeCompleto": "Nome Completo",
        "email": "Email",
        "profissionalSaude": "Profissional de Saúde",
        "crmCpf": "CRM/CPF",
        "nomeCalculadora": "Score",
        "preenchimento_dt": "Data de Preenchimento",
    })

    st.dataframe(df_todos_registros, width='stretch')

    buffer_todos = BytesIO()
    df_todos_registros.to_excel(buffer_todos, index=False)
    st.download_button(
        "Baixar todos os registros (.xlsx)",
        data=buffer_todos.getvalue(),
        file_name=f"todos_registros_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="download_todos_registros",
    )

# 2. gráfico de barras número de registros por calculadora
def secao_dataset_calculadoras(df_usuarios: pd.DataFrame) -> None:
    st.header("2. Dataset de uso e de respostas")

    if df_usuarios.empty:
        st.warning("Nenhum dado disponível para montar o dataset de calculadoras no período selecionado.")
        return

    resumo = []
    for nome in CALCULADORAS:
        resumo.append({"Calculadora": nome, "Registros": int((df_usuarios["nomeCalculadora"] == nome).sum())})

    st.dataframe(pd.DataFrame(resumo), width='stretch', hide_index=True)


# 1. cards com indicadores gerais do período selecionado
def secao_analytics_gerais(df_usuarios: pd.DataFrame) -> None:
    st.header("1. Analytics Gerais")

    if df_usuarios.empty:
        st.warning("Nenhum dado disponível para o período selecionado.")
        return

    contagem_calc = df_usuarios["nomeCalculadora"].value_counts()
    contagem_calc = contagem_calc.reindex(CALCULADORAS, fill_value=0)
    mais_consumida = contagem_calc.idxmax()
    menos_consumida = contagem_calc.idxmin()

    # pico considera apenas médicos únicos e válidos (mesmo critério da Lista de Médicos)
    df_medicos = filtrar_medicos_unicos(df_usuarios)
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Número de acessos (total)", len(df_usuarios))
    col2.metric("Calculadora mais consumida", mais_consumida, f"{contagem_calc[mais_consumida]} registros")
    col3.metric("Calculadora menos consumida", menos_consumida, f"{contagem_calc[menos_consumida]} registros")

    if df_medicos.empty:
        col4.metric("Pico de atividade", "—")
    else:
        datas_sem_horario = df_medicos["preenchimento_dt"].dt.normalize()
        contagem_por_dia = datas_sem_horario.value_counts()
        pico_dia = contagem_por_dia.idxmax()
        col4.metric("Pico de atividade", pico_dia.strftime("%d/%m/%Y"), f"{contagem_por_dia[pico_dia]} médicos")


def _match_question(perguntas: list[str], keywords: list[str]) -> Optional[str]:
    """Retorna a primeira pergunta cujo texto contenha alguma das keywords."""
    for pergunta in perguntas:
        low = pergunta.lower()
        if any(keyword in low for keyword in keywords):
            return pergunta
    return None


def plot_bar_pergunta(df_calc: pd.DataFrame, question: str, titulo: str, eixo_x: str) -> None:
    sub = df_calc[df_calc["question"] == question].copy()
    sub["Answer"] = sub["Answer"].astype(str)
    contagem = sub["Answer"].value_counts()
    contagem_numerica = pd.to_numeric(contagem.index.to_series(), errors="coerce")
    if contagem_numerica.notna().all():
        contagem = contagem.loc[contagem_numerica.sort_values().index]
    else:
        contagem = contagem.sort_index()

    fig = px.bar(x=contagem.index, y=contagem.values, color=contagem.index, color_discrete_sequence=COLORS)
    fig.update_layout(
        title=titulo,
        xaxis_title=eixo_x,
        yaxis_title="Pacientes (n)",
        xaxis_tickangle=-45,
        showlegend=False,
    )
    fig.update_traces(texttemplate="%{y}", textposition="outside")
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(showgrid=False)
    st.plotly_chart(fig, width='stretch')


def plot_pie_pergunta(df_calc: pd.DataFrame, question: str, titulo: str) -> None:
    sub = df_calc[df_calc["question"] == question].copy()
    sub["Answer"] = sub["Answer"].astype(str)
    contagem = sub["Answer"].value_counts().reset_index()
    contagem.columns = ["Resposta", "Frequência"]

    fig = px.pie(
        contagem,
        names="Resposta",
        values="Frequência",
        color="Resposta",
        color_discrete_sequence=COLORS,
    )
    fig.update_layout(title=titulo)
    st.plotly_chart(fig, width='stretch')


def plot_hist_pergunta(df_calc: pd.DataFrame, question: str, titulo: str, eixo_x: str) -> None:
    sub = df_calc[df_calc["question"] == question].copy()
    sub["Answer_num"] = pd.to_numeric(sub["Answer"], errors="coerce")
    sub = sub.dropna(subset=["Answer_num"])
    if sub.empty:
        plot_bar_pergunta(df_calc, question, titulo, eixo_x)
        return

    fig = px.histogram(sub, x="Answer_num", color_discrete_sequence=COLORS)
    fig.update_layout(title=titulo, xaxis_title=eixo_x, yaxis_title="Pacientes (n)")
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(showgrid=False)
    st.plotly_chart(fig, width='stretch')


def secao_dados_sociodemograficos(df_calc: pd.DataFrame) -> None:
    st.markdown("#### 4.1 Dados sócio-demográficos")

    perguntas = df_calc["question"].dropna().unique().tolist()
    encontrou_algum = False
    for label, keywords, tipo in SOCIO_DEMO_CONFIG:
        question = _match_question(perguntas, keywords)
        if not question:
            continue
        encontrou_algum = True
        if tipo == "pie":
            plot_pie_pergunta(df_calc, question, f"Distribuição por {label}")
        elif tipo == "hist":
            plot_hist_pergunta(df_calc, question, f"Distribuição de {label}", label)
        else:
            plot_bar_pergunta(df_calc, question, f"Distribuição por {label}", label)

    if not encontrou_algum:
        st.info("Nenhum dado sócio-demográfico disponível para esta calculadora.")


def secao_analytics_calculadora(df_calc: pd.DataFrame, nome_calculadora: str) -> None:
    st.markdown("#### 4.2 Analytics da calculadora selecionada")

    serie_temporal = (
        df_calc[["user_id", "mes_ano"]].drop_duplicates().groupby("mes_ano").size().reset_index(name="Frequência")
    )
    fig = px.line(serie_temporal, x="mes_ano", y="Frequência")
    fig.update_layout(
        title=f"Utilização de {nome_calculadora} ao longo do tempo",
        xaxis_title="Período",
        yaxis_title="Frequência",
    )
    fig.update_traces(line=dict(color="#EC0E73", width=3), marker=dict(color="#041266", size=8))
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(showgrid=False)
    st.plotly_chart(fig, width='stretch')

    perguntas = df_calc["question"].dropna().unique().tolist()
    perguntas_sociodemo = {_match_question(perguntas, keywords) for _, keywords, _ in SOCIO_DEMO_CONFIG}
    perguntas_restantes = sorted(p for p in perguntas if p not in perguntas_sociodemo)

    if not perguntas_restantes:
        st.info("Nenhuma outra pergunta disponível para esta calculadora.")
        return

    for question in perguntas_restantes:
        plot_bar_pergunta(df_calc, question, question, question)


def secao_score_analytics(resultado: pd.DataFrame) -> None:
    st.header("4. Score Analytics")
    st.write("Selecione a calculadora deseja analisar")

    if "calculadora_selecionada" not in st.session_state:
        st.session_state["calculadora_selecionada"] = None

    cols = st.columns(len(CALCULADORAS))
    for col, nome in zip(cols, CALCULADORAS):
        with col:
            selecionada = st.session_state["calculadora_selecionada"] == nome
            if st.button(
                nome, key=f"btn_calc_{nome}", width='stretch', type="primary" if selecionada else "secondary"
            ):
                st.session_state["calculadora_selecionada"] = nome

    nome_selecionado = st.session_state["calculadora_selecionada"]
    if not nome_selecionado:
        return

    df_calc = resultado[resultado["nomeCalculadora"] == nome_selecionado].copy()
    if df_calc.empty:
        st.warning(f"Nenhum registro de {nome_selecionado} encontrado no período selecionado.")
        return

    secao_dados_sociodemograficos(df_calc)
    secao_analytics_calculadora(df_calc, nome_selecionado)


PRESETS_PERIODO = ["Todo o período", "Esta semana", "Este mês", "Mês anterior", "Este ano", "Personalizado"]


def _calcular_preset_periodo(preset: str, data_min: date, data_max: date) -> tuple[date, date]:
    """Calcula (início, fim) para um preset, limitado ao intervalo real dos dados."""
    hoje = datetime.now().date()
    if preset == "Todo o período":
        inicio, fim = data_min, min(data_max, hoje)
    elif preset == "Esta semana":
        inicio, fim = hoje - timedelta(days=hoje.weekday()), hoje
    elif preset == "Este mês":
        inicio, fim = hoje.replace(day=1), hoje
    elif preset == "Mês anterior":
        fim = hoje.replace(day=1) - timedelta(days=1)
        inicio = fim.replace(day=1)
    elif preset == "Este ano":
        inicio, fim = hoje.replace(month=1, day=1), hoje
    else:
        return data_min, data_max

    inicio, fim = max(inicio, data_min), min(fim, data_max)
    return (data_min, data_max) if inicio > fim else (inicio, fim)


# ── Página principal ───────────────────────────────────────────────────────
def main() -> None:
    st.title("Analytics PREVTEV")

    with st.sidebar:
        st.header("Dados")
        if st.button("Atualizar dados (Firestore)", width='stretch'):
            with st.spinner("Executando export.py..."):
                sucesso, saida = run_export()
            if sucesso:
                st.success("Export concluído com sucesso.")
                load_raw_data.clear()
            else:
                st.error(f"Falha ao executar export.py:\n{saida}")

        if EXPORT_JSON.exists():
            mtime = datetime.fromtimestamp(EXPORT_JSON.stat().st_mtime)
            st.caption(f"Última atualização do JSON: {mtime.strftime('%d/%m/%Y %H:%M:%S')}")

    if not EXPORT_JSON.exists():
        st.error(f"Arquivo não encontrado: {EXPORT_JSON}. Clique em 'Atualizar dados' para gerá-lo.")
        return

    data = load_raw_data(str(EXPORT_JSON), EXPORT_JSON.stat().st_mtime)
    df_usuarios = build_df_usuarios(data)

    if df_usuarios.empty:
        st.warning("Nenhum registro disponível.")
        return

    data_min = df_usuarios["preenchimento_dt"].min().date()
    data_max = df_usuarios["preenchimento_dt"].max().date()
    with st.sidebar:
        st.header("Período")
        preset = st.radio("Período de análise", options=PRESETS_PERIODO, index=0, key="preset_periodo")

        if preset == "Personalizado":
            intervalo = st.date_input(
                "Data de preenchimento",
                value=(data_min, data_max),
                min_value=data_min,
                max_value=data_max,
            )
            inicio, fim = intervalo if isinstance(intervalo, tuple) and len(intervalo) == 2 else (data_min, data_max)
        else:
            inicio, fim = _calcular_preset_periodo(preset, data_min, data_max)
            st.caption(f"Período: {inicio.strftime('%d/%m/%Y')} a {fim.strftime('%d/%m/%Y')}")

    df_usuarios = df_usuarios[
        (df_usuarios["preenchimento_dt"] >= pd.Timestamp(inicio))
        & (df_usuarios["preenchimento_dt"] <= pd.Timestamp(fim))
    ]
    resultado = build_resultado(df_usuarios)

    secao_analytics_gerais(df_usuarios)
    secao_dataset_calculadoras(df_usuarios)
    secao_lista_medicos(df_usuarios)
    secao_score_analytics(resultado)


if __name__ == "__main__":
    main()
