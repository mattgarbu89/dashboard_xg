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
  return pd.to_numeric(series.astype(str).str.replace(",", "."), errors="coerce")


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

  # Formattazione e pulizia delle colonne Data e Orario
  for col in df.columns:
    if "DATA" in col.upper():
      df[col] = (
          pd.to_datetime(df[col], errors="coerce").dt.strftime("%d/%m/%Y")
      ).fillna(df[col])
    elif any(k in col.upper() for k in ["ORA", "ORARIO"]):
      df[col] = df[col].astype(str).str.replace("00:00:00", "").str.strip()

  return df


def evaluate_market(row, market):
  if pd.isna(row["GOL CASA"]) or pd.isna(row["GOL OSPITE"]):
    return None

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

  ops = {
      ">=": lambda s, v: s >= v,
      "<=": lambda s, v: s <= v,
      ">": lambda s, v: s > v,
      "<": lambda s, v: s < v,
      "==": lambda s, v: s == v,
  }

  for col in ["SOMMA", "DC", "C1", "C2", "MEDIA CASA", "MEDIA OSPITE"]:
    val = params.get(col)
    if val is not None and col in df.columns:
      op_str = params.get(f"{col}_OP", ">=")
      if col == "C2" and f"{col}_OP" not in params:
        op_str = "<="
      if col == "MEDIA OSPITE" and f"{col}_OP" not in params:
        op_str = params.get("MEDIA_OSPITE_OP", "<=")

      if op_str in ops:
        mask &= ops[op_str](df[col], val)

  df_filtered = df[mask].copy().reset_index(drop=True)
  df_filtered["WIN"] = df_filtered.apply(
      lambda row: evaluate_market(row, params["MERCATO"]), axis=1
  )
  return df_filtered


def get_params_string(params):
  """Genera una stringa leggibile con la lista dei parametri attivi."""
  items = []
  col_names = {
      "SOMMA": "SOMMA",
      "DC": "DC",
      "C1": "C1",
      "C2": "C2",
      "MEDIA CASA": "Media Casa",
      "MEDIA OSPITE": "Media Ospite",
  }

  for col, label in col_names.items():
    val = params.get(col)
    if val is not None:
      op = params.get(f"{col}_OP", ">=")
      if col == "C2" and f"{col}_OP" not in params:
        op = "<="
      if col == "MEDIA OSPITE" and f"{col}_OP" not in params:
        op = params.get("MEDIA_OSPITE_OP", "<=")

      val_str = str(val).replace(".", ",")
      items.append(f"{label} {op} {val_str}")

  if items:
    return " | ".join(items)
  else:
    return "Nessun filtro xG applicato (Tutto il database)"


def render_tables(df_filtered):
  df_played = (
      df_filtered[df_filtered["GOL CASA"].notna()].copy().reset_index(drop=True)
  )
  df_future = (
      df_filtered[df_filtered["GOL CASA"].isna()].copy().reset_index(drop=True)
  )

  cols_disponibili = list(df_filtered.columns)
  cols_da_mostrare = [
      c
      for c in cols_disponibili
      if any(
          k in c.upper()
          for k in [
              "DATA",
              "ORA",
              "ORARIO",
              "CASA",
              "OSPITE",
              "SQUADRA",
              "MATCH",
              "PARTITA",
              "GOL",
              "SOMMA",
              "DC",
              "C1",
              "C2",
              "WIN",
          ]
      )
  ]
  if not cols_da_mostrare:
    cols_da_mostrare = cols_disponibili[:8]

  st.subheader(f"⏳ Prossime Partite da Giocare ({len(df_future)})")
  if len(df_future) > 0:
    cols_future = [
        c for c in cols_da_mostrare if c not in ["WIN", "GOL CASA", "GOL OSPITE"]
    ]
    st.dataframe(df_future[cols_future], use_container_width=True)
  else:
    st.info("Nessuna prossima partita trovata per questa strategia.")

  st.subheader(f"📋 Ultime Partite Processate / Giocate ({len(df_played)})")
  if len(df_played) > 0:
    st.dataframe(
        df_played[cols_da_mostrare].tail(15).iloc[::-1],
        use_container_width=True,
    )
  else:
    st.info("Nessuna partita giocata ancora a storico per questo filtro.")


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
    df_base = df_raw.copy()

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
        "Esito X super combo 235 match 37.45%": {
            "SOMMA": None,
            "DC": None,
            "C1": -1.5,
            "C1_OP": ">=",
            "C2": 0.0,
            "C2_OP": "<=",
            "MEDIA CASA": 1.0,
            "MEDIA_CASA_OP": ">=",
            "MEDIA OSPITE": 1.50,
            "MEDIA_OSPITE_OP": "<=",
            "MERCATO": "X",
        },
        "Esito X 300 match 36.33%": {
            "SOMMA": None,
            "DC": None,
            "C1": -1.5,
            "C1_OP": ">=",
            "C2": 1.0,
            "C2_OP": "<=",
            "MEDIA CASA": None,
            "MEDIA OSPITE": 1.50,
            "MEDIA_OSPITE_OP": "<=",
            "MERCATO": "X",
        },
        "Esito X 406 match 34.24%": {
            "SOMMA": None,
            "DC": None,
            "C1": None,
            "C2": None,
            "MEDIA CASA": 1.1,
            "MEDIA_CASA_OP": ">=",
            "MEDIA OSPITE": 1.50,
            "MEDIA_OSPITE_OP": "<=",
            "MERCATO": "X",
        },
        "Esito X 263 match 36.5%": {
            "SOMMA": -1.03,
            "SOMMA_OP": ">=",
            "DC": None,
            "C1": None,
            "C2": None,
            "MEDIA CASA": None,
            "MEDIA OSPITE": 1.54,
            "MEDIA_OSPITE_OP": "<=",
            "MERCATO": "X",
        },
        "Esito X 200 match 38%": {
            "SOMMA": -0.79,
            "SOMMA_OP": ">=",
            "DC": None,
            "C1": None,
            "C2": None,
            "MEDIA CASA": 1.1,
            "MEDIA_CASA_OP": ">=",
            "MEDIA OSPITE": 1.51,
            "MEDIA_OSPITE_OP": "<=",
            "MERCATO": "X",
        },
        "Esito X 301 match 35.9%": {
            "SOMMA": None,
            "DC": 0.62,
            "DC_OP": ">=",
            "C1": -1.02,
            "C1_OP": ">=",
            "C2": None,
            "MEDIA CASA": None,
            "MEDIA OSPITE": 1.51,
            "MEDIA_OSPITE_OP": "<=",
            "MERCATO": "X",
        },
        "Esito 1-1 436 match 17.9%": {
            "SOMMA": None,
            "DC": None,
            "C1": None,
            "C2": 0.0,
            "C2_OP": "<=",
            "MEDIA CASA": None,
            "MEDIA OSPITE": 1.50,
            "MEDIA_OSPITE_OP": "<",
            "MERCATO": "ESITO 1-1",
        },
        "Esito und 2,5 ospite 248 match 90.3%": {
            "SOMMA": None,
            "DC": None,
            "C1": None,
            "C2": -4.0,
            "C2_OP": "<=",
            "MEDIA CASA": None,
            "MEDIA OSPITE": None,
            "MERCATO": "UNDER 2,5 OSPITE",
        },
    }

    st.sidebar.header("📌 SELEZIONA MODALITÀ")
    modalita = st.sidebar.radio(
        "Scegli il tipo di analisi:",
        [
            "🚨 Panoramica Strategie Sottoperformanti",
            "1. Mercati Singoli (Database Totale)",
            "2. Strategie xG & Filtri Salvati",
        ],
    )

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
        df_strat_played = df_strat[df_strat["GOL CASA"].notna()].copy()
        tot_strat = len(df_strat_played)

        if tot_strat >= finestra_alert:
          wins = df_strat_played["WIN"].sum()
          win_rate_tot = (wins / tot_strat) * 100
          quota_reale = (100 / win_rate_tot) if win_rate_tot > 0 else 0

          df_strat_played["MA"] = (
              df_strat_played["WIN"].rolling(window=finestra_alert).mean() * 100
          )
          mm_att = df_strat_played["MA"].dropna().iloc[-1]

          rit_att = 0
          for res in reversed(df_strat_played["WIN"]):
            if res == 0:
              rit_att += 1
            else:
              break

          diff = mm_att - win_rate_tot

          if mm_att < win_rate_tot:
            alert_list.append({
                "Strategia": name,
                "Mercato": params["MERCATO"],
                "Parametri Filtro": get_params_string(params),
                "Match Giocati": tot_strat,
                "Win Rate Totale": (
                    f"{str(round(win_rate_tot, 1)).replace('.', ',')}%"
                ),
                "Quota Reale": f"{str(round(quota_reale, 2)).replace('.', ',')}",
                f"MM Attuale ({finestra_alert}p)": (
                    f"{str(round(mm_att, 1)).replace('.', ',')}%"
                ),
                "Scostamento": f"{str(round(diff, 1)).replace('.', ',')}%",
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
      df_played = df[df["GOL CASA"].notna()].copy()
      tot_match = len(df_played)

      if tot_match >= finestra_ma:
        wins = df_played["WIN"].sum()
        freq_cum = (wins / tot_match) * 100
        quota_reale = (100 / freq_cum) if freq_cum > 0 else 0.0

        df_played["MA"] = (
            df_played["WIN"].rolling(window=finestra_ma).mean() * 100
        )
        df_played["FREQ_CUM_DINAMICA"] = df_played["WIN"].expanding().mean() * 100

        rit_att, rit_max, curr_r = 0, 0, 0
        for res in df_played["WIN"]:
          if res == 0:
            curr_r += 1
            if curr_r > rit_max:
              rit_max = curr_r
          else:
            curr_r = 0
        rit_att = curr_r

        ma_clean = df_played["MA"].dropna()
        mm_att = ma_clean.iloc[-1]
        mm_min = ma_clean.min()
        mm_max = ma_clean.max()

        st.subheader(f"📊 {titolo_analisi}")
        st.info("ℹ️ **Parametri Filtro:** Tutto il database (Nessun filtro xG)")

        col1, col2, col3, col4, col5, col6 = st.columns(6)
        col1.metric("Match Giocati", tot_match)
        col2.metric(
            "Win Rate Totale", f"{str(round(freq_cum, 1)).replace('.', ',')}%"
        )
        col3.metric(
            "Quota Reale", f"{str(round(quota_reale, 2)).replace('.', ',')}"
        )
        col4.metric("Ritardo Attuale", rit_att)
        col5.metric("Ritardo Max Storico", rit_max)
        col6.metric(
            f"MM Attuale ({finestra_ma}p)",
            f"{str(round(mm_att, 1)).replace('.', ',')}%",
        )

        col_m1, col_m2 = st.columns(2)
        col_m1.metric(
            "MM Minima Registrata",
            f"{str(round(mm_min, 1)).replace('.', ',')}%",
        )
        col_m2.metric(
            "MM Massima Registrata",
            f"{str(round(mm_max, 1)).replace('.', ',')}%",
        )

        chart_data = pd.DataFrame({
            f"Media Mobile ({finestra_ma} match)": df_played["MA"],
            "Frequenza Cumulativa Progressiva": df_played["FREQ_CUM_DINAMICA"],
            "Media Finale Totale": freq_cum,
        })
        st.line_chart(chart_data)

        render_tables(df)

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
        st.sidebar.subheader("⚙️ Filtri Personalizzati Dinamici")
        mercato_target = st.sidebar.selectbox(
            "Mercato Target", MERCATI, index=1
        )

        params = {"MERCATO": mercato_target}

        metriche = [
            ("C1", "C1 (Scarto Casa)", -1.00, ">="),
            ("C2", "C2 (Scarto Ospite)", 0.00, "<="),
            ("DC", "DC (Diff C1 - C2)", 0.00, ">="),
            ("SOMMA", "SOMMA (C1 + C2)", -1.03, ">="),
            ("MEDIA CASA", "Media Casa xG", 1.10, ">="),
            ("MEDIA OSPITE", "Media Ospite xG", 1.54, "<="),
        ]

        for col_name, label, def_val, def_op in metriche:
          use_param = st.sidebar.checkbox(
              f"Filtra per {label}", value=False, key=f"use_{col_name}"
          )
          if use_param:
            col_op, col_val = st.sidebar.columns([1, 2])
            op_sel = col_op.selectbox(
                "Op",
                [">=", "<=", ">", "<", "=="],
                index=[">=", "<=", ">", "<", "=="].index(def_op),
                key=f"op_{col_name}",
            )
            val_sel = col_val.number_input(
                "Valore",
                value=float(def_val),
                step=0.01,
                format="%.2f",
                key=f"val_{col_name}",
            )

            params[col_name] = val_sel
            params[f"{col_name}_OP"] = op_sel

        df = apply_filters(df_base, params)
        titolo_analisi = (
            f"Filtro Manuale Personalizzato - Target: {mercato_target}"
        )

      finestra_ma = st.sidebar.slider(
          "Finestra Media Mobile (Partite)", 10, 50, 20, 5
      )
      df_played = df[df["GOL CASA"].notna()].copy()
      tot_match = len(df_played)

      if tot_match >= finestra_ma:
        wins = df_played["WIN"].sum()
        freq_cum = (wins / tot_match) * 100
        quota_reale = (100 / freq_cum) if freq_cum > 0 else 0.0

        df_played["MA"] = (
            df_played["WIN"].rolling(window=finestra_ma).mean() * 100
        )
        df_played["FREQ_CUM_DINAMICA"] = df_played["WIN"].expanding().mean() * 100

        rit_att, rit_max, curr_r = 0, 0, 0
        for res in df_played["WIN"]:
          if res == 0:
            curr_r += 1
            if curr_r > rit_max:
              rit_max = curr_r
          else:
            curr_r = 0
        rit_att = curr_r

        ma_clean = df_played["MA"].dropna()
        mm_att = ma_clean.iloc[-1]
        mm_min = ma_clean.min()
        mm_max = ma_clean.max()

        st.subheader(f"📊 {titolo_analisi}")

        # BOX INFORMATIVO CON I PARAMETRI
        st.info(f"⚙️ **Parametri Filtro Attivi:** {get_params_string(params)}")

        col1, col2, col3, col4, col5, col6 = st.columns(6)
        col1.metric("Match Giocati", tot_match)
        col2.metric(
            "Win Rate Totale", f"{str(round(freq_cum, 1)).replace('.', ',')}%"
        )
        col3.metric(
            "Quota Reale", f"{str(round(quota_reale, 2)).replace('.', ',')}"
        )
        col4.metric("Ritardo Attuale", rit_att)
        col5.metric("Ritardo Max Storico", rit_max)
        col6.metric(
            f"MM Attuale ({finestra_ma}p)",
            f"{str(round(mm_att, 1)).replace('.', ',')}%",
        )

        col_m1, col_m2 = st.columns(2)
        col_m1.metric(
            "MM Minima Registrata",
            f"{str(round(mm_min, 1)).replace('.', ',')}%",
        )
        col_m2.metric(
            "MM Massima Registrata",
            f"{str(round(mm_max, 1)).replace('.', ',')}%",
        )

        chart_data = pd.DataFrame({
            f"Media Mobile ({finestra_ma} match)": df_played["MA"],
            "Frequenza Cumulativa Progressiva": df_played["FREQ_CUM_DINAMICA"],
            "Media Finale Totale": freq_cum,
        })
        st.line_chart(chart_data)

        render_tables(df)
      else:
        st.warning(
            f"Partite giocate riscontrate: {tot_match}. Servono almeno"
            f" {finestra_ma} gare per i calcoli della Media Mobile."
        )
        render_tables(df)

  else:
    st.error(
        "Impossibile leggere il file. Verifica la condivisione del Foglio"
        " Google."
    )

except Exception as e:
  st.error(f"Errore durante l'elaborazione dei dati: {e}")
