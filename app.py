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

    if len(c1) >= 3 and len(c2) >= 3:
        if c1 in c2 or c2 in c1:
            return True

    ratio = difflib.SequenceMatcher(None, c1, c2).ratio()
    return ratio >= 0.50


def fetch_all_active_odds(api_key):
    if not api_key:
        return [], "Nessuna API Key fornita"

    sports_url = f"https://api.the-odds-api.com/v4/sports/?apiKey={api_key}"
    req_remaining = "N/D"
    try:
        res_sports = requests.get(sports_url, timeout=10)
        if res_sports.status_code == 401:
            return [], "API Key non valida!"
        elif res_sports.status_code == 429:
            return [], "Crediti API esauriti!"
        elif res_sports.status_code != 200:
            return [], f"Errore server API: {res_sports.status_code}"

        req_remaining = res_sports.headers.get("x-requests-remaining", "N/D")

        all_sports = res_sports.json()
        soccer_keys = [s["key"] for s in all_sports if s.get("group") == "Soccer" and s.get("active")]
    except Exception:
        soccer_keys = [
            "soccer_italy_serie_a", "soccer_italy_serie_b", "soccer_epl", 
            "soccer_spain_la_liga", "soccer_germany_bundesliga", "soccer_france_ligue_one",
            "soccer_netherlands_eredivisie", "soccer_belgium_first_div", "soccer_uefa_champs_league"
        ]

    all_odds = []
    # Includiamo i mercati estesi (h2h, totals, correct_score, team_totals)
    for key in soccer_keys:
        url = f"https://api.the-odds-api.com/v4/sports/{key}/odds/?apiKey={api_key}&regions=eu,uk&markets=h2h,totals,correct_score,team_totals&oddsFormat=decimal"
        try:
            res = requests.get(url, timeout=5)
            if res.status_code == 200:
                data = res.json()
                if isinstance(data, list):
                    all_odds.extend(data)
            elif res.status_code == 429:
                break
        except Exception:
            continue

    return all_odds, req_remaining


def extract_best_odd(event, market_target):
    """Scansiona i bookmaker e restituisce la quota più alta e il relativo bookmaker per il mercato specifico."""
    m_target = str(market_target).upper().strip().replace(",", ".")
    ev_home = event.get("home_team", "")
    ev_away = event.get("away_team", "")

    bookmakers = event.get("bookmakers", [])
    if not bookmakers:
        return None, None

    best_price = None
    best_bookmaker = None

    for bkm in bookmakers:
        bkm_title = bkm.get("title", bkm.get("key", "Sconosciuto"))

        odds_1, odds_x, odds_2 = None, None, None
        over_25, under_25 = None, None
        exact_11 = None
        
        home_under_25, home_under_15 = None, None
        away_under_25, away_under_15 = None, None

        for market in bkm.get("markets", []):
            key = market.get("key")
            outcomes = market.get("outcomes", [])

            # Mercato 1X2
            if key == "h2h":
                for out in outcomes:
                    price = float(out.get("price", 0))
                    name = out.get("name", "")
                    if name == ev_home or fuzzy_match_teams(name, ev_home):
                        odds_1 = price
                    elif name == ev_away or fuzzy_match_teams(name, ev_away):
                        odds_2 = price
                    elif name in ["Draw", "X"]:
                        odds_x = price

            # Mercato Somma Gol Totale
            elif key == "totals":
                for out in outcomes:
                    price = float(out.get("price", 0))
                    point = float(out.get("point", 0))
                    name = out.get("name", "")
                    if point == 2.5:
                        if name == "Over":
                            over_25 = price
                        elif name == "Under":
                            under_25 = price

            # Mercato Risultato Esatto (Correct Score)
            elif key == "correct_score":
                for out in outcomes:
                    price = float(out.get("price", 0))
                    name = str(out.get("name", "")).strip()
                    if name in ["1-1", "1:1", "1 - 1"]:
                        exact_11 = price

            # Mercato Gol Squadra (Team Totals)
            elif key in ["team_totals", "home_team_totals", "away_team_totals"]:
                for out in outcomes:
                    price = float(out.get("price", 0))
                    point = float(out.get("point", 0))
                    name = out.get("name", "")
                    description = out.get("description", "")
                    
                    # Identifica se si riferisce a Casa o Ospite
                    is_away = ev_away in description or fuzzy_match_teams(description, ev_away)
                    is_home = ev_home in description or fuzzy_match_teams(description, ev_home)

                    if is_away:
                        if point == 2.5 and name == "Under":
                            away_under_25 = price
                        elif point == 1.5 and name == "Under":
                            away_under_15 = price
                    elif is_home:
                        if point == 2.5 and name == "Under":
                            home_under_25 = price
                        elif point == 1.5 and name == "Under":
                            home_under_15 = price

        current_odd = None
        if m_target == "1":
            current_odd = odds_1
        elif m_target == "X":
            current_odd = odds_x
        elif m_target == "2":
            current_odd = odds_2
        elif m_target in ["1X", "1/X"]:
            if odds_1 and odds_x:
                current_odd = round(1 / ((1 / odds_1) + (1 / odds_x)), 2)
            else:
                current_odd = odds_1
        elif m_target in ["X2", "X/2"]:
            if odds_2 and odds_x:
                current_odd = round(1 / ((1 / odds_2) + (1 / odds_x)), 2)
            else:
                current_odd = odds_2
        elif m_target in ["12", "1/2"]:
            if odds_1 and odds_2:
                current_odd = round(1 / ((1 / odds_1) + (1 / odds_2)), 2)
            else:
                current_odd = odds_1
        elif "OVER 2.5" in m_target:
            current_odd = over_25
        elif "UNDER 2.5 OSPITE" in m_target:
            current_odd = away_under_25
        elif "UNDER 1.5 OSPITE" in m_target:
            current_odd = away_under_15
        elif "UNDER 2.5 CASA" in m_target:
            current_odd = home_under_25
        elif "UNDER 1.5 CASA" in m_target:
            current_odd = home_under_15
        elif "UNDER 2.5" in m_target:
            current_odd = under_25
        elif m_target == "ESITO 1-1":
            current_odd = exact_11

        if current_odd is not None and current_odd > 0:
            if best_price is None or current_odd > best_price:
                best_price = current_odd
                best_bookmaker = bkm_title

    return best_price, best_bookmaker


def match_odds(home_team, away_team, market_target, odds_data):
    """Trova il match e restituisce la miglior quota disponibile con il relativo bookmaker."""
    if not odds_data or not home_team or not away_team or pd.isna(home_team) or pd.isna(away_team):
        return None, "N/D", "N/D"

    h_clean, a_clean = split_teams_if_combined(home_team, away_team)

    for event in odds_data:
        ev_home = event.get("home_team", "")
        ev_away = event.get("away_team", "")

        if fuzzy_match_teams(h_clean, ev_home) and fuzzy_match_teams(a_clean, ev_away):
            quota_max, bookmaker = extract_best_odd(event, market_target)
            if quota_max is not None:
                match_found = f"{ev_home} vs {ev_away}"
                return quota_max, bookmaker, match_found
            else:
                return None, "N/D", f"{ev_home} vs {ev_away} (Quota non disponibile)"

    return None, "N/D", "Non Trovata"


def evaluate_market(row, market):
    if pd.isna(row["GOL CASA"]) or pd.isna(row["GOL OSPITE"]):
        return None

    gc = float(row["GOL CASA"])
    go = float(row["GOL OSPITE"])
    gt = gc + go
    m = str(market).upper().strip()

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

    elif m == "GOL":
        return int(gc > 0 and go > 0)
    elif m in ["NO GOL", "NOGOL", "MOGOL"]:
        return int(gc == 0 or go == 0)

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


def calculate_delays_and_cycles(win_series):
    esiti = win_series.tolist()

    ritardi_conclusi = []
    ritardo_corrente = 0

    for esito in esiti:
        if esito == 1:
            ritardi_conclusi.append(ritardo_corrente)
            ritardo_corrente = 0
        else:
            ritardo_corrente += 1

    ritardo_attuale = ritardo_corrente

    tutti_i_ritardi = (
        ritardi_conclusi + [ritardo_attuale]
        if ritardi_conclusi
        else [ritardo_attuale]
    )
    ritardo_max = max(tutti_i_ritardi) if tutti_i_ritardi else 0

    if len(ritardi_conclusi) > 0:
        ritardo_medio = sum(ritardi_conclusi) / len(ritardi_conclusi)
    else:
        ritardo_medio = 0.0

    ciclo_max_storico = 0
    ciclo_consecutivo_corrente = 0

    for r in ritardi_conclusi:
        if r > ritardo_medio:
            ciclo_consecutivo_corrente += 1
            if ciclo_consecutivo_corrente > ciclo_max_storico:
                ciclo_max_storico = ciclo_consecutivo_corrente
        else:
            ciclo_consecutivo_corrente = 0

    ciclo_attuale = ciclo_consecutivo_corrente

    return {
        "ritardo_attuale": ritardo_attuale,
        "ritardo_max": ritardo_max,
        "ritardo_medio": ritardo_medio,
        "ciclo_max_storico": ciclo_max_storico,
        "ciclo_attuale": ciclo_attuale,
    }


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
        q_book_list, bkm_list, semaforo_list, match_api_list = [], [], [], []

        for _, row in df_future.iterrows():
            val_casa = row.get(col_casa) if col_casa else None
            val_ospite = row.get(col_ospite) if col_ospite else None

            q_book, bookmaker, match_name_api = match_odds(val_casa, val_ospite, market_target, odds_dataset)
            match_api_list.append(match_name_api)
            bkm_list.append(bookmaker)

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

        df_future["Match Trovato su API"] = match_api_list
        df_future["Quota Limite"] = format_num_comma(quota_limite)
        df_future["Miglior Quota"] = q_book_list
        df_future["Bookmaker"] = bkm_list
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

        cols_finali.extend(["Match Trovato su API", "Quota Limite", "Miglior Quota", "Bookmaker", "Valutazione Value Bet"])
        cols_finali_clean = [c for c in cols_finali if c in df_future.columns]

        df_future_display = df_future[cols_finali_clean].copy()
        df_future_display.index = range(1, len(df_future_display) + 1)

        st.dataframe(df_future_display, use_container_width=True)
    else:
        st.info("Nessuna prossima partita trovata per questa selezione.")

    st.subheader(f"📋 Ultime Partite Processate ({len(df_played)})")
    if len(df_played) > 0:
        q_book_played_list = []
        bkm_played_list = []
        for _, row in df_played.iterrows():
            excel_quota = None
            for col_q in df_played.columns:
                if "QUOTA" in str(col_q).upper():
                    excel_quota = row.get(col_q)
                    break

            if pd.notna(excel_quota) and str(excel_quota).strip() not in ["", "nan", "None"]:
                q_book_played_list.append(format_num_comma(excel_quota))
                bkm_played_list.append("Excel")
            else:
                val_casa = row.get(col_casa) if col_casa else None
                val_ospite = row.get(col_ospite) if col_ospite else None
                q_book, bkm, _ = match_odds(val_casa, val_ospite, market_target, odds_dataset)
                q_book_played_list.append(format_num_comma(q_book) if q_book else "N/D")
                bkm_played_list.append(bkm if bkm else "N/D")

        df_played["Miglior Quota"] = q_book_played_list
        df_played["Bookmaker"] = bkm_played_list

        cols_played = []

        for c in df_played.columns:
            if any(k in str(c).upper() for k in ["DATA", "ORA", "ORARIO"]):
                if c not in cols_played:
                    cols_played.append(c)

        if col_casa and col_casa in df_played.columns and col_casa not in cols_played:
            cols_played.append(col_casa)
        if col_ospite and col_ospite in df_played.columns and col_ospite not in cols_played and col_ospite != col_casa:
            cols_played.append(col_ospite)

        altre_cols = [
            c for c in df_played.columns
            if any(
                k in str(c).upper()
                for k in ["GOL CASA", "GOL OSPITE", "SOMMA", "DC", "C1", "C2", "WIN"]
            ) and c not in cols_played
        ]
        cols_played.extend(altre_cols)

        if "Miglior Quota" in df_played.columns and "Miglior Quota" not in cols_played:
            cols_played.append("Miglior Quota")
        if "Bookmaker" in df_played.columns and "Bookmaker" not in cols_played:
            cols_played.append("Bookmaker")

        df_display = df_played[cols_played].iloc[::-1].copy().reset_index(drop=True)
        df_display.index = range(1, len(df_display) + 1)

        for col in df_display.select_dtypes(include=['float', 'float64']).columns:
            df_display[col] = df_display[col].apply(lambda x: format_num_comma(x))

        st.dataframe(df_display, use_container_width=True)


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
                "🔥 Cicli Max da Puntare",
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

        # --- GESTIONE API CON SALVATAGGIO IN MEMORIA ---
        if "odds_dataset" not in st.session_state:
            st.session_state["odds_dataset"] = []
        if "api_req_remaining" not in st.session_state:
            st.session_state["api_req_remaining"] = "N/D"

        st.sidebar.header("🔑 Configurazione Odds API")
        api_key = st.sidebar.text_input(
            "API Key:",
            value=ODDS_API_KEY_DEFAULT,
            type="password",
        )

        if st.sidebar.button("📥 Scarica / Aggiorna Quote ora"):
            if api_key:
                with st.sidebar.spinner("Download quote in corso..."):
                    data_odds, req_rem = fetch_all_active_odds(api_key)
                    st.session_state["odds_dataset"] = data_odds
                    st.session_state["api_req_remaining"] = req_rem
                    
                    if len(data_odds) > 0:
                        st.sidebar.success(f"✅ Scaricate {len(data_odds)} quote!")
                    else:
                        st.sidebar.warning(f"⚠️ {req_rem}")
            else:
                st.sidebar.error("Inserisci prima una API Key valida.")

        odds_dataset = st.session_state["odds_dataset"]

        if len(odds_dataset) > 0:
            st.sidebar.info(f"📊 Quote attive in memoria: {len(odds_dataset)}\n\nCrediti Rimanenti: {st.session_state['api_req_remaining']}")
        else:
            st.sidebar.caption("🔴 API disconnessa (0 quote in memoria). Premere il tasto sopra per scaricarle.")

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

        elif "📊" in modalita:
            # === MODALITÀ STRATEGIE XG & VALUE BET FINDER ===
            strat_info = strat_map[strat_nome]
            params = strat_info["params"]
            win_rate_storico = strat_info["win_rate_storico"]

            df_strat = apply_filters(df_base, params)
            df_played = df_strat[df_strat["GOL CASA"].notna()].copy()
            tot_match = len(df_played)

            win_rate_reale = (df_played["WIN"].sum() / tot_match * 100) if tot_match > 0 else 0
            quota_limite = (100 / win_rate_reale) if win_rate_reale > 0 else 0

            res_delays = (
                calculate_delays_and_cycles(df_played["WIN"])
                if tot_match > 0
                else {
                    "ritardo_attuale": 0,
                    "ritardo_max": 0,
                    "ritardo_medio": 0.0,
                    "ciclo_max_storico": 0,
                    "ciclo_attuale": 0,
                }
            )

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

            st.subheader(f"📊 Analisi Strategia: `{strat_nome}`")

            with st.container(border=True):
                st.markdown(get_combination_string(params))

            st.markdown("#### 📈 Metriche Principali & Cicli di Ritardo")

            r1_c1, r1_c2, r1_c3, r1_c4, r1_c5 = st.columns(5)
            with r1_c1:
                with st.container(border=True):
                    st.caption("Match Totali")
                    st.markdown(f"### {tot_match}")

            with r1_c2:
                with st.container(border=True):
                    st.caption("Win Rate Reale")
                    st.markdown(f"### {format_num_comma(win_rate_reale)}%")

            with r1_c3:
                with st.container(border=True):
                    st.caption("Quota Fair / Limite")
                    st.markdown(f"### {format_num_comma(quota_limite)}")

            with r1_c4:
                with st.container(border=True):
                    st.caption("Ritardo Attuale")
                    st.markdown(f"### {res_delays['ritardo_attuale']}")

            with r1_c5:
                with st.container(border=True):
                    st.caption("Ritardo Max Storico")
                    st.markdown(f"### {res_delays['ritardo_max']}")

            r2_c1, r2_c2, r2_c3, r2_c4, r2_c5, r2_c6 = st.columns(6)
            with r2_c1:
                with st.container(border=True):
                    st.caption("Ritardo Medio")
                    st.markdown(f"### {format_num_comma(res_delays['ritardo_medio'], 2)}")

            with r2_c2:
                with st.container(border=True):
                    st.caption("Ciclo Max Storico")
                    st.markdown(f"### {res_delays['ciclo_max_storico']}")

            with r2_c3:
                with st.container(border=True):
                    st.caption("Ciclo Attuale")
                    st.markdown(f"### {res_delays['ciclo_attuale']}")

            with r2_c4:
                with st.container(border=True):
                    st.caption(f"MM Attuale ({finestra_ma}p)")
                    st.markdown(f"### {format_num_comma(mm_att, 1)}%")

            with r2_c5:
                with st.container(border=True):
                    st.caption(f"MM Minima ({finestra_ma}p)")
                    st.markdown(f"### {format_num_comma(mm_min, 1)}%")

            with r2_c6:
                with st.container(border=True):
                    st.caption(f"MM Massima ({finestra_ma}p)")
                    st.markdown(f"### {format_num_comma(mm_max, 1)}%")

            st.markdown("---")

            if tot_match >= finestra_ma:
                chart_data = pd.DataFrame({
                    f"Media Mobile ({finestra_ma} match)": df_played["MA"],
                    "Frequenza Cumulativa": df_played["FREQ_CUM_DINAMICA"],
                })
                st.line_chart(chart_data)

            render_tables(df_strat, quota_limite, odds_dataset, params["MERCATO"], col_casa, col_ospite)

        elif "📂" in modalita:
            # === MODALITÀ DATABASE TOTALE ===
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

            res_delays = (
                calculate_delays_and_cycles(df_played["WIN"])
                if tot_match > 0
                else {
                    "ritardo_attuale": 0,
                    "ritardo_max": 0,
                    "ritardo_medio": 0.0,
                    "ciclo_max_storico": 0,
                    "ciclo_attuale": 0,
                }
            )

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

            st.markdown("#### 📈 Metriche Principali & Cicli di Ritardo (Database Completo)")

            r1_c1, r1_c2, r1_c3, r1_c4, r1_c5 = st.columns(5)
            with r1_c1:
                with st.container(border=True):
                    st.caption("Match Totali")
                    st.markdown(f"### {tot_match}")

            with r1_c2:
                with st.container(border=True):
                    st.caption("Win Rate Reale")
                    st.markdown(f"### {format_num_comma(win_rate_reale)}%")

            with r1_c3:
                with st.container(border=True):
                    st.caption("Quota Fair / Limite")
                    st.markdown(f"### {format_num_comma(quota_limite)}")

            with r1_c4:
                with st.container(border=True):
                    st.caption("Ritardo Attuale")
                    st.markdown(f"### {res_delays['ritardo_attuale']}")

            with r1_c5:
                with st.container(border=True):
                    st.caption("Ritardo Max Storico")
                    st.markdown(f"### {res_delays['ritardo_max']}")

            r2_c1, r2_c2, r2_c3, r2_c4, r2_c5, r2_c6 = st.columns(6)
            with r2_c1:
                with st.container(border=True):
                    st.caption("Ritardo Medio")
                    st.markdown(f"### {format_num_comma(res_delays['ritardo_medio'], 2)}")

            with r2_c2:
                with st.container(border=True):
                    st.caption("Ciclo Max Storico")
                    st.markdown(f"### {res_delays['ciclo_max_storico']}")

            with r2_c3:
                with st.container(border=True):
                    st.caption("Ciclo Attuale")
                    st.markdown(f"### {res_delays['ciclo_attuale']}")

            with r2_c4:
                with st.container(border=True):
                    st.caption(f"MM Attuale ({finestra_ma}p)")
                    st.markdown(f"### {format_num_comma(mm_att, 1)}%")

            with r2_c5:
                with st.container(border=True):
                    st.caption(f"MM Minima ({finestra_ma}p)")
                    st.markdown(f"### {format_num_comma(mm_min, 1)}%")

            with r2_c6:
                with st.container(border=True):
                    st.caption(f"MM Massima ({finestra_ma}p)")
                    st.markdown(f"### {format_num_comma(mm_max, 1)}%")

            st.markdown("---")

            if tot_match >= finestra_ma:
                chart_data = pd.DataFrame({
                    f"Media Mobile ({finestra_ma} match)": df_played["MA"],
                    "Frequenza Cumulativa": df_played["FREQ_CUM_DINAMICA"],
                })
                st.line_chart(chart_data)

            render_tables(df_tot, quota_limite, odds_dataset, mercato_totale_sel, col_casa, col_ospite)

        elif "🔥" in modalita:
            # === MODALITÀ CICLI MAX DA PUNTARE ===
            st.subheader("🔥 Strategie & Mercati in Ciclo Max da Puntare")
            st.caption("Filtro attivo: mostra solo le strategie/mercati con Ciclo Attuale >= Ciclo Max Storico")

            cicli_target = []

            for item in ranked_strategies:
                name = item["nome"]
                params = item["params"]
                df_strat = apply_filters(df_base, params)
                df_played = df_strat[df_strat["GOL CASA"].notna()].copy()

                if len(df_played) > 0:
                    delays = calculate_delays_and_cycles(df_played["WIN"])
                    c_att = delays["ciclo_attuale"]
                    c_max = delays["ciclo_max_storico"]

                    if c_att >= c_max and c_max > 0:
                        tot = len(df_played)
                        wr = (df_played["WIN"].sum() / tot * 100)
                        q_fair = (100 / wr) if wr > 0 else 0.0

                        cicli_target.append({
                            "Tipo": "Strategia Salvata",
                            "Nome / Mercato": name,
                            "Mercato Specifico": params["MERCATO"],
                            "Ciclo Attuale": c_att,
                            "Ciclo Max Storico": c_max,
                            "Ritardo Medio": format_num_comma(delays["ritardo_medio"], 2),
                            "Win Rate Reale": f"{format_num_comma(wr)}%",
                            "Quota Fair": format_num_comma(q_fair),
                            "Match Giocati": tot,
                            "Filtri / Dettagli": get_combination_string(params),
                        })

            for m in MERCATI_TOTALI:
                params_m = {
                    "SOMMA": None, "DC": None, "C1": None, "C2": None,
                    "MEDIA CASA": None, "MEDIA OSPITE": None,
                    "MERCATO": m
                }
                df_m = apply_filters(df_base, params_m)
                df_played_m = df_m[df_m["GOL CASA"].notna()].copy()

                if len(df_played_m) > 0:
                    delays_m = calculate_delays_and_cycles(df_played_m["WIN"])
                    c_att_m = delays_m["ciclo_attuale"]
                    c_max_m = delays_m["ciclo_max_storico"]

                    if c_att_m >= c_max_m and c_max_m > 0:
                        tot_m = len(df_played_m)
                        wr_m = (df_played_m["WIN"].sum() / tot_m * 100)
                        q_fair_m = (100 / wr_m) if wr_m > 0 else 0.0

                        cicli_target.append({
                            "Tipo": "Mercato Totale",
                            "Nome / Mercato": f"Database Totale - {m}",
                            "Mercato Specifico": m,
                            "Ciclo Attuale": c_att_m,
                            "Ciclo Max Storico": c_max_m,
                            "Ritardo Medio": format_num_comma(delays_m["ritardo_medio"], 2),
                            "Win Rate Reale": f"{format_num_comma(wr_m)}%",
                            "Quota Fair": format_num_comma(q_fair_m),
                            "Match Giocati": tot_m,
                            "Filtri / Dettagli": "Database Totale (Senza filtri extra)",
                        })

            if cicli_target:
                df_cicli_res = pd.DataFrame(cicli_target)
                st.dataframe(df_cicli_res, use_container_width=True)
            else:
                st.info("Al momento nessuna strategia o mercato ha il Ciclo Attuale maggiore o uguale al Ciclo Max Storico.")

except Exception as e:
    st.error(f"❌ Errore durante l'esecuzione dell'applicazione: {e}")
