import io
import re
import zipfile
import difflib
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


def format_num_comma(val, decimals=2):
    """Formatta un numero con la virgola per i decimali."""
    if val is None or pd.isna(val):
        return "-"
    try:
        fmt = f"{{:.{decimals}f}}"
        return fmt.format(float(val)).replace(".", ",")
    except Exception:
        return str(val)


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

    cols_to_clean = ["SOMMA", "DC", "C1", "C2", "MEDIA CASA", "MEDIA OSPITE", "GOL CASA", "GOL OSPITE"]
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


def detect_team_columns(df):
    col_casa, col_ospite = None, None

    for c in df.columns:
        c_clean = str(c).upper().strip()
        if c_clean in [
            "CASA", "SQUADRA CASA", "SQUADRA_CASA", "HOME", 
            "SQUADRA 1", "SQUADRA_1", "SQUADRA H", "HOME TEAM"
        ]:
            col_casa = c
        elif c_clean in [
            "OSPITE", "SQUADRA OSPITE", "SQUADRA_OSPITE", "AWAY", 
            "SQUADRA 2", "SQUADRA_2", "TRASFERTA", "SQUADRA A", "AWAY TEAM"
        ]:
            col_ospite = c

    if not col_casa or not col_ospite:
        text_cols = [c for c in df.columns if df[c].dtype == "object" or df[c].dtype == "string"]
        for c in text_cols:
            c_clean = str(c).upper().strip()
            if not col_casa and "CASA" in c_clean and not any(x in c_clean for x in ["GOL", "MEDIA", "C1", "XG", "SUBITI", "FATTI", "QUOTA"]):
                col_casa = c
            elif not col_ospite and any(x in c_clean for x in ["OSPITE", "TRASFERTA", "AWAY"]) and not any(x in c_clean for x in ["GOL", "MEDIA", "C2", "XG", "SUBITI", "FATTI", "QUOTA"]):
                col_ospite = c

    if not col_casa or not col_ospite:
        string_cols = []
        for c in df.columns:
            sample_val = df[c].dropna().astype(str).head(5).tolist()
            if sample_val and not any(re.match(r"^-?\d+[\.,]?\d*$", v.strip()) for v in sample_val):
                if not any(k in str(c).upper() for k in ["DATA", "ORA", "ORARIO", "LEGA", "CAMPIONATO"]):
                    string_cols.append(c)
        if len(string_cols) >= 2:
            col_casa = col_casa or string_cols[0]
            col_ospite = col_ospite or string_cols[1]
        elif len(string_cols) == 1:
            col_casa = col_casa or string_cols[0]
            col_ospite = col_ospite or string_cols[0]

    return col_casa, col_ospite


def split_teams_if_combined(val_casa, val_ospite):
    s_casa = str(val_casa).strip() if pd.notna(val_casa) else ""
    s_ospite = str(val_ospite).strip() if pd.notna(val_ospite) else ""

    if s_casa == s_ospite or not s_ospite or s_ospite == "nan":
        for sep in [" - ", " vs ", " v ", " -"]:
            if sep in s_casa:
                parts = s_casa.split(sep, 1)
                return parts[0].strip(), parts[1].strip()

    return s_casa, s_ospite


def clean_team_name(name):
    if not name or pd.isna(name):
        return ""
    text = str(name).lower().strip()
    
    replacements = {
        r"\butd\b": "united",
        r"\bu\.\b": "universidad ",
        r"\bdep\.\b": "deportes ",
        r"\bst\.\b": "saint ",
        r"\bac\b": "",
        r"\bfc\b": "",
        r"\bsc\b": "",
        r"\bcf\b": "",
        r"\bcd\b": "",
    }
    for pat, repl in replacements.items():
        text = re.sub(pat, repl, text)

    text = re.sub(r"[^\w\s]", "", text)
    stopwords = ["club", "calcio", "spg", "vfb", "1", "de", "la", "real"]
    words = [w for w in text.split() if w not in stopwords]
    return " ".join(words) if words else text


def fuzzy_match_teams(t1, t2):
    c1 = clean_team_name(t1)
    c2 = clean_team_name(t2)
    if not c1 or not c2:
        return False

    if c1 == c2:
        return True

    if len(c1) >= 4 and len(c2) >= 4:
        if c1 in c2 or c2 in c1:
            return True

    ratio = difflib.SequenceMatcher(None, c1, c2).ratio()
    return ratio >= 0.45


@st.cache_data(ttl=1800)
def fetch_all_active_odds(api_key):
    if not api_key:
        return []

    sports_url = f"https://api.the-odds-api.com/v4/sports/?apiKey={api_key}"
    try:
        res_sports = requests.get(sports_url, timeout=10)
        if res_sports.status_code == 401:
            st.sidebar.error("❌ API Key non valida!")
            return []
        elif res_sports.status_code == 429:
            st.sidebar.error("⚠️ Crediti API esauriti!")
            return []
        elif res_sports.status_code != 200:
            return []

        requests_remaining = res_sports.headers.get("x-requests-remaining")
        if requests_remaining:
            st.sidebar.info(f"📊 Crediti API Rimanenti: {requests_remaining}")

        all_sports = res_sports.json()
        soccer_keys = [s["key"] for s in all_sports if s.get("group") == "Soccer" and s.get("active")]
    except Exception:
        soccer_keys = [
            "soccer_italy_serie_a", "soccer_italy_serie_b", "soccer_epl", 
            "soccer_spain_la_liga", "soccer_germany_bundesliga", "soccer_france_ligue_one",
            "soccer_netherlands_eredivisie", "soccer_belgium_first_div", "soccer_uefa_champs_league"
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
            elif res.status_code == 429:
                st.sidebar.error("⚠️ Quota chiamate API superata!")
                break
        except Exception:
            continue

    return all_odds


def extract_best_odd(event, market_target):
    m_target = str(market_target).upper().strip()
    ev_home = event.get("home_team", "")
    ev_away = event.get("away_team", "")

    odds_1, odds_x, odds_2 = None, None, None
    over_25, under_25 = None, None

    for bookmaker in event.get("bookmakers", []):
        for market in bookmaker.get("markets", []):
            key = market.get("key")
            outcomes = market.get("outcomes", [])

            if key == "h2h":
                for out in outcomes:
                    price = float(out.get("price", 0))
                    name = out.get("name", "")
                    if name == ev_home:
                        odds_1 = max(odds_1 or 0, price)
                    elif name == ev_away:
                        odds_2 = max(odds_2 or 0, price)
                    elif name in ["Draw", "X"]:
                        odds_x = max(odds_x or 0, price)

            elif key == "totals":
                for out in outcomes:
                    price = float(out.get("price", 0))
                    point = float(out.get("point", 0))
                    name = out.get("name", "")
                    if point == 2.5:
                        if name == "Over":
                            over_25 = max(over_25 or 0, price)
                        elif name == "Under":
                            under_25 = max(under_25 or 0, price)

    if m_target == "1":
        return odds_1
    elif m_target == "X":
        return odds_x
    elif m_target == "2":
        return odds_2
    elif m_target == "1X":
        return round(1 / ((1 / odds_1) + (1 / odds_x)), 2) if (odds_1 and odds_x) else odds_1
    elif m_target == "X2":
        return round(1 / ((1 / odds_2) + (1 / odds_x)), 2) if (odds_2 and odds_x) else odds_2
    elif "OVER" in m_target:
        return over_25
    elif "UNDER" in m_target:
        return under_25
    elif m_target == "ESITO 1-1":
        return odds_x

    return odds_1 or odds_x or odds_2


def match_odds(home_team, away_team, market_target, odds_data):
    if not odds_data or not home_team or not away_team or pd.isna(home_team) or pd.isna(away_team):
        return None

    h_clean, a_clean = split_teams_if_combined(home_team, away_team)

    for event in odds_data:
        ev_home = event.get("home_team", "")
        ev_away = event.get("away_team", "")

        if fuzzy_match_teams(h_clean, ev_home) and fuzzy_match_teams(a_clean, ev_away):
            return extract_best_odd(event, market_target)

    return None


def evaluate_market(row, market):
    if pd.isna(row["GOL CASA"]) or pd.isna(row["GOL OSPITE"]):
        return None

    gc = float(row["GOL CASA"])
    go = float(row["GOL OSPITE"])
    gt = gc + go
    m = str(market).upper().strip()

    # ESITI FINALI
    if m == "1":
        return int(gc > go)
    elif m == "X":
        return int(gc == go)
    elif m == "2":
        return int(go > gc)
    elif m == "1X":
        return int(gc >= go)
    elif m == "X2":
        return int(go >= gc)
    elif m == "12":
        return int(gc != go)
    elif m == "ESITO 1-1":
        return int(gc == 1 and go == 1)

    # GOL / NO GOL
    elif m == "GOL":
        return int(gc > 0 and go > 0)
    elif m in ["NO GOL", "NOGOL", "MOGOL"]:
        return int(gc == 0 or go == 0)

    # OVER / UNDER GENERALI
    elif m in ["OVER 1,5", "OVER 1.5"]:
        return int(gt > 1.5)
    elif m in ["OVER 2,5", "OVER 2.5"]:
        return int(gt > 2.5)
    elif m in ["OVER 3,5", "OVER 3.5"]:
        return int(gt > 3.5)
    elif m in ["UNDER 1,5", "UNDER 1.5"]:
        return int(gt < 1.5)
    elif m in ["UNDER 2,5", "UNDER 2.5"]:
        return int(gt < 2.5)
    elif m in ["UNDER 3,5", "UNDER 3.5"]:
        return int(gt < 3.5)

    # MERCATI CASA
    elif m == "GOL CASA":
        return int(gc > 0)
    elif m in ["OVER 1,5 CASA", "OVER 1.5 CASA"]:
        return int(gc > 1.5)
    elif m in ["OVER 2,5 CASA", "OVER 2.5 CASA"]:
        return int(gc > 2.5)
    elif m in ["UNDER 1,5 CASA", "UNDER 1.5 CASA"]:
        return int(gc < 1.5)
    elif m in ["UNDER 2,5 CASA", "UNDER 2.5 CASA"]:
        return int(gc < 2.5)

    # MERCATI OSPITE
    elif m == "GOL OSPITE":
        return int(go > 0)
    elif m in ["OVER 1,5 OSPITE", "OVER 1.5 OSPITE"]:
        return int(go > 1.5)
    elif m in ["OVER 2,5 OSPITE", "OVER 2.5 OSPITE"]:
        return int(go > 2.5)
    elif m in ["UNDER 1,5 OSPITE", "UNDER 1.5 OSPITE"]:
        return int(go < 1.5)
    elif m in ["UNDER 2,5 OSPITE", "UNDER 2.5 OSPITE"]:
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


def calculate_delays(win_series):
    current_delay = 0
    max_delay = 0
    temp_delay = 0

    for win in win_series:
        if win == 0:
            temp_delay += 1
            max_delay = max(max_delay, temp_delay)
        elif win == 1:
            temp_delay = 0

    for win in reversed(win_series.tolist()):
        if win == 0:
            current_delay += 1
        else:
            break

    return current_delay, max_delay


def get_sorted_strategies(df_base, strategie_dict):
    ranked_strategies = []
    for name, params in strategie_dict.items():
        df_strat = apply_filters(df_base, params)
        df_strat_played = df_strat[df_strat["GOL CASA"].notna()].copy()
        tot = len(df_strat_played)
        win_rate_reale = (df_strat_played["WIN"].sum() / tot * 100) if tot > 0 else 0.0

        match = re.search(r"(\d+[\.,]?\d*)%", name)
        win_rate_storico = (
            float(match.group(1).replace(",", ".")) if match else win_rate_reale
        )

        ranked_strategies.append({
            "nome": name,
            "win_rate_storico": win_rate_storico,
            "win_rate_reale": win_rate_reale,
            "params": params,
        })

    ranked_strategies.sort(key=lambda x: x["win_rate_reale"], reverse=True)
    return ranked_strategies


def get_combination_string(params):
    """Genera la stringa che esplicita la combinazione di parametri usata."""
    condizioni = []
    mercato = params.get("MERCATO", "N/D")

    mancanti = {
        "SOMMA": params.get("SOMMA_OP", ">="),
        "DC": params.get("DC_OP", ">="),
        "C1": params.get("C1_OP", ">="),
        "C2": params.get("C2_OP", "<="),
        "MEDIA CASA": params.get("MEDIA_CASA_OP", ">="),
        "MEDIA OSPITE": params.get("MEDIA_OSPITE_OP", "<="),
    }

    for key, default_op in mancanti.items():
        val = params.get(key)
        if val is not None:
            op_key = f"{key}_OP"
            if key == "MEDIA CASA":
                op_key = "MEDIA_CASA_OP"
            elif key == "MEDIA OSPITE":
                op_key = "MEDIA_OSPITE_OP"

            op = params.get(op_key, default_op)
            val_str = format_num_comma(val)
            condizioni.append(f"**{key}** {op} `{val_str}`")

    if not condizioni:
        return f"🎯 **Mercato Target:** {mercato} | *Nessun filtro sui parametri fisso (Tutti i match)*"

    return f"🎯 **Mercato Target:** `{mercato}`  \n⚙️ **Combinazione Parametri:** " + "  •  ".join(condizioni)


def render_tables(df_filtered, quota_limite, odds_dataset, market_target, col_casa, col_ospite):
    df_played = (
        df_filtered[df_filtered["GOL CASA"].notna()].copy().reset_index(drop=True)
    )
    df_future = (
        df_filtered[df_filtered["GOL CASA"].isna()].copy().reset_index(drop=True)
    )

    st.subheader(f"⏳ Prossime Partite da Giocare ({len(df_future)})")

    if len(df_future) > 0:
        q_book_list, semaforo_list = [], []

        for _, row in df_future.iterrows():
            val_casa = row.get(col_casa) if col_casa else None
            val_ospite = row.get(col_ospite) if col_ospite else None

            q_book = match_odds(val_casa, val_ospite, market_target, odds_dataset)
            if q_book:
                q_book_list.append(format_num_comma(q_book))
                if q_book > quota_limite:
                    semaforo_list.append("VALORE")
                elif abs(q_book - quota_limite) <= 0.05:
                    semaforo_list.append("FAIR")
                else:
                    semaforo_list.append("NO VALUE")
            else:
                q_book_list.append("N/D")
                semaforo_list.append("N/D")

        df_future["Quota Limite"] = format_num_comma(quota_limite)
        df_future["Miglior Quota Bookmaker"] = q_book_list
        df_future["Valutazione Value Bet"] = semaforo_list

        cols_finali = []
        for c in df_future.columns:
            if any(k in str(c).upper() for k in ["DATA", "ORA", "ORARIO"]):
                if c not in cols_finali:
                    cols_finali.append(c)

        if col_casa and col_casa in df_future.columns and col_casa not in cols_finali:
            cols_finali.append(col_casa)
        if col_ospite and col_ospite in df_future.columns and col_ospite not in cols_finali and col_ospite != col_casa:
            cols_finali.append(col_ospite)

        cols_finali.extend(["Quota Limite", "Miglior Quota Bookmaker", "Valutazione Value Bet"])
        cols_finali_clean = [c for c in cols_finali if c in df_future.columns]

        st.dataframe(df_future[cols_finali_clean], use_container_width=True)
    else:
        st.info("Nessuna prossima partita trovata per questa selezione.")

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
        strat_map = {item["nome"]: item for item in ranked_strategies}

        # LISTA COMPLETA DEI MERCATI PER DATABASE TOTALE
        MERCATI_TOTALI = [
            "1", "X", "2", "1X", "X2", "12",
            "GOL", "NO GOL",
            "OVER 1,5", "OVER 2,5", "UNDER 1,5", "UNDER 2,5",
            "GOL CASA", "OVER 1,5 CASA", "OVER 2,5 CASA", "UNDER 1,5 CASA", "UNDER 2,5 CASA",
            "GOL OSPITE", "OVER 1,5 OSPITE", "OVER 2,5 OSPITE", "UNDER 1,5 OSPITE", "UNDER 2,5 OSPITE"
        ]

        # --- SIDEBAR - SELEZIONI IN ALTO ---
        st.sidebar.header("📌 SELEZIONA MODALITÀ")
        modalita = st.sidebar.radio(
            "Scegli il tipo di analisi:",
            [
                "🚨 Panoramica Strategie & Trend",
                "📊 Strategie xG & Value Bet Finder",
                "📂 Database Totale (Analisi Mercati)",
            ],
        )

        st.sidebar.markdown("---")
        if "📊" in modalita:
            st.sidebar.header("🎯 SELEZIONA STRATEGIA")
            strat_nome = st.sidebar.selectbox("Strategia attiva:", list(strat_map.keys()))
            st.sidebar.markdown("---")
        elif "📂" in modalita:
            st.sidebar.header("🎯 SELEZIONA MERCATO TOTALE")
            mercato_totale_sel = st.sidebar.selectbox("Mercato da analizzare:", MERCATI_TOTALI)
            st.sidebar.markdown("---")

        st.sidebar.header("🔑 Configurazione Odds API")
        api_key = st.sidebar.text_input(
            "API Key:",
            value=ODDS_API_KEY_DEFAULT,
            type="password",
        )

        odds_dataset = fetch_all_active_odds(api_key) if api_key else []

        if len(odds_dataset) > 0:
            st.sidebar.success(f"Quote API Connesse ({len(odds_dataset)} match salvati)")
        else:
            st.sidebar.warning("Nessuna quota scaricata. Verifica API Key o disponibilità match.")

        if st.sidebar.button("🔄 Aggiorna Dati da Google Drive"):
            st.cache_data.clear()

        col_casa_auto, col_ospite_auto = detect_team_columns(df_base)
        st.sidebar.markdown("---")
        st.sidebar.header("📌 Selezione Colonne Squadre")
        all_cols = list(df_base.columns)
        
        idx_casa = all_cols.index(col_casa_auto) if col_casa_auto in all_cols else 0
        idx_ospite = all_cols.index(col_ospite_auto) if col_ospite_auto in all_cols else (1 if len(all_cols) > 1 else 0)

        col_casa = st.sidebar.selectbox("Colonna Squadra Casa (o Partita Intera):", all_cols, index=idx_casa)
        col_ospite = st.sidebar.selectbox("Colonna Squadra Ospite:", all_cols, index=idx_ospite)

        # --- CORPO PRINCIPALE DASHBOARD ---
        if "🚨" in modalita:
            st.subheader("🚨 Report Strategie: Sottoperformance & Bounce Back")

            finestra_alert = st.sidebar.slider(
                "Finestra Media Mobile per Alert", 10, 50, 20, 5
            )

            alert_underperforming, alert_bounce_back = [], []

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
                            "WR Storico Target": f"{format_num_comma(win_rate_storico)}%",
                            "WR Attuale Reale": f"{format_num_comma(win_rate_reale)}%",
                            f"MM Attuale ({finestra_alert}p)": f"{format_num_comma(mm_att, 1)}%",
                            "Scostamento vs Storico": f"{format_num_comma(diff_storica, 1)}%",
                            "Combinazione Filtri": get_combination_string(params),
                        })

                        last_win = df_strat_played["WIN"].iloc[-1]
                        if last_win == 1 and len(ma_series) >= 2:
                            mm_prev = ma_series.iloc[-2]
                            diff_bounce = mm_att - mm_prev

                            alert_bounce_back.append({
                                "Strategia": name,
                                "Mercato": params["MERCATO"],
                                "WR Storico Target": f"{format_num_comma(win_rate_storico)}%",
                                f"MM Precedente ({finestra_alert}p)": f"{format_num_comma(mm_prev, 1)}%",
                                f"MM Attuale ({finestra_alert}p)": f"{format_num_comma(mm_att, 1)}%",
                                "Rimbalzo": f"+{format_num_comma(diff_bounce, 1)}%",
                                "Ultimo Esito": "WIN",
                                "Combinazione Filtri": get_combination_string(params),
                            })

            st.markdown("### SEGNALI DI RIENTRO IN TREND (Bounce Back)")
            if alert_bounce_back:
                st.dataframe(pd.DataFrame(alert_bounce_back), use_container_width=True)
            else:
                st.info("Nessun segnale di rimbalzo attivo nell'ultimo match.")

            st.markdown("---")

            st.markdown("### STRATEGIE IN SOTTOPERFORMANCE")
            if alert_underperforming:
                st.dataframe(pd.DataFrame(alert_underperforming), use_container_width=True)
            else:
                st.info("Tutte le strategie stabili sopra la media target.")

        elif "📂" in modalita:
            # === NUOVA MODALITÀ DATABASE TOTALE ===
            st.subheader(f"📂 Analisi Database Totale — Mercato: `{mercato_totale_sel}`")

            params_tot = {
                "SOMMA": None, "DC": None, "C1": None, "C2": None,
                "MEDIA CASA": None, "MEDIA OSPITE": None,
                "MERCATO": mercato_totale_sel
            }

            df_tot = apply_filters(df_base, params_tot)
            df_played = df_tot[df_tot["GOL CASA"].notna()].copy()
            tot_match = len(df_played)

            win_rate_reale = (df_played["WIN"].sum() / tot_match * 100) if tot_match > 0 else 0
            quota_limite = (100 / win_rate_reale) if win_rate_reale > 0 else 0

            current_delay, max_delay = calculate_delays(df_played["WIN"]) if tot_match > 0 else (0, 0)

            st.sidebar.markdown("---")
            finestra_ma = st.sidebar.slider("Finestra Media Mobile (Partite)", 10, 50, 20, 5)

            mm_att, mm_min, mm_max = 0.0, 0.0, 0.0
            if tot_match >= finestra_ma:
                df_played["MA"] = df_played["WIN"].rolling(window=finestra_ma).mean() * 100
                df_played["FREQ_CUM_DINAMICA"] = df_played["WIN"].expanding().mean() * 100
                
                ma_valid = df_played["MA"].dropna()
                if len(ma_valid) > 0:
                    mm_att = ma_valid.iloc[-1]
                    mm_min = ma_valid.min()
                    mm_max = ma_valid.max()

            with st.container(border=True):
                st.markdown(get_combination_string(params_tot))

            st.markdown("#### 📈 Metriche Principali (Database Completo)")

            # BLOCCO VISIVO SU 2 RIGHE E 4 COLONNE
            r1_c1, r1_c2, r1_c3, r1_c4 = st.columns(4)
            with r1_c1:
                with st.container(border=True):
                    st.caption("Match Totali Giocati")
                    st.markdown(f"### {tot_match}")

            with r1_c2:
                with st.container(border=True):
                    st.caption("Win Rate Reale (%)")
                    st.markdown(f"### {format_num_comma(win_rate_reale)}%")

            with r1_c3:
                with st.container(border=True):
                    st.caption("Quota Fair / Limite")
                    st.markdown(f"### {format_num_comma(quota_limite)}")

            with r1_c4:
                with st.container(border=True):
                    st.caption("Ritardo Attuale (Match)")
                    st.markdown(f"### {current_delay}")

            r2_c1, r2_c2, r2_c3, r2_c4 = st.columns(4)
            with r2_c1:
                with st.container(border=True):
                    st.caption("Ritardo Max Storico (Match)")
                    st.markdown(f"### {max_delay}")

            with r2_c2:
                with st.container(border=True):
                    st.caption(f"MM Attuale ({finestra_ma} match)")
                    st.markdown(f"### {format_num_comma(mm_att, 1)}%")

            with r2_c3:
                with st.container(border=True):
                    st.caption(f"MM Minima ({finestra_ma} match)")
                    st.markdown(f"### {format_num_comma(mm_min, 1)}%")

            with r2_c4:
                with st.container(border=True):
                    st.caption(f"MM Massima ({finestra_ma} match)")
                    st.markdown(f"### {format_num_comma(mm_max, 1)}%")

            st.markdown("---")

            if tot_match >= finestra_ma:
                chart_data = pd.DataFrame({
                    f"Media Mobile ({finestra_ma} match)": df_played["MA"],
                    "Frequenza Cumulativa": df_played["FREQ_CUM_DINAMICA"],
                })
                st.line_chart(chart_data)

            render_tables(df_tot, quota_limite, odds_dataset, mercato_totale_sel, col_casa, col_ospite)

        else:
            # === MODALITÀ STRATEGIE SALVATE ===
            selected_item = strat_map[strat_nome]
            params = selected_item["params"]
            win_rate_storico = selected_item["win_rate_storico"]

            df_strat = apply_filters(df_base, params)
            df_played = df_strat[df_strat["GOL CASA"].notna()].copy()
            tot_match = len(df_played)

            win_rate_reale = (df_played["WIN"].sum() / tot_match * 100) if tot_match > 0 else 0
            quota_limite = (100 / win_rate_reale) if win_rate_reale > 0 else 0

            current_delay, max_delay = calculate_delays(df_played["WIN"]) if tot_match > 0 else (0, 0)

            st.sidebar.markdown("---")
            finestra_ma = st.sidebar.slider("Finestra Media Mobile (Partite)", 10, 50, 20, 5)

            mm_att, mm_min, mm_max = 0.0, 0.0, 0.0
            if tot_match >= finestra_ma:
                df_played["MA"] = df_played["WIN"].rolling(window=finestra_ma).mean() * 100
                df_played["FREQ_CUM_DINAMICA"] = df_played["WIN"].expanding().mean() * 100
                
                ma_valid = df_played["MA"].dropna()
                if len(ma_valid) > 0:
                    mm_att = ma_valid.iloc[-1]
                    mm_min = ma_valid.min()
                    mm_max = ma_valid.max()

            st.subheader(f"📊 {strat_nome}")

            # ESPLICITAZIONE DELLA COMBINAZIONE DEI PARAMETRI
            with st.container(border=True):
                st.markdown(get_combination_string(params))

            st.markdown("#### 📈 Metriche Principali")

            # BLOCCO VISIVO SU 2 RIGHE E 4 COLONNE
            r1_c1, r1_c2, r1_c3, r1_c4 = st.columns(4)
            with r1_c1:
                with st.container(border=True):
                    st.caption("Match Totali Giocati")
                    st.markdown(f"### {tot_match}")

            with r1_c2:
                with st.container(border=True):
                    st.caption("Win Rate Reale (%)")
                    st.markdown(f"### {format_num_comma(win_rate_reale)}%")

            with r1_c3:
                with st.container(border=True):
                    st.caption("Quota Fair / Limite")
                    st.markdown(f"### {format_num_comma(quota_limite)}")

            with r1_c4:
                with st.container(border=True):
                    st.caption("Ritardo Attuale (Match)")
                    st.markdown(f"### {current_delay}")

            r2_c1, r2_c2, r2_c3, r2_c4 = st.columns(4)
            with r2_c1:
                with st.container(border=True):
                    st.caption("Ritardo Max Storico (Match)")
                    st.markdown(f"### {max_delay}")

            with r2_c2:
                with st.container(border=True):
                    st.caption(f"MM Attuale ({finestra_ma} match)")
                    st.markdown(f"### {format_num_comma(mm_att, 1)}%")

            with r2_c3:
                with st.container(border=True):
                    st.caption(f"MM Minima ({finestra_ma} match)")
                    st.markdown(f"### {format_num_comma(mm_min, 1)}%")

            with r2_c4:
                with st.container(border=True):
                    st.caption(f"MM Massima ({finestra_ma} match)")
                    st.markdown(f"### {format_num_comma(mm_max, 1)}%")

            st.markdown("---")

            if tot_match >= finestra_ma:
                chart_data = pd.DataFrame({
                    f"Media Mobile ({finestra_ma} match)": df_played["MA"],
                    "Frequenza Cumulativa": df_played["FREQ_CUM_DINAMICA"],
                    "Media Target": win_rate_storico,
                })
                st.line_chart(chart_data)

            render_tables(df_strat, quota_limite, odds_dataset, params["MERCATO"], col_casa, col_ospite)

except Exception as e:
    st.error(f"Errore durante l'elaborazione dei dati: {e}")
