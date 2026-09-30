"""Página Streamlit de analytics da PREVTEV sem o Score Analytics."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPORT_SCRIPT = PROJECT_ROOT / "scripts" / "Python" / "export.py"
EXPORT_JSON = PROJECT_ROOT / "export" / "export.json"
CALCULADORAS = ["SCORE Caprini", "Padua", "Improve", "ImproveDD", "RCOG"]
PRESETS_PERIODO = ["Todo o período", "Esta semana", "Este mês", "Mês anterior", "Este ano", "Personalizado"]


st.set_page_config(page_title="Analytics PREVTEV - Itens 1 a 3", layout="wide")


def run_export() -> tuple[bool, str]:
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


@st.cache_data(show_spinner=False)
def load_raw_data(json_path: str, mtime: float) -> dict:
    with open(json_path, "r", encoding="utf-8") as file:
        return json.load(file)


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


def filtrar_medicos_unicos(df_usuarios: pd.DataFrame) -> pd.DataFrame:
    df_medicos = df_usuarios[
        ["user_id", "nomeCompleto", "email", "profissionalSaude", "crmCpf", "nomeCalculadora", "preenchimento_dt"]
    ].copy()
    df_medicos = df_medicos[df_medicos["profissionalSaude"] == True]  # noqa: E712
    df_medicos = df_medicos[df_medicos["email"].notnull()]
    df_medicos = df_medicos[df_medicos["email"] != "NI"]
    return df_medicos.drop_duplicates(subset="crmCpf", keep="first")


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
    st.dataframe(df_lista_medicos, width="stretch")

    buffer = BytesIO()
    df_lista_medicos.to_excel(buffer, index=False)
    st.download_button(
        "Baixar lista de médicos (.xlsx)",
        data=buffer.getvalue(),
        file_name=f"lista_medicos_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="download_medicos_unicos_sem_score",
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
    st.dataframe(df_todos_registros, width="stretch")

    buffer_todos = BytesIO()
    df_todos_registros.to_excel(buffer_todos, index=False)
    st.download_button(
        "Baixar todos os registros (.xlsx)",
        data=buffer_todos.getvalue(),
        file_name=f"todos_registros_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="download_todos_registros_sem_score",
    )


def secao_dataset_calculadoras(df_usuarios: pd.DataFrame) -> None:
    st.header("2. Dataset de uso e de respostas")
    if df_usuarios.empty:
        st.warning("Nenhum dado disponível para montar o dataset de calculadoras no período selecionado.")
        return

    resumo = [
        {"Calculadora": nome, "Registros": int((df_usuarios["nomeCalculadora"] == nome).sum())}
        for nome in CALCULADORAS
    ]
    st.dataframe(pd.DataFrame(resumo), width="stretch", hide_index=True)


def secao_analytics_gerais(df_usuarios: pd.DataFrame) -> None:
    st.header("1. Analytics Gerais")
    if df_usuarios.empty:
        st.warning("Nenhum dado disponível para o período selecionado.")
        return

    contagem_calc = df_usuarios["nomeCalculadora"].value_counts().reindex(CALCULADORAS, fill_value=0)
    mais_consumida = contagem_calc.idxmax()
    menos_consumida = contagem_calc.idxmin()
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


def _calcular_preset_periodo(preset: str, data_min: date, data_max: date) -> tuple[date, date]:
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


def main() -> None:
    st.title("Analytics PREVTEV")

    with st.sidebar:
        st.header("Dados")
        if st.button("Atualizar dados (Firestore)", width="stretch"):
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
        preset = st.radio(
            "Período de análise",
            options=PRESETS_PERIODO,
            index=0,
            key="preset_periodo_sem_score",
        )

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

    secao_analytics_gerais(df_usuarios)
    secao_dataset_calculadoras(df_usuarios)
    secao_lista_medicos(df_usuarios)


if __name__ == "__main__":
    main()
