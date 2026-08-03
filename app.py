import io
import re
import zipfile
import pandas as pd
import requests
import streamlit as st

# Configurazione pagina
st.set_page_config(
    page_title="Dashboard Strategie & Filtri xG", page_icon="⚽", layout="wide"
)

# ==========================================
# LINK GOOGLE SHEETS
# ==========================================
LINK_GOOGLE_DRIVE = "https://docs.google.com/spreadsheets/d/1xmLiTz2YDi7XSKHwli1noUTgc2F0xxIxS5NJJ4digCE/edit?usp=sharing"


def get_drive_direct_url(url):
  file_id_match = re.search(r"/d/([a-zA-Z0-9_-]+)", url)
  if file_id_match:
    file_id = file_id_match.group(1)
    return f"https://docs.google.com/spreadsheets/d/{file_id}/export?format=xlsx"
  return url


def clean_numeric_column(series):
  """Converte una colonna in numeri gestendo virgole e punti decimali."""
  return pd.to_numeric(
      series.astype(str).str.replace(",", "."), errors="coerce"
  )


def load_clean_df(file_bytes):
  output = io.BytesIO()
  with zipfile.ZipFile(file_bytes, "r") as zin:
    with zipfile.ZipFile(output, "w") as zout:
      for item in zin.infolist():
        buffer = zin.read(item.filename)
        if "sheet" in item.filename and item.filename.endswith(".xml"):
          buffer = re.sub(b"<autoFilter[^>]*/>", b"", buffer)
          buffer = re.sub(
              b"<autoFilter[^>]*>.*?</autoFilter>",
              b"",
              buffer,
              flags=re.DOTALL,
          )
          buffer = re.sub(
              b"<customFilters[^>]*>.*?</customFilters>",
              b"",
              buffer,
              flags=re.DOTALL,
          )
          buffer = re.sub(b"<filter[^>]*/>", b"", buffer)
        zout.writestr(item, buffer)
  output.seek(0)

  df = pd.read_excel(output, sheet_name="INCROCI GEMINI", engine="openpyxl")

  # Pulizia e normalizzazione delle colonne metriche
  cols_to_clean = ["SOMMA", "DC", "C1", "C2", "MEDIA CASA", "MEDIA OSPITE"]
  for col in cols_to_clean:
    if col in df.columns:
      df[col] = clean_numeric_column(df[col])

  return df


def evaluate_market(row, market):
  gc = row["GOL CASA"]
  go = row["GOL OSPITE"]
  gt = gc + go

  if market == "1":
    return int(gc > go)
  elif market == "X":
    return int(gc == go)
  elif market == "2":
    return int(go > gc)
  elif market == "1X":
    return int(gc >= go)
  elif market == "X2":
    return int(go >= gc)
  elif market == "12":
    return int(gc != go)
  elif market == "ESITO 1-1":
    return int(gc == 1 and go == 1)

  elif market == "OVER 1,5":
    return int(gt > 1.5)
  elif market == "OVER 2,5":
    return int(gt > 2.5)
  elif market == "OVER 3,5":
    return int(gt > 3.5)
  elif market == "UNDER 1,5":
    return int(gt < 1.5)
  elif market == "UNDER 2,5":
    return int(gt < 2.5)
  elif market == "UNDER 3,5":
    return int(gt < 3.5)

  elif market == "GOL CASA":
    return int(gc > 0)
  elif market == "OVER 1,5 CASA":
    return int(gc > 1.5)
  elif market == "OVER 2,5 CASA":
    return int(gc > 2.5)
  elif market == "UNDER 1,5 CASA":
    return int(gc < 1.5)
  elif market == "UNDER 2,5 CASA":
    return int(gc < 2.5)

  elif market == "GOL OSPITE":
    return int(go > 0)
  elif market == "OVER 1,5 OSPITE":
    return int(go > 1.5)
  elif market == "OVER 2,5 OSPITE":
    return int(go > 2.5)
  elif market == "UNDER 1,5 OSPITE":
    return int(go < 1.5)
  elif market == "UNDER 2,5 OSPITE":
    return int(go < 2.5)

  return 0


def apply_filters(df, params):
  """Funzione ausiliaria per filtrare il DataFrame secondo i parametri della strategia."""
  mask = pd.Series([True] * len(df))
  somma_val = params.get("SOMMA")
  dc_val = params.get("DC")
  c1_val = params.get("C1")
  c2_val = params.get("C2")
  mc_val = params.get("MEDIA CASA")
  mo_val = params.get("MEDIA OSPITE")
  mo_op = params.get("MEDIA_OSPITE_OP", "<=")

  if somma_val is not None and "SOMMA" in df.columns:
    mask &= df["SOMMA"] >= somma_val
  if dc_val is not None and "DC" in df.columns:
    mask &= df["DC"] >= dc_val
  if c1_val is not None and "C1" in df.columns:
    mask &= df["C1"] >= c1_val
  if c2_val is not None and "C2" in df.columns:
    mask &= df["C2"] <= c2_val
  if mc_val is not None and "MEDIA CASA" in df.columns:
    mask &= df["MEDIA CASA"] >= mc_val
  if mo_val is not None and "MEDIA OSPITE" in df.columns:
    if mo_op == "<":
      mask &= df["MEDIA OSPITE"] < mo_val
    else:
      mask &= df["MEDIA OSPITE"] <= mo_val

  df_filtered = df[mask].copy().reset_index(drop=True)
  df_filtered["WIN"] = df_filtered.apply(
      lambda row: evaluate_market(row, params["MERCATO"]), axis=1
  )
  return df_filtered


st.title("⚽ Dashboard Analisi xG & Mercati")

if st.sidebar.button("🔄 Aggiorna Dati da Google Drive"):
  st.cache_data.clear()

direct_url = get_drive_direct_url(LINK_GOOGLE_DRIVE)


@st.cache_data(ttl=300)
def fetch_data_from_drive(url):
  response = requests.get(url)
  if response.status_code == 200:
    return load_clean_df(io.BytesIO(response.content))
  else:
    return None


try:
  with st.spinner("Lettura dati in corso..."):
    df_raw = fetch_data_from_drive(direct_url)

  if df_raw is not None:
    df_base = df_raw[
        df_raw["GOL CASA"].notna() & df_raw["GOL OSPITE"].notna()
    ].copy()
    df_base.reset_index(drop=True, inplace=True)

    MERCATI = [
        "1",
        "X",
        "2",
        "1X",
        "X2",
        "12",
        "ESITO 1-1",
        "OVER 1,5",
        "OVER 2,5",
        "OVER 3,5",
        "UNDER 1,5",
        "UNDER 2,5",
        "UNDER 3,5",
        "GOL CASA",
        "OVER 1,5 CASA",
        "OVER 2,5 CASA",
        "UNDER 1,5 CASA",
        "UNDER 2,5 CASA",
        "GOL OSPITE",
        "OVER 1,5 OSPITE",
        "OVER 2,5 OSPITE",
        "UNDER 1,5 OSPITE",
        "UNDER 2,5 OSPITE",
    ]

    # ==========================================
    # STRATEGIE SALVATE
    # ==========================================
    STRATEGIE_SALVATE = {
        "1. Esito 1-1 (C2 <= 0 | Media Ospite < 1.50)": {
            "SOMMA": None,
            "DC": None,
            "C1": None,
            "C2": 0.0,
            "MEDIA CASA": None,
            "MEDIA OSPITE": 1.50,
            "MEDIA_OSPITE_OP": "<",
            "MERCATO": "ESITO 1-1",
        },
        "2. Under 2,5 Ospite (C2 <= -4)": {
            "SOMMA": None,
            "DC": None,
            "C1": None,
            "C2": -4.0,
            "MEDIA CASA": None,
            "MEDIA OSPITE": None,
            "MERCATO": "UNDER 2,5 OSPITE",
        },
        "3. Esito X - Base (Somma >= -1.03 | Media Ospite <= 1.54)": {
            "SOMMA": -1.03,
            "DC": None,
            "C1": None,
            "C2": None,
            "MEDIA CASA": None,
            "MEDIA OSPITE": 1.54,
            "MEDIA_OSPITE_OP": "<=",
            "MERCATO": "X",
        },
        "4. Esito X - Gold (Somma >= -0.79 | Media Casa >= 1.1 | Media Ospite <= 1.51)": {
            "SOMMA": -0.79,
            "DC": None,
            "C1": None,
            "C2": None,
            "MEDIA CASA": 1.1,
            "MEDIA OSPITE": 1.51,
            "MEDIA_OSPITE_OP": "<=",
            "MERCATO": "X",
        },
        "5. Esito X - Stabilità (C1 >= -1.02 | DC >= 0.62 | Media Ospite <= 1.51)": {
            "SOMMA": None,
            "DC": 0.62,
            "C1": -1.02,
            "C2": None,
            "MEDIA CASA": None,
            "MEDIA OSPITE": 1.51,
            "MEDIA_OSPITE_OP": "<=",
            "MERCATO": "X",
        },
    }

    # SELETTORE PRINCIPALE
    st.sidebar.header("📌 SELEZIONA MODALITÀ")
    modalita = st.sidebar.radio(
        "Scegli il tipo di analisi:",
        [
            "🚨 Panoramica Strategie Sottoperformanti",
            "1. Mercati Singoli (Database Totale)",
            "2. Strategie xG & Filtri Salvati",
        ],
    )

    # -------------------------------------------------------------------------
    # MODALITÀ 0: PANORAMICA SOTTOPERFORMANTE (AUTOMATICA)
    # -------------------------------------------------------------------------
    if "🚨" in modalita:
      st.subheader("🚨 Report Strategie in Sottoperformance")
      st.write(
          "Elenco sintetico delle strategie salvate la cui **Media Mobile"
          " recente è INFERIORE alla Media Storica Totale** o che presentano"
          " un ritardo elevato."
      )

      finestra_alert = st.sidebar.slider(
          "Finestra Media Mobile per Alert", 10, 50, 20, 5
      )

      alert_list = []
      for name, params in STRATEGIE_SALVATE.items():
        df_strat = apply_filters(df_base, params)
        tot_strat = len(df_strat)

        if tot_strat >= finestra_alert:
          wins = df_strat["WIN"].sum()
          win_rate_tot = (wins / tot_strat) * 100
          quota_reale = (100 / win_rate_tot) if win_rate_tot > 0 else 0

          df_strat["MA"] = df_strat["WIN"].rolling(window=finestra_alert).mean() * 100
          mm_att = df_strat["MA"].dropna().iloc[-1]

          # Calcolo Ritardo Attuale
          rit_att = 0
          for res in reversed(df_strat["WIN"]):
            if res == 0:
              rit_att += 1
            else:
              break

          diff = mm_att - win_rate_tot

          # Condizione di Sottoperformance: MM Attuale sotto il Win Rate Totale
          if mm_att < win_rate_tot:
            alert_list.append({
                "Strategia": name,
                "Mercato": params["MERCATO"],
                "Match Totali": tot_strat,
                "Win Rate Totale": f"{win_rate_tot:.1f}%",
                "Quota Reale": f"{quota_reale:.2f}",
                f"MM Attuale ({finestra_alert}p)": f"{mm_att:.1f}%",
                "Scostamento": f"{diff:.1f}%",
                "Ritardo Attuale": rit_att,
            })

      if alert_list:
        df_alerts = pd.DataFrame(alert_list)
        st.warning(
            f"Trovate {len(alert_list)} strategie attualmente in"
            " sottoperformance rispetto alla loro media storica:"
        )
        st.dataframe(df_alerts, use_container_width=True)
      else:
        st.success(
            "🎉 Nessuna strategia è attualmente sotto la sua media storica!"
        )

    # -------------------------------------------------------------------------
    # MODALITÀ 1: MERCATI SINGOLI SU TUTTE LE PARTITE
    # -------------------------------------------------------------------------
    elif "1." in modalita:
      st.sidebar.markdown("---")
      st.sidebar.subheader("Mercato da Analizzare")
      mercato_scelto = st.sidebar.selectbox("Seleziona Mercato", MERCATI)
      titolo_analisi = (
          f"Analisi Mercato: {mercato_scelto} (Su tutte le partite)"
      )

      df = df_base.copy()
      df["WIN"] = df.apply(
          lambda row: evaluate_market(row, mercato_scelto), axis=1
      )

      finestra_ma = st.sidebar.slider(
          "Finestra Media Mobile (Partite)", 10, 50, 20, 5
      )
      tot_match = len(df)

      if tot_match >= finestra_ma:
        wins = df["WIN"].sum()
        freq_cum = (wins / tot_match) * 100
        quota_reale = (100 / freq_cum) if freq_cum > 0 else 0.0

        df["MA"] = df["WIN"].rolling(window=finestra_ma).mean() * 100

        rit_att, rit_max, curr_r = 0, 0, 0
        for res in df["WIN"]:
          if res == 0:
            curr_r += 1
            if curr_r > rit_max:
              rit_max = curr_r
          else:
            curr_r = 0
        rit_att = curr_r

        ma_clean = df["MA"].dropna()
        mm_att = ma_clean.iloc[-1]
        mm_min = ma_clean.min()
        mm_max = ma_clean.max()

        st.subheader(f"📊 {titolo_analisi}")
        col1, col2, col3, col4, col5, col6 = st.columns(6)
        col1.metric("Match Filtrati / Totali", tot_match)
        col2.metric("Win Rate Totale", f"{freq_cum:.1f}%")
        col3.metric("Quota Reale", f"{quota_reale:.2f}")
        col4.metric("Ritardo Attuale", rit_att)
        col5.metric("Ritardo Max Storico", rit_max)
        col6.metric(f"MM Attuale ({finestra_ma}p)", f"{mm_att:.1f}%")

        col_m1, col_m2 = st.columns(2)
        col_m1.metric("MM Minima Registrata", f"{mm_min:.1f}%")
        col_m2.metric("MM Massima Registrata", f"{mm_max:.1f}%")

        chart_data = pd.DataFrame({
            f"Media Mobile ({finestra_ma} match)": df["MA"],
            "Frequenza Cumulativa Totale": freq_cum,
        })
        st.line_chart(chart_data)

        st.subheader("📋 Ultime Partite Processate")
        st.dataframe(df.tail(15).iloc[::-1])

    # -------------------------------------------------------------------------
    # MODALITÀ 2: STRATEGIE E FILTRI xG SALVATI
    # -------------------------------------------------------------------------
    else:
      st.sidebar.markdown("---")
      st.sidebar.subheader("Scegli Strategia Salvata")
      strat_options = list(STRATEGIE_SALVATE.keys()) + [
          "Filtro Manuale Personalizzato"
      ]
      strat_nome = st.sidebar.selectbox("Strategia con Filtri", strat_options)

      if strat_nome != "Filtro Manuale Personalizzato":
        params = STRATEGIE_SALVATE[strat_nome]
        df = apply_filters(df_base, params)
        titolo_analisi = f"Strategia Salvata: {strat_nome}"
      else:
        st.sidebar.markdown("---")
        st.sidebar.subheader("Imposta Filtri Manuali")
        mercato_target = st.sidebar.selectbox(
            "Mercato Target", MERCATI, index=1
        )

        use_somma = st.sidebar.checkbox("Filtra per SOMMA Minima", value=False)
        somma_val = (
            st.sidebar.number_input(
                "SOMMA Minima", value=-1.03, step=0.01, format="%.2f"
            )
            if use_somma
            else None
        )

        use_dc = st.sidebar.checkbox("Filtra per DC Minima", value=False)
        dc_val = (
            st.sidebar.number_input(
                "DC Minima", value=0.00, step=0.01, format="%.2f"
            )
            if use_dc
            else None
        )

        use_c1 = st.sidebar.checkbox("Filtra per C1 Minimo", value=False)
        c1_val = (
            st.sidebar.number_input(
                "C1 Minimo", value=-1.00, step=0.01, format="%.2f"
            )
            if use_c1
            else None
        )

        use_c2 = st.sidebar.checkbox("Filtra per C2 Massimo", value=False)
        c2_val = (
            st.sidebar.number_input(
                "C2 Massimo", value=0.00, step=0.01, format="%.2f"
            )
            if use_c2
            else None
        )

        use_mc = st.sidebar.checkbox("Filtra per Media Casa Minima", value=False)
        mc_val = (
            st.sidebar.number_input(
                "Media Casa Minima", value=1.10, step=0.01, format="%.2f"
            )
            if use_mc
            else None
        )

        use_mo = st.sidebar.checkbox("Filtra per Media Ospite", value=False)
        mo_val = (
            st.sidebar.number_input(
                "Media Ospite Soglia", value=1.54, step=0.01, format="%.2f"
            )
            if use_mo
            else None
        )

        manual_params = {
            "SOMMA": somma_val,
            "DC": dc_val,
            "C1": c1_val,
            "C2": c2_val,
            "MEDIA CASA": mc_val,
            "MEDIA OSPITE": mo_val,
            "MEDIA_OSPITE_OP": "<=",
            "MERCATO": mercato_target,
        }
        df = apply_filters(df_base, manual_params)
        titolo_analisi = (
            f"Filtro Manuale Personalizzato - Target: {mercato_target}"
        )

      finestra_ma = st.sidebar.slider(
          "Finestra Media Mobile (Partite)", 10, 50, 20, 5
      )
      tot_match = len(df)

      if tot_match >= finestra_ma:
        wins = df["WIN"].sum()
        freq_cum = (wins / tot_match) * 100
        quota_reale = (100 / freq_cum) if freq_cum > 0 else 0.0

        df["MA"] = df["WIN"].rolling(window=finestra_ma).mean() * 100

        rit_att, rit_max, curr_r = 0, 0, 0
        for res in df["WIN"]:
          if res == 0:
            curr_r += 1
            if curr_r > rit_max:
              rit_max = curr_r
          else:
            curr_r = 0
        rit_att = curr_r

        ma_clean = df["MA"].dropna()
        mm_att = ma_clean.iloc[-1]
        mm_min = ma_clean.min()
        mm_max = ma_clean.max()

        st.subheader(f"📊 {titolo_analisi}")
        col1, col2, col3, col4, col5, col6 = st.columns(6)
        col1.metric("Match Filtrati / Totali", tot_match)
        col2.metric("Win Rate Totale", f"{freq_cum:.1f}%")
        col3.metric("Quota Reale", f"{quota_reale:.2f}")
        col4.metric("Ritardo Attuale", rit_att)
        col5.metric("Ritardo Max Storico", rit_max)
        col6.metric(f"MM Attuale ({finestra_ma}p)", f"{mm_att:.1f}%")

        col_m1, col_m2 = st.columns(2)
        col_m1.metric("MM Minima Registrata", f"{mm_min:.1f}%")
        col_m2.metric("MM Massima Registrata", f"{mm_max:.1f}%")

        chart_data = pd.DataFrame({
            f"Media Mobile ({finestra_ma} match)": df["MA"],
            "Frequenza Cumulativa Totale": freq_cum,
        })
        st.line_chart(chart_data)

        st.subheader("📋 Ultime Partite Processate")
        st.dataframe(df.tail(15).iloc[::-1])

  else:
    st.error(
        "Impossibile leggere il file. Verifica la condivisione del Foglio"
        " Google."
    )

except Exception as e:
  st.error(f"Errore durante l'elaborazione dei dati: {e}")
