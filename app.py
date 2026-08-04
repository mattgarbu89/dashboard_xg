import io
import re
import zipfile
import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="Dashboard Analisi xG & Value Bet Finder",
    page_icon="⚽",
    layout="wide",
)

LINK_GOOGLE_DRIVE = "https://docs.google.com/spreadsheets/d/1xmLiTz2YDi7XSKHwli1noUTgc2F0xxIxS5NJJ4digCE/edit?usp=sharing"
ODDS_API_KEY_DEFAULT = "1eb2407df0cb3fee45c56827ba2610d2"


def get_drive_direct_url(url):
    file_id_match = re.search(r"/d/([a-zA-Z0-9_-]+)", url)
    if file_id_match:
        return f"https://docs.google.com/spreadsheets/d/{file_id_match.group(1)}/export?format=xlsx"
    return url


def clean_numeric_column(series):
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

    cols_to_clean = ["SOMMA", "DC", "C1", "C2", "MEDIA CASA", "MEDIA OSPITE"]
    for col in cols_to_clean:
        if col in df.columns:
            df[col] = clean_numeric_column(df[col])

    for col in df.columns:
        if "DATA" in str(col).upper():
            df[col] = (
                pd.to_datetime(df[col], errors="coerce").dt.strftime("%d/%m/%Y")
            ).fillna(df[col])
        elif any(k in str(col).upper() for k in ["ORA", "ORARIO"]):
            df[col] = df[col].astype(str).str.replace("00:00:00", "").str.strip()

    return df


@st.cache_data(ttl=1800)
def fetch_all_active_odds(api_key):
    if not api_key:
        return []

    sports_url = f"https://api.the-odds-api.com/v4/sports/?apiKey={api_key}"
    try:
        res_sports = requests.get(sports_url, timeout=10)
        if res_sports.status_code != 200:
            return []
        all_sports = res_sports.json()
    except Exception:
        return []

    soccer_keys = [
        s["key"]
        for s in all_sports
        if s.get("group") == "Soccer" and s.get("active")
    ]

    all_odds = []
    for key in soccer_keys:
        url = f"https://api.the-odds-api.com/v4/sports/{key}/odds/?apiKey={api_key}&regions=eu&markets=h2h,totals&oddsFormat=decimal"
        try:
            res = requests.get(url, timeout=5)
            if res.status_code == 200:
                data = res.json()
                if isinstance(data, list):
                    all_odds.extend(data)
        except Exception:
            continue

    return all_odds


def clean_name(name):
    if not name or pd.isna(name):
        return ""
    n = str(name).lower().strip()
    n = re.sub(
        r"\b(fc|afc|cf|sc|ac|cd|ud|sd|rb|sporting|club|deportivo|atletico|real)\b",
        "",
        n,
    )
    n = re.sub(r"[^\w\s]", "", n)
    return " ".join(n.split())


def match_odds(home_team, away_team, market_target, odds_data):
    if not odds_data or not home_team or not away_team:
        return None

    h_clean = clean_name(home_team)
    a_clean = clean_name(away_team)
    m_target = str(market_target).upper().strip()

    for event in odds_data:
        ev_home = clean_name(event.get("home_team", ""))
        ev_away = clean_name(event.get("away_team", ""))

        home_match = (
            h_clean in ev_home
            or ev_home in h_clean
            or any(
                w in ev_home for w in h_clean.split() if len(w) > 3
            )
        )
        away_match = (
            a_clean in ev_away
            or ev_away in a_clean
            or any(
                w in ev_away for w in a_clean.split() if len(w) > 3
            )
        )

        if home_match and away_match:
            odds_1, odds_x, odds_2 = None, None, None
            over_25, under_25 = None, None

            for bookmaker in event.get("bookmakers", []):
                for market in bookmaker.get("markets", []):
                    key = market.get("key")
                    outcomes = market.get("outcomes", [])

                    if key == "h2h":
                        for out in outcomes:
                            price = float(out.get("price", 0))
                            name_out = clean_name(out.get("name", ""))
                            if name_out == ev_home:
                                odds_1 = max(odds_1 or 0, price)
                            elif name_out == ev_away:
                                odds_2 = max(odds_2 or 0, price)
                            elif name_out in ["draw", "x"]:
                                odds_x = max(odds_x or 0, price)

                    elif key == "totals":
                        for out in outcomes:
                            price = float(out.get("price", 0))
                            point = float(out.get("point", 0))
                            name_out = out.get("name", "").upper()
                            if point == 2.5:
                                if "OVER" in name_out:
                                    over_25 = max(over_25 or 0, price)
                                elif "UNDER" in name_out:
                                    under_25 = max(under_25 or 0, price)

            if m_target == "1":
                return odds_1
            elif m_target == "X":
                return odds_x
            elif m_target == "2":
                return odds_2
            elif m_target == "1X":
                return (
                    round(1 / ((1 / odds_1) + (1 / odds_x)), 2)
                    if (odds_1 and odds_x)
                    else odds_1
                )
            elif m_target == "X2":
                return (
                    round(1 / ((1 / odds_2) + (1 / odds_x)), 2)
                    if (odds_2 and odds_x)
                    else odds_2
                )
            elif "OVER" in m_target:
                return over_25
            elif "UNDER" in m_target:
                return under_25
            elif m_target == "ESITO 1-1":
                return odds_x

    return None


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
    elif market in ["OVER 1,5", "OVER 1.5"]:
        return int(gt > 1.5)
    elif market in ["OVER 2,5", "OVER 2.5"]:
        return int(gt > 2.5)
    elif market in ["OVER 3,5", "OVER 3.5"]:
        return int(gt > 3.5)
    elif market in ["UNDER 1,5", "UNDER 1.5"]:
        return int(gt < 1.5)
    elif market in ["UNDER 2,5", "UNDER 2.5"]:
        return int(gt < 2.5)
    elif market in ["UNDER 3,5", "UNDER 3.5"]:
        return int(gt < 3.5)
    elif market == "GOL CASA":
        return int(gc > 0)
    elif market == "OVER 1,5 CASA":
        return int(gc > 1.5)
    elif market == "UNDER 1,5 CASA":
        return int(gc < 1.5)
    elif market == "GOL OSPITE":
        return int(go > 0)
    elif market == "UNDER 1,5 OSPITE":
        return int(go < 1.5)
    elif market == "UNDER 2,5 OSPITE":
        return int(go < 2.5)

    return 0


def apply_filters(df, params):
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


def get_sorted_strategies(df_base, strategie_dict):
    ranked_strategies = []

    for name, params in strategie_dict.items():
        df_strat = apply_filters(df_base, params)
        df_strat_played = df_strat[df_strat["GOL CASA"].notna()].copy()
        tot = len(df_strat_played)
        win_rate_reale = (
            (df_strat_played["WIN"].sum() / tot * 100) if tot > 0 else 0.0
        )

        match = re.search(r"(\d+[\.,]?\d*)%", name)
        if match:
            win_rate_storico = float(match.group(1).replace(",", "."))
        else:
            win_rate_storico = win_rate_reale

        ranked_strategies.append({
            "nome": name,
            "win_rate_storico": win_rate_storico,
            "win_rate_reale": win_rate_reale,
            "params": params,
        })

    ranked_strategies.sort(key=lambda x: x["win_rate_reale"], reverse=True)
    return ranked_strategies


def render_tables(df_filtered, quota_limite, odds_dataset, market_target):
    df_played = (
        df_filtered[df_filtered["GOL CASA"].notna()].copy().reset_index(drop=True)
    )
    df_future = (
        df_filtered[df_filtered["GOL CASA"].isna()].copy().reset_index(drop=True)
    )

    col_casa = "SQUADRA CASA" if "SQUADRA CASA" in df_filtered.columns else "CASA"
    col_ospite = (
        "SQUADRA OSPITE" if "SQUADRA OSPITE" in df_filtered.columns else "OSPITE"
    )

    st.subheader(f"⏳ Prossime Partite da Giocare ({len(df_future)})")

    if len(df_future) > 0:
        q_book_list = []
        semaforo_list = []

        for _, row in df_future.iterrows():
            q_book = match_odds(
                row.get(col_casa), row.get(col_ospite), market_target, odds_dataset
            )
            if q_book:
                q_book_list.append(str(round(q_book, 2)).replace(".", ","))
                if q_book > quota_limite:
                    semaforo_list.append("🟢 VALORE")
                elif abs(q_book - quota_limite) <= 0.05:
                    semaforo_list.append("🟡 FAIR")
                else:
                    semaforo_list.append("🔴 NO VALUE")
            else:
                q_book_list.append("N/D")
                semaforo_list.append("⚪ N/D")

        df_future["Quota Limite"] = str(round(quota_limite, 2)).replace(".", ",")
        df_future["Miglior Quota Bookmaker"] = q_book_list
        df_future["Valutazione Value Bet"] = semaforo_list

        cols_finali = [
            c
            for c in df_future.columns
            if any(
                k in str(c).upper()
                for k in [
                    "DATA", "ORA", "CASA", "OSPITE", "GOL", "SOMMA", "DC", "C1", "C2"
                ]
            )
        ]
        cols_finali.extend(
            ["Quota Limite", "Miglior Quota Bookmaker", "Valutazione Value Bet"]
        )

        st.dataframe(df_future[cols_finali], use_container_width=True)
    else:
        st.info("Nessuna prossima partita trovata per questa strategia.")

    st.subheader(f"📋 Ultime Partite Processate ({len(df_played)})")
    if len(df_played) > 0:
        cols_played = [
            c
            for c in df_played.columns
            if any(
                k in str(c).upper()
                for k in [
                    "DATA", "ORA", "CASA", "OSPITE", "GOL", "SOMMA", "DC", "C1", "C2", "WIN"
                ]
            )
        ]
        st.dataframe(
            df_played[cols_played].tail(15).iloc[::-1], use_container_width=True
        )


st.title("⚽ Dashboard Analisi xG & Value Bet Finder")

st.sidebar.header("🔑 Configurazione Odds API")
api_key = st.sidebar.text_input(
    "API Key:",
    value=ODDS_API_KEY_DEFAULT,
    type="password",
)

if api_key:
    odds_dataset = fetch_all_active_odds(api_key)
    if odds_dataset:
        st.sidebar.success(f"⚡ Quote API Connesse ({len(odds_dataset)} match scaricati)")
    else:
        st.sidebar.warning("⚠️ Nessuna quota scaricata. Verifica API Key o disponibilità match.")
else:
    odds_dataset = []

if st.sidebar.button("🔄 Aggiorna Dati da Google Drive"):
    st.cache_data.clear()

direct_url = get_drive_direct_url(LINK_GOOGLE_DRIVE)


@st.cache_data(ttl=300)
def fetch_data_from_drive(url):
    response = requests.get(url)
    if response.status_code == 200:
        return load_clean_df(io.BytesIO(response.content))
    return None


try:
    with st.spinner("Lettura dati da Google Drive..."):
        df_raw = fetch_data_from_drive(direct_url)

    if df_raw is not None:
        df_base = df_raw.copy()

        STRATEGIE_SALVATE = {
            "Esito X super combo 235 match 37.45%": {
                "SOMMA": None, "DC": None, "C1": -1.5, "C1_OP": ">=", "C2": 0.0, "C2_OP": "<=", "MEDIA CASA": 1.0, "MEDIA_CASA_OP": ">=", "MEDIA OSPITE": 1.50, "MEDIA_OSPITE_OP": "<=", "MERCATO": "X",
            },
            "Esito X 300 match 36.33%": {
                "SOMMA": None, "DC": None, "C1": -1.5, "C1_OP": ">=", "C2": 1.0, "C2_OP": "<=", "MEDIA CASA": None, "MEDIA OSPITE": 1.50, "MEDIA_OSPITE_OP": "<=", "MERCATO": "X",
            },
            "Esito X 406 match 34.24%": {
                "SOMMA": None, "DC": None, "C1": None, "C2": None, "MEDIA CASA": 1.1, "MEDIA_CASA_OP": ">=", "MEDIA OSPITE": 1.50, "MEDIA_OSPITE_OP": "<=", "MERCATO": "X",
            },
            "Esito X 263 match 36.5%": {
                "SOMMA": -1.03, "SOMMA_OP": ">=", "DC": None, "C1": None, "C2": None, "MEDIA CASA": None, "MEDIA OSPITE": 1.54, "MEDIA_OSPITE_OP": "<=", "MERCATO": "X",
            },
            "Esito X 200 match 38%": {
                "SOMMA": -0.79, "SOMMA_OP": ">=", "DC": None, "C1": None, "C2": None, "MEDIA CASA": 1.1, "MEDIA_CASA_OP": ">=", "MEDIA OSPITE": 1.51, "MEDIA_OSPITE_OP": "<=", "MERCATO": "X",
            },
            "Esito X 301 match 35.9%": {
                "SOMMA": None, "DC": 0.62, "DC_OP": ">=", "C1": -1.02, "C1_OP": ">=", "C2": None, "MEDIA CASA": None, "MEDIA OSPITE": 1.51, "MEDIA_OSPITE_OP": "<=", "MERCATO": "X",
            },
            "Esito 1-1 436 match 17.9%": {
                "SOMMA": None, "DC": None, "C1": None, "C2": 0.0, "C2_OP": "<=", "MEDIA CASA": None, "MEDIA OSPITE": 1.50, "MEDIA_OSPITE_OP": "<", "MERCATO": "ESITO 1-1",
            },
            "Esito und 2,5 ospite 248 match 90.3%": {
                "SOMMA": None, "DC": None, "C1": None, "C2": -4.0, "C2_OP": "<=", "MEDIA CASA": None, "MEDIA OSPITE": None, "MERCATO": "UNDER 2,5 OSPITE",
            },
        }

        ranked_strategies = get_sorted_strategies(df_base, STRATEGIE_SALVATE)

        st.sidebar.header("📌 SELEZIONA MODALITÀ")
        modalita = st.sidebar.radio(
            "Scegli il tipo di analisi:",
            [
                "🚨 Panoramica Strategie & Trend",
                "📊 Strategie xG & Value Bet Finder",
            ],
        )

        if "🚨" in modalita:
            st.subheader("🚨 Report Strategie: Sottoperformance & Bounce Back")

            finestra_alert = st.sidebar.slider(
                "Finestra Media Mobile per Alert", 10, 50, 20, 5
            )

            alert_underperforming = []
            alert_bounce_back = []

            for item in ranked_strategies:
                name = item["nome"]
                win_rate_storico = item["win_rate_storico"]
                win_rate_reale = item["win_rate_reale"]
                params = item["params"]

                df_strat = apply_filters(df_base, params)
                df_strat_played = df_strat[df_strat["GOL CASA"].notna()].copy()
                tot_strat = len(df_strat_played)

                if tot_strat >= finestra_alert:
                    df_strat_played["MA"] = (
                        df_strat_played["WIN"].rolling(window=finestra_alert).mean() * 100
                    )
                    ma_series = df_strat_played["MA"].dropna()
                    mm_att = ma_series.iloc[-1]
                    diff_storica = mm_att - win_rate_storico

                    if mm_att < win_rate_storico:
                        alert_underperforming.append({
                            "Strategia": name,
                            "Mercato": params["MERCATO"],
                            "Match Giocati": tot_strat,
                            "WR Storico Target": f"{str(round(win_rate_storico, 2)).replace('.', ',')}%",
                            "WR Attuale Reale": f"{str(round(win_rate_reale, 2)).replace('.', ',')}%",
                            f"MM Attuale ({finestra_alert}p)": f"{str(round(mm_att, 1)).replace('.', ',')}%",
                            "Scostamento vs Storico": f"{str(round(diff_storica, 1)).replace('.', ',')}%",
                        })

                        last_win = df_strat_played["WIN"].iloc[-1]
                        if last_win == 1 and len(ma_series) >= 2:
                            mm_prev = ma_series.iloc[-2]
                            diff_bounce = mm_att - mm_prev

                            alert_bounce_back.append({
                                "Strategia": name,
                                "Mercato": params["MERCATO"],
                                "WR Storico Target": f"{str(round(win_rate_storico, 2)).replace('.', ',')}%",
                                f"MM Precedente ({finestra_alert}p)": f"{str(round(mm_prev, 1)).replace('.', ',')}%",
                                f"MM Attuale ({finestra_alert}p)": f"{str(round(mm_att, 1)).replace('.', ',')}%",
                                "Rimbalzo": f"+{str(round(diff_bounce, 1)).replace('.', ',')}%",
                                "Ultimo Esito": "✅ WIN",
                            })

            st.markdown("### 🚀 SEGNALI DI RIENTRO IN TREND (Bounce Back)")
            if alert_bounce_back:
                st.success(f"🔥 Trovate {len(alert_bounce_back)} strategie con segnale di inversione di trend:")
                st.dataframe(pd.DataFrame(alert_bounce_back), use_container_width=True)
            else:
                st.info("Nessun segnale di rimbalzo attivo nell'ultimo match.")

            st.markdown("---")

            st.markdown("### 🚨 STRATEGIE IN SOTTOPERFORMANCE")
            if alert_underperforming:
                st.warning(f"Trovate {len(alert_underperforming)} strategie sotto la baseline storica:")
                st.dataframe(pd.DataFrame(alert_underperforming), use_container_width=True)
            else:
                st.success("🎉 Tutte le strategie stabili sopra la media target.")

        else:
            strat_map = {item["nome"]: item for item in ranked_strategies}

            st.sidebar.markdown("---")
            strat_nome = st.sidebar.selectbox(
                "Scegli Strategia da Analizzare", list(strat_map.keys())
            )

            selected_item = strat_map[strat_nome]
            params = selected_item["params"]
            win_rate_storico = selected_item["win_rate_storico"]

            df_strat = apply_filters(df_base, params)
            df_played = df_strat[df_strat["GOL CASA"].notna()].copy()
            tot_match = len(df_played)

            win_rate_reale = (
                (df_played["WIN"].sum() / tot_match * 100) if tot_match > 0 else 0
            )
            quota_limite = (100 / win_rate_reale) if win_rate_reale > 0 else 0

            st.subheader(f"📊 {strat_nome}")
            st.info(
                f"🎯 **Win Rate Reale:** {str(round(win_rate_reale, 2)).replace('.', ',')}% | "
                f"**Quota Limite Minima (Fair Odds):** {str(round(quota_limite, 2)).replace('.', ',')}"
            )

            finestra_ma = st.sidebar.slider(
                "Finestra Media Mobile (Partite)", 10, 50, 20, 5
            )

            if tot_match >= finestra_ma:
                df_played["MA"] = (
                    df_played["WIN"].rolling(window=finestra_ma).mean() * 100
                )
                df_played["FREQ_CUM_DINAMICA"] = (
                    df_played["WIN"].expanding().mean() * 100
                )

                chart_data = pd.DataFrame({
                    f"Media Mobile ({finestra_ma} match)": df_played["MA"],
                    "Frequenza Cumulativa": df_played["FREQ_CUM_DINAMICA"],
                    "Media Target": win_rate_storico,
                })
                st.line_chart(chart_data)

            render_tables(
                df_strat, quota_limite, odds_dataset, params["MERCATO"]
            )

except Exception as e:
    st.error(f"Errore durante l'elaborazione dei dati: {e}")
