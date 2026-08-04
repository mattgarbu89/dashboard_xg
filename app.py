import io
import re
import zipfile
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

# -----------------------------------------------------------------------------
# CONFIGURAZIONE PAGINA STREAMLIT
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Dashboard Analitica 26 Mercati - Incroci Gemini",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("⚽ Dashboard Analitica Completa - Database Incroci Gemini")
st.markdown(
    "Analisi statistica globale su tutti i **26 mercati di scommessa**, con tracciamento di **Win Rate Globale, Quota Fair, Ritardo Attuale/Max, Medie Mobili (MM Attuale, Min, Max) e Scostamento di Sottoperformance**."
)

# -----------------------------------------------------------------------------
# 1. FUNZIONI DI CARICAMENTO E PULIZIA DATI DA GOOGLE DRIVE
# -----------------------------------------------------------------------------
LINK_GOOGLE_DRIVE = "https://docs.google.com/spreadsheets/d/1xmLiTz2YDi7XSKHwli1noUTgc2F0xxIxS5NJJ4digCE/edit?usp=sharing"


def get_drive_direct_url(url):
    file_id_match = re.search(r"/d/([a-zA-Z0-9_-]+)", url)
    if file_id_match:
        return f"https://docs.google.com/spreadsheets/d/{file_id_match.group(1)}/export?format=xlsx"
    return url


def clean_numeric(series):
    return pd.to_numeric(
        series.astype(str).str.replace(",", "."), errors="coerce"
    )


@st.cache_data(ttl=600)
def load_data():
    url_direct = get_drive_direct_url(LINK_GOOGLE_DRIVE)
    res = requests.get(url_direct)

    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(res.content), "r") as zin:
        with zipfile.ZipFile(output, "w") as zout:
            for item in zin.infolist():
                buffer = zin.read(item.filename)
                if "sheet" in item.filename and item.filename.endswith(".xml"):
                    buffer = re.sub(b"<autoFilter[^>]*/>", b"", buffer)
                    buffer = re.sub(
                        b"<autoFilter[^>]*>.*?</autoFilter>", b"", buffer
                    )
                    buffer = re.sub(
                        b"<customFilters[^>]*>.*?</customFilters>", b"", buffer
                    )
                    buffer = re.sub(b"<filter[^>]*/>", b"", buffer)
                zout.writestr(item, buffer)
    output.seek(0)

    df = pd.read_excel(output, sheet_name="INCROCI GEMINI", engine="openpyxl")
    df.columns = [str(c).strip().upper() for c in df.columns]

    df["GOL CASA"] = clean_numeric(df["GOL CASA"])
    df["GOL OSPITE"] = clean_numeric(df["GOL OSPITE"])

    # Consideriamo solo i match giocati con punteggio valido
    df_played = (
        df[df["GOL CASA"].notna() & df["GOL OSPITE"].notna()]
        .copy()
        .reset_index(drop=True)
    )
    df_played["GOL_TOT"] = df_played["GOL CASA"] + df_played["GOL OSPITE"]
    return df_played


with st.spinner("📥 Caricamento ed elaborazione dati in corso..."):
    df_played = load_data()

st.sidebar.header("⚙️ Parametri Strategia")
st.sidebar.info(
    f"📊 Match Giocati Trovati: **{len(df_played)}**", icon="ℹ️"
)

window_size = st.sidebar.slider(
    "Finestra Media Mobile (Partite)",
    min_value=5,
    max_value=50,
    value=20,
    step=5,
    help="Numero di partite recenti da considerare per il calcolo della Media Mobile.",
)

# -----------------------------------------------------------------------------
# 2. DEFINIZIONE DEI 26 MERCATI DI SCOMMESSA
# -----------------------------------------------------------------------------
markets = {
    # 1X2
    "1": df_played["GOL CASA"] > df_played["GOL OSPITE"],
    "X": df_played["GOL CASA"] == df_played["GOL OSPITE"],
    "2": df_played["GOL CASA"] < df_played["GOL OSPITE"],
    # Doppia Chance
    "1X": df_played["GOL CASA"] >= df_played["GOL OSPITE"],
    "X2": df_played["GOL CASA"] <= df_played["GOL OSPITE"],
    "12": df_played["GOL CASA"] != df_played["GOL OSPITE"],
    # Gol / NoGol
    "GOL (GG)": (df_played["GOL CASA"] > 0) & (df_played["GOL OSPITE"] > 0),
    "NOGOL (NG)": (df_played["GOL CASA"] == 0) | (df_played["GOL OSPITE"] == 0),
    # Under / Over Match
    "Over 1.5": df_played["GOL_TOT"] > 1.5,
    "Under 1.5": df_played["GOL_TOT"] < 1.5,
    "Over 2.5": df_played["GOL_TOT"] > 2.5,
    "Under 2.5": df_played["GOL_TOT"] < 2.5,
    "Over 3.5": df_played["GOL_TOT"] > 3.5,
    "Under 3.5": df_played["GOL_TOT"] < 3.5,
    # Casa
    "Gol Casa (Over 0.5 C)": df_played["GOL CASA"] > 0,
    "Under 1.5 Casa": df_played["GOL CASA"] < 1.5,
    "Over 1.5 Casa": df_played["GOL CASA"] > 1.5,
    # Ospite
    "Gol Ospite (Over 0.5 O)": df_played["GOL OSPITE"] > 0,
    "Under 1.5 Ospite": df_played["GOL OSPITE"] < 1.5,
    "Over 1.5 Ospite": df_played["GOL OSPITE"] > 1.5,
    # Combo
    "1 + Over 1.5": (df_played["GOL CASA"] > df_played["GOL OSPITE"])
    & (df_played["GOL_TOT"] > 1.5),
    "2 + Over 1.5": (df_played["GOL CASA"] < df_played["GOL OSPITE"])
    & (df_played["GOL_TOT"] > 1.5),
    "1X + Over 1.5": (df_played["GOL CASA"] >= df_played["GOL OSPITE"])
    & (df_played["GOL_TOT"] > 1.5),
    "X2 + Over 1.5": (df_played["GOL CASA"] <= df_played["GOL OSPITE"])
    & (df_played["GOL_TOT"] > 1.5),
    "1X + Gol": (df_played["GOL CASA"] >= df_played["GOL OSPITE"])
    & (df_played["GOL CASA"] > 0)
    & (df_played["GOL OSPITE"] > 0),
    "X2 + Gol": (df_played["GOL CASA"] <= df_played["GOL OSPITE"])
    & (df_played["GOL CASA"] > 0)
    & (df_played["GOL OSPITE"] > 0),
}

# -----------------------------------------------------------------------------
# 3. ENGINE ANALITICO (RITARDI E MEDIE MOBILI)
# -----------------------------------------------------------------------------
analysis_results = []
mm_curves = {}

for m_name, condition in markets.items():
    s_bool = condition.astype(int)
    tot_match = len(s_bool)
    tot_ok = int(s_bool.sum())
    wr_globale = (tot_ok / tot_match) * 100 if tot_match > 0 else 0.0
    quota_fair = round(100 / wr_globale, 2) if wr_globale > 0 else 0.0

    # --- Calcolo Ritardi ---
    arr = s_bool.values

    # Ritardo Attuale
    ritardo_attuale = 0
    for val in reversed(arr):
        if val == 0:
            ritardo_attuale += 1
        else:
            break

    # Ritardo Max Storico
    ritardo_max = 0
    current_ritardo = 0
    for val in arr:
        if val == 0:
            current_ritardo += 1
            if current_ritardo > ritardo_max:
                ritardo_max = current_ritardo
        else:
            current_ritardo = 0

    # --- Calcolo Medie Mobili (MM) ---
    mm_series = s_bool.rolling(window=window_size).mean() * 100
    mm_curves[m_name] = mm_series

    mm_attuale = mm_series.iloc[-1] if not mm_series.empty else 0.0
    mm_min = mm_series.min() if not mm_series.empty else 0.0
    mm_max = mm_series.max() if not mm_series.empty else 0.0

    scostamento = mm_attuale - wr_globale

    analysis_results.append({
        "Mercato": m_name,
        "Match Tot": tot_match,
        "Eventi OK": tot_ok,
        "WR Globale (%)": round(wr_globale, 2),
        "Quota Fair": quota_fair,
        "Ritardo Att": ritardo_attuale,
        "Ritardo Max": ritardo_max,
        "MM Att (%)": round(mm_attuale, 2),
        "MM Min (%)": round(mm_min, 2),
        "MM Max (%)": round(mm_max, 2),
        "Scostamento (%)": round(scostamento, 2),
    })

df_out = pd.DataFrame(analysis_results)

# -----------------------------------------------------------------------------
# 4. TABELLA RIASSUNTIVA E FILTRI INTERATTIVI
# -----------------------------------------------------------------------------
st.subheader("📋 Matrice Statistica Completa dei 26 Mercati")

# Filtro rapido di ricerca mercato
search_market = st.multiselect(
    "Filtra per Mercati specifici:",
    options=list(markets.keys()),
    default=list(markets.keys()),
)

df_filtered = df_out[df_out["Mercato"].isin(search_market)].sort_values(
    by="WR Globale (%)", ascending=False
)

# Formattazione per la visualizzazione nella tabella Streamlit
st.dataframe(
    df_filtered.style.format({
        "WR Globale (%)": "{:.2f}%",
        "Quota Fair": "{:.2f}",
        "MM Att (%)": "{:.2f}%",
        "MM Min (%)": "{:.2f}%",
        "MM Max (%)": "{:.2f}%",
        "Scostamento (%)": "{:+.2f}%",
    }).background_gradient(subset=["Scostamento (%)"], cmap="RdYlGn"),
    use_container_width=True,
    height=450,
)

# -----------------------------------------------------------------------------
# 5. GRAFICI INTERATTIVI STREAMLIT
# -----------------------------------------------------------------------------
st.subheader("📊 Visual Analytics & Opportunità di Valore")

col1, col2 = st.columns(2)

with col1:
    # Grafico 1: Win Rate Globale vs Media Mobile Attuale
    fig_bar = go.Figure()

    fig_bar.add_trace(
        go.Bar(
            x=df_filtered["Mercato"],
            y=df_filtered["WR Globale (%)"],
            name="WR Globale (%)",
            marker_color="rgb(55, 83, 109)",
        )
    )

    fig_bar.add_trace(
        go.Bar(
            x=df_filtered["Mercato"],
            y=df_filtered["MM Att (%)"],
            name=f"MM Attuale ({window_size}p) (%)",
            marker_color="rgb(26, 118, 255)",
        )
    )

    fig_bar.update_layout(
        title="Win Rate Globale vs Media Mobile Recente",
        xaxis_title="Mercato",
        yaxis_title="Percentuale (%)",
        barmode="group",
        template="plotly_white",
        height=400,
    )
    st.plotly_chart(fig_bar, use_container_width=True)

with col2:
    # Grafico 2: Scostamento Percentuale (Analisi Sottoperformance)
    df_scost = df_filtered.sort_values(by="Scostamento (%)", ascending=True)

    fig_scost = px.bar(
        df_scost,
        x="Mercato",
        y="Scostamento (%)",
        color="Scostamento (%)",
        color_continuous_scale="RdYlGn",
        title=f"Scostamento MM {window_size}p vs WR Globale (Valori negativi = Sottoperformance)",
        template="plotly_white",
        height=400,
    )
    st.plotly_chart(fig_scost, use_container_width=True)

# -----------------------------------------------------------------------------
# 6. SELEZIONE AVANZATA TREND TEMPORALE (CURVE MEDIE MOBILI)
# -----------------------------------------------------------------------------
st.subheader("📈 Analisi Trend Temporale Medie Mobili")

selected_trend_markets = st.multiselect(
    "Seleziona i mercati da visualizzare sul grafico di trend:",
    options=list(markets.keys()),
    default=["1", "X", "2", "Over 2.5", "GOL (GG)"],
)

if selected_trend_markets:
    fig_trends = go.Figure()
    for m in selected_trend_markets:
        fig_trends.add_trace(
            go.Scatter(
                y=mm_curves[m], mode="lines", name=f"MM {window_size}p - {m}"
            )
        )

    fig_trends.update_layout(
        title=f"Andamento Storico Media Mobile ({window_size} Partite)",
        xaxis_title="Match Progressivo",
        yaxis_title="Win Rate Mobile (%)",
        template="plotly_white",
        height=450,
    )
    st.plotly_chart(fig_trends, use_container_width=True)

st.success(
    "✅ Aggiornamento completato. Il file è pronto per essere salvato ed eseguito su GitHub/Streamlit!"
)
