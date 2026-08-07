import io
import json
import os
import re
import zipfile
from datetime import datetime, timedelta
import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="Dashboard Analisi xG & Value Bet Finder",
    page_icon="⚽",
    layout="wide",
)

# ==========================================
# CONFIGURAZIONE E CREDENZIALI
# ==========================================
LINK_GOOGLE_DRIVE = "https://docs.google.com/spreadsheets/d/1xmLiTz2YDi7XSKHwli1noUTgc2F0xxIxS5NJJ4digCE/edit?usp=sharing"
FILE_QUOTE_LOCALE = "quote_salvate.json"

TELEGRAM_BOT_TOKEN = "8841718832:AAGJaB7mB4wv51TZA6WY0cThwRNDEuvvoFw"

# LISTA DESTINATARI: Tu in privato + Il canale condiviso
TELEGRAM_DESTINATARI = [
    "1192615708",      # Il tuo Chat ID Privato
    "-1004447605760"    # ID del Canale/Gruppo Telegram
]


# ==========================================
# GESTIONE SALVATAGGIO PERMANENTE E TELEGRAM
# ==========================================
def carica_quote_locali():
    """Carica il dizionario delle quote salvate dal file JSON locale."""
    if os.path.exists(FILE_QUOTE_LOCALE):
        try:
            with open(FILE_QUOTE_LOCALE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def salva_quota_locale(chiave_match, valore_quota):
    """Salva una singola quota all'interno del file JSON locale."""
    quote = carica_quote_locali()
    quote[chiave_match] = valore_quota
    with open(FILE_QUOTE_LOCALE, "w", encoding="utf-8") as f:
        json.dump(quote, f, indent=4, ensure_ascii=False)
    st.session_state["saved_odds"][chiave_match] = valore_quota


if "saved_odds" not in st.session_state:
    st.session_state["saved_odds"] = carica_quote_locali()


def escape_markdown(text):
    """Pulisce il testo dai caratteri speciali che possono causare errori nel Markdown di Telegram."""
    if not isinstance(text, str):
        text = str(text)
    # Rimuove i caratteri che rompono la formattazione
    return re.sub(r'[*_`\[\]]', '', text)


def invia_singolo_messaggio_telegram(chat_id, testo):
    """Invia un singolo blocco di testo tramite Telegram API."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": testo,
        "parse_mode": "Markdown",
    }
    try:
        res = requests.post(url, json=payload, timeout=20)
        if res.status_code == 200:
            return True, "OK"
        else:
            return False, f"HTTP {res.status_code}: {res.text}"
    except Exception as e:
        return False, str(e)


def invia_telegram(testo):
    """Divide i messaggi se troppo lunghi (>3500 char) e li invia a tutti i destinatari."""
    # Splitta il testo in blocchi per evitare il limite di 4096 caratteri di Telegram
    MAX_CHAR = 3500
    blocchi = []
    
    if len(testo) <= MAX_CHAR:
        blocchi.append(testo)
    else:
        righe = testo.split("\n")
        blocco_corrente = ""
        for riga in righe:
            if len(blocco_corrente) + len(riga) + 1 > MAX_CHAR:
                blocchi.append(blocco_corrente)
                blocco_corrente = riga + "\n"
            else:
                blocco_corrente += riga + "\n"
        if blocco_corrente:
            blocchi.append(blocco_corrente)

    esito_globale = True
    dettagli_errori = []

    for chat_id in TELEGRAM_DESTINATARI:
        for i, blocco in enumerate(blocchi):
            ok, err_msg = invia_singolo_messaggio_telegram(chat_id, blocco)
            if not ok:
                # Prova fallback senza Markdown se c'è un errore di formattazione
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                payload = {"chat_id": chat_id, "text": blocco}
                try:
                    res_fb = requests.post(url, json=payload, timeout=20)
                    if res_fb.status_code != 200:
                        esito_globale = False
                        dettagli_errori.append(f"Chat {chat_id}: {err_msg}")
                except Exception as e_fb:
                    esito_globale = False
                    dettagli_errori.append(f"Chat {chat_id}: {str(e_fb)}")

    if not esito_globale and dettagli_errori:
        st.error(f"⚠️ Errore Telegram dettagliato: {', '.join(set(dettagli_errori))}")

    return esito_globale


def invia_report_esiti_telegram(df_base, strategie_dict, col_casa, col_ospite):
    """Calcola ed invia il resoconto sui match ESITATI con quota salvata da oggi in poi."""
    quote_salvate = carica_quote_locali()

    if not quote_salvate:
        st.warning(
            "⚠️ Nessuna quota salvata trovata nel database locale. Inizia a salvare le quote nelle strategie."
        )
        return

    report_strats = {}
    totale_vinte_globali = 0
    totale_perse_globali = 0
    profitto_totale_unita = 0.0

    for strat_nome, params in strategie_dict.items():
        df_strat = apply_filters(df_base, params)
        df_played = df_strat[df_strat["GOL CASA"].notna()].copy()

        vinte_strat = 0
        perse_strat = 0
        profitto_strat = 0.0

        for idx, row in df_played.iterrows():
            m_key, _ = get_match_keys(
                row, col_casa, col_ospite, params.get("MERCATO", "")
            )

            if m_key in quote_salvate:
                q_val = float(quote_salvate[m_key])
                if q_val > 1.0:
                    is_win = int(row["WIN"]) == 1
                    if is_win:
                        vinte_strat += 1
                        totale_vinte_globali += 1
                        profitto_strat += q_val - 1.0
                        profitto_totale_unita += q_val - 1.0
                    else:
                        perse_strat += 1
                        totale_perse_globali += 1
                        profitto_strat -= 1.0
                        profitto_totale_unita -= 1.0

        tot_tracciati = vinte_strat + perse_strat
        if tot_tracciati > 0:
            wr_tracciato = (vinte_strat / tot_tracciati) * 100
            report_strats[strat_nome] = {
                "vinte": vinte_strat,
                "perse": perse_strat,
                "totali": tot_tracciati,
                "wr": wr_tracciato,
                "profitto": profitto_strat,
                "wr_storico": (
                    (df_played["WIN"].sum() / len(df_played) * 100)
                    if len(df_played) > 0
                    else 0.0
                ),
            }

    tot_globali = totale_vinte_globali + totale_perse_globali
    if tot_globali == 0:
        st.info(
            "ℹ️ Nessun match esitato corrisponde alle quote attualmente salvate."
        )
        return

    wr_globale = (totale_vinte_globali / tot_globali) * 100
    roi_globale = (profitto_totale_unita / tot_globali) * 100

    messaggio = "📊 *RESOCONTO MATCH ESITATI (TRACCIATI DA OGGI)*\n"
    messaggio += (
        f"📅 _Data Report:_ `{datetime.now().strftime('%d/%m/%Y %H:%M')}`\n\n"
    )

    messaggio += "📈 *PERFORMANCE GLOBALE:* \n"
    messaggio += f"⚽ Match Esitati Totali: `{tot_globali}`\n"
    messaggio += (
        f"✅ Vinte: `{totale_vinte_globali}` | ❌ Perse: `{totale_perse_globali}`\n"
    )
    messaggio += f"🎯 Win Rate Reale: `{format_num_comma(wr_globale)}%`\n"
    messaggio += f"💰 PnL Netto (1u/bet): `{format_num_comma(profitto_totale_unita, 2)}u`\n"
    messaggio += f"🚀 ROI Reale: `{format_num_comma(roi_globale, 2)}%`\n"
    messaggio += "==================================\n\n"

    for strat_nome, data in report_strats.items():
        s_clean = escape_markdown(strat_nome)
        messaggio += f"📌 *STRATEGIA:* `{s_clean}`\n"
        messaggio += (
            f"📊 Vinte: `{data['vinte']}` | Perse: `{data['perse']}` (Tot: `{data['totali']}`)\n"
        )
        messaggio += f"🎯 Win Rate 'Da Oggi': `{format_num_comma(data['wr'])}%`\n"
        messaggio += f"🏛️ Win Rate Storico DB: `{format_num_comma(data['wr_storico'])}%`\n"
        messaggio += f"💵 Profitto Netto: `{format_num_comma(data['profitto'], 2)}u`\n"
        messaggio += "----------------------------------\n"

    ok = invia_telegram(messaggio)
    if ok:
        st.success(
            f"✅ Report Esiti inviato sia a te che nel canale! ({tot_globali} match analizzati)"
        )


def genera_e_invia_report_48h_strategie(
    df_base, strategie_dict, col_casa, col_ospite
):
    """Scansiona TUTTE le strategie e invia su Telegram i match delle prossime 48h."""
    adesso = datetime.now()
    limite_48h = adesso + timedelta(hours=48)

    report_strats = {}
    totale_match_trovati = 0

    for strat_nome, params in strategie_dict.items():
        df_strat = apply_filters(df_base, params)
        df_played = df_strat[df_strat["GOL CASA"].notna()].copy()
        tot_match_strat = len(df_played)

        win_rate_reale = (
            (df_played["WIN"].sum() / tot_match_strat * 100)
            if tot_match_strat > 0
            else 0
        )
        quota_limite_strat = (
            (100 / win_rate_reale) if win_rate_reale > 0 else 0.0
        )

        df_future = df_strat[df_strat["GOL CASA"].isna()].copy()

        if len(df_future) == 0:
            continue

        df_future["DATETIME_MATCH"] = pd.NaT
        data_cols = [c for c in df_future.columns if "DATA" in str(c).upper()]
        ora_cols = [
            c
            for c in df_future.columns
            if any(k in str(c).upper() for k in ["ORA", "ORARIO"])
        ]

        for idx, row in df_future.iterrows():
            try:
                data_str = (
                    str(row[data_cols[0]]).split()[0] if data_cols else ""
                )
                ora_str = (
                    str(row[ora_cols[0]]).replace("00:00:00", "").strip()
                    if ora_cols
                    else "00:00"
                )
                if not ora_str or ora_str.lower() == "nan":
                    ora_str = "00:00"
                
                dt_obj = pd.to_datetime(f"{data_str} {ora_str}", dayfirst=True, errors="coerce")
                if pd.isna(dt_obj):
                    dt_obj = pd.to_datetime(f"{data_str} {ora_str}", errors="coerce")
                    
                df_future.at[idx, "DATETIME_MATCH"] = dt_obj
            except Exception:
                pass

        mask_48h = (df_future["DATETIME_MATCH"] >= adesso) & (
            df_future["DATETIME_MATCH"] <= limite_48h
        )
        df_48h = df_future[mask_48h].copy().sort_values(by="DATETIME_MATCH")

        if len(df_48h) > 0:
            report_strats[strat_nome] = {
                "quota_limite": quota_limite_strat,
                "matches": df_48h,
                "mercato": params.get("MERCATO", ""),
            }
            totale_match_trovati += len(df_48h)

    if totale_match_trovati == 0:
        msg_empty = (
            f"📅 *REPORT STRATEGIE (PROSSIME 48 ORE)*\n"
            f"⏱️ _Finestra:_ `{adesso.strftime('%d/%m %H:%M')}` ➔ `{limite_48h.strftime('%d/%m %H:%M')}`\n\n"
            f"ℹ️ Nessun match in programma nelle prossime 48 ore per alcuna strategia."
        )
        ok = invia_telegram(msg_empty)
        if ok:
            st.info("ℹ️ Report inviato su Telegram: nessuna partita programmata nelle prossime 48h.")
        return

    messaggio = f"📅 *REPORT STRATEGIE (PROSSIME 48 ORE)*\n"
    messaggio += f"⏱️ _Da_ `{adesso.strftime('%d/%m %H:%M')}` _a_ `{limite_48h.strftime('%d/%m %H:%M')}`\n"
    messaggio += f"🎯 *Match totali identificati:* `{totale_match_trovati}`\n"
    messaggio += "==================================\n\n"

    for strat_nome, data in report_strats.items():
        q_limite = data["quota_limite"]
        df_m = data["matches"]
        m_strat = data["mercato"]

        s_clean = escape_markdown(strat_nome)
        messaggio += f"📌 *STRATEGIA:* `{s_clean}`\n"
        messaggio += f"⚖️ *Quota Limite Strategia:* `{format_num_comma(q_limite)}`\n\n"

        for idx, row in df_m.iterrows():
            casa, ospite = get_clean_team_names(row, col_casa, col_ospite)
            casa_clean = escape_markdown(casa)
            ospite_clean = escape_markdown(ospite)

            m_key, generic_key = get_match_keys(
                row, col_casa, col_ospite, m_strat
            )

            dt_str = (
                row["DATETIME_MATCH"].strftime("%d/%m/%Y %H:%M")
                if pd.notna(row["DATETIME_MATCH"])
                else "N/D"
            )

            quote_salvate = carica_quote_locali()
            quota_inserita = quote_salvate.get(m_key, None)

            messaggio += f"⚽ *{casa_clean} - {ospite_clean}*\n"
            messaggio += f"🕐 *Orario:* `{dt_str}`\n"

            if quota_inserita and float(quota_inserita) > 1.0:
                q_val = float(quota_inserita)
                messaggio += (
                    f"💰 *Quota Trovata:* `{format_num_comma(q_val)}`\n"
                )
                if q_val >= q_limite:
                    messaggio += "STATUS: 🟢 *VALUE BET CONFERMATA*\n"
                else:
                    messaggio += "STATUS: 🔴 *NO VALUE*\n"
            else:
                messaggio += "💰 *Quota Trovata:* ⚠️ *NON TROVATA PER QUESTO MERCATO*\n"
                messaggio += (
                    "STATUS: ⏳ *IN ATTESA DI CONFERMA/SALVATAGGIO*\n"
                )

            messaggio += "----------------------------------\n"
        messaggio += "\n"

    ok = invia_telegram(messaggio)
    if ok:
        st.success(
            f"✅ Report inviato con successo sia a te che nel canale! Notificati {totale_match_trovati} match."
        )


# ==========================================
# UTILITÀ E PULIZIA DATI
# ==========================================
def get_drive_direct_url(url):
    file_id_match = re.search(r"/d/([a-zA-Z0-9_-]+)", url)
    if file_id_match:
        return f"https://docs.google.com/spreadsheets/d/{file_id_match.group(1)}/export?format=xlsx"
    return url


def clean_numeric_column(series):
    return pd.to_numeric(
        series.astype(str).str.replace(",", "."), errors="coerce"
    )


def format_num_comma(val, decimals=2):
    if val is None or pd.isna(val):
        return "-"
    try:
        fmt = f"{{:.{decimals}f}}"
        return fmt.format(float(val)).replace(".", ",")
    except Exception:
        return str(val)


def get_clean_team_names(row, col_casa, col_ospite):
    val_casa = str(row.get(col_casa, "")).strip()
    val_ospite = str(row.get(col_ospite, "")).strip()

    regex_separatori = r"\s+(?:vs|v|-)\s+"

    if re.search(regex_separatori, val_casa, re.IGNORECASE):
        parts = re.split(regex_separatori, val_casa, flags=re.IGNORECASE)
        if len(parts) >= 2:
            return parts[0].strip(), parts[1].strip()

    if re.search(regex_separatori, val_ospite, re.IGNORECASE):
        parts = re.split(regex_separatori, val_ospite, flags=re.IGNORECASE)
        if len(parts) >= 2:
            return parts[0].strip(), parts[1].strip()

    return val_casa or "Casa", val_ospite or "Ospite"


def get_match_keys(row, col_casa, col_ospite, mercato=""):
    casa, ospite = get_clean_team_names(row, col_casa, col_ospite)
    data_match = ""
    for c in row.index:
        if "DATA" in str(c).upper():
            data_match = str(row[c]).strip()
            break

    mercato_clean = str(mercato).lower().strip()
    generic_key = f"{data_match}_{casa}_{ospite}".lower()
    generic_key = re.sub(r"\s+", " ", generic_key)

    specific_key = f"{generic_key}_{mercato_clean}"
    return specific_key, generic_key


def carica_quota_con_fallback(chiave_specifica, chiave_generica):
    quote = carica_quote_locali()

    if chiave_specifica in quote:
        return quote[chiave_specifica], True

    for k, v in quote.items():
        if k.startswith(chiave_generica):
            return v, False

    return 1.00, False


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

    cols_to_clean = [
        "SOMMA",
        "DC",
        "C1",
        "C2",
        "MEDIA CASA",
        "MEDIA OSPITE",
        "GOL CASA",
        "GOL OSPITE",
    ]
    for col in cols_to_clean:
        if col in df.columns:
            df[col] = clean_numeric_column(df[col])

    for col in df.columns:
        if "DATA" in str(col).upper():
            df[col] = (
                pd.to_datetime(df[col], errors="coerce").dt.strftime("%d/%m/%Y")
            ).fillna(df[col])
        elif any(k in str(col).upper() for k in ["ORA", "ORARIO"]):
            df[col] = (
                df[col].astype(str).str.replace("00:00:00", "").str.strip()
            )

    return df


def detect_team_columns(df):
    col_casa, col_ospite = None, None

    for c in df.columns:
        c_clean = str(c).upper().strip()
        if c_clean in [
            "CASA",
            "SQUADRA CASA",
            "SQUADRA_CASA",
            "HOME",
            "SQUADRA 1",
            "SQUADRA_1",
            "SQUADRA H",
            "HOME TEAM",
        ]:
            col_casa = c
        elif c_clean in [
            "OSPITE",
            "SQUADRA OSPITE",
            "SQUADRA_OSPITE",
            "AWAY",
            "SQUADRA 2",
            "SQUADRA_2",
            "TRASFERTA",
            "SQUADRA A",
            "AWAY TEAM",
        ]:
            col_ospite = c

    if not col_casa or not col_ospite:
        text_cols = [
            c
            for c in df.columns
            if df[c].dtype == "object" or df[c].dtype == "string"
        ]
        for c in text_cols:
            c_clean = str(c).upper().strip()
            if not col_casa and "CASA" in c_clean and not any(
                x in c_clean
                for x in [
                    "GOL",
                    "MEDIA",
                    "C1",
                    "XG",
                    "SUBITI",
                    "FATTI",
                    "QUOTA",
                ]
            ):
                col_casa = c
            elif not col_ospite and any(
                x in c_clean for x in ["OSPITE", "TRASFERTA", "AWAY"]
            ) and not any(
                x in c_clean
                for x in [
                    "GOL",
                    "MEDIA",
                    "C2",
                    "XG",
                    "SUBITI",
                    "FATTI",
                    "QUOTA",
                ]
            ):
                col_ospite = c

    if not col_casa or not col_ospite:
        string_cols = []
        for c in df.columns:
            sample_val = df[c].dropna().astype(str).head(5).tolist()
            if sample_val and not any(
                re.match(r"^-?\d+[\.,]?\d*$", v.strip()) for v in sample_val
            ):
                if not any(
                    k in str(c).upper()
                    for k in ["DATA", "ORA", "ORARIO", "LEGA", "CAMPIONATO"]
                ):
                    string_cols.append(c)
        if len(string_cols) >= 2:
            col_casa = col_casa or string_cols[0]
            col_ospite = col_ospite or string_cols[1]
        elif len(string_cols) == 1:
            col_casa = col_casa or string_cols[0]
            col_ospite = col_ospite or string_cols[0]

    return col_casa, col_ospite


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
    ritardo_medio = (
        (sum(ritardi_conclusi) / len(ritardi_conclusi))
        if len(ritardi_conclusi) > 0
        else 0.0
    )

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
        win_rate_reale = (
            (df_strat_played["WIN"].sum() / tot * 100) if tot > 0 else 0.0
        )

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

    return (
        f"🎯 **Mercato Target:** `{mercato}`  \n⚙️ **Combinazione Parametri:** "
        + "  •  ".join(condizioni)
    )


def render_tables(df_filtered, quota_limite, col_casa, col_ospite, mercato=""):
    df_played = (
        df_filtered[df_filtered["GOL CASA"].notna()]
        .copy()
        .reset_index(drop=True)
    )
    df_future = (
        df_filtered[df_filtered["GOL CASA"].isna()]
        .copy()
        .reset_index(drop=True)
    )

    st.subheader(f"⏳ Prossime Partite da Giocare ({len(df_future)})")

    if len(df_future) > 0:
        st.info(
            "💡 Le quote già inserite per questa partita in altri mercati vengono proposte in automatico. Premi **💾 Salva** per memorizzarle per questo specifico mercato."
        )

        for idx, row in df_future.iterrows():
            m_key, generic_key = get_match_keys(row, col_casa, col_ospite, mercato)
            nome_casa, nome_ospite = get_clean_team_names(
                row, col_casa, col_ospite
            )

            data_match = ""
            for col_d in df_future.columns:
                if "DATA" in str(col_d).upper():
                    data_match = f" ({row[col_d]})"
                    break

            c_info, c_qlim, c_input, c_btn, c_res = st.columns(
                [3, 1.5, 1.5, 1.2, 2]
            )

            with c_info:
                st.markdown(f"**{nome_casa} - {nome_ospite}**{data_match}")

            with c_qlim:
                st.markdown(
                    f"Quota Limite: **{format_num_comma(quota_limite)}**"
                )

            val_proposto, e_esatta = carica_quota_con_fallback(m_key, generic_key)

            with c_input:
                q_val = st.number_input(
                    "Quota Trovata",
                    min_value=1.00,
                    max_value=50.00,
                    value=float(val_proposto),
                    step=0.05,
                    key=f"input_{m_key}",
                )

            with c_btn:
                st.markdown(
                    "<div style='padding-top: 25px;'></div>",
                    unsafe_allow_html=True,
                )
                if st.button("💾 Salva", key=f"btn_{m_key}"):
                    salva_quota_locale(m_key, q_val)
                    st.toast(
                        f"Quota {format_num_comma(q_val)} salvata per {nome_casa} - {nome_ospite} ({mercato})!"
                    )
                    st.rerun()

            with c_res:
                if abs(float(q_val) - float(val_proposto)) > 0.001:
                    st.caption("⚠️ *Modificata (non salvata)*")
                elif e_esatta:
                    st.caption("✅ *Quota Salvata (Mercato)*")
                elif val_proposto > 1.00:
                    st.caption("💡 *Quota Suggerita (da altro mercato)*")
                else:
                    st.caption("⏳ *In attesa*")

                if q_val > 1.00:
                    if float(q_val) >= quota_limite:
                        st.success(
                            f"🟢 **VALUE BET!** ({format_num_comma(q_val)})"
                        )
                    else:
                        st.error(f"🔴 **NO VALUE** ({format_num_comma(q_val)})")

            st.divider()
    else:
        st.info("Nessuna prossima partita trovata per questa selezione.")

    st.subheader(f"📋 Ultime Partite Processate ({len(df_played)})")
    if len(df_played) > 0:
        quote_salvate = carica_quote_locali()
        df_played["QUOTA SALVATA"] = df_played.apply(
            lambda r: format_num_comma(
                quote_salvate.get(
                    get_match_keys(r, col_casa, col_ospite, mercato)[0], None
                )
            ),
            axis=1,
        )

        cols_played = []
        for c in df_played.columns:
            if any(k in str(c).upper() for k in ["DATA", "ORA", "ORARIO"]):
                if c not in cols_played:
                    cols_played.append(c)

        if (
            col_casa
            and col_casa in df_played.columns
            and col_casa not in cols_played
        ):
            cols_played.append(col_casa)
        if (
            col_ospite
            and col_ospite in df_played.columns
            and col_ospite not in cols_played
            and col_ospite != col_casa
        ):
            cols_played.append(col_ospite)

        cols_played.append("QUOTA SALVATA")

        altre_cols = [
            c
            for c in df_played.columns
            if any(
                k in str(c).upper()
                for k in ["GOL CASA", "GOL OSPITE", "SOMMA", "DC", "C1", "C2", "WIN"]
            )
            and c not in cols_played
        ]
        cols_played.extend(altre_cols)

        df_display = (
            df_played[cols_played].iloc[::-1].copy().reset_index(drop=True)
        )
        df_display.index = range(1, len(df_display) + 1)

        integer_cols = ["GOL CASA", "GOL OSPITE", "WIN"]
        for c_int in integer_cols:
            if c_int in df_display.columns:
                df_display[c_int] = (
                    pd.to_numeric(df_display[c_int], errors="coerce")
                    .fillna(0)
                    .astype(int)
                )

        float_cols = [
            c
            for c in df_display.select_dtypes(
                include=["float", "float64"]
            ).columns
            if c not in integer_cols and c != "QUOTA SALVATA"
        ]
        for col in float_cols:
            df_display[col] = df_display[col].apply(
                lambda x: format_num_comma(x)
            )

        st.dataframe(df_display, use_container_width=True)


# ==========================================
# APPLICAZIONE PRINCIPALE STREAMLIT
# ==========================================
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

        ranked_strategies = get_sorted_strategies(df_base, STRATEGIE_SALVATE)
        strat_map = {item["nome"]: item for item in ranked_strategies}

        MERCATI_TOTALI = [
            "1",
            "X",
            "2",
            "1X",
            "X2",
            "12",
            "GOL",
            "NO GOL",
            "OVER 1,5",
            "OVER 2,5",
            "UNDER 1,5",
            "UNDER 2,5",
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
            strat_nome = st.sidebar.selectbox(
                "Strategia attiva:", list(strat_map.keys())
            )
            st.sidebar.markdown("---")
        elif "📂" in modalita:
            st.sidebar.header("🎯 SELEZIONA MERCATO TOTALE")
            mercato_totale_sel = st.sidebar.selectbox(
                "Mercato da analizzare:", MERCATI_TOTALI
            )
            st.sidebar.markdown("---")

        if st.sidebar.button("🔄 Aggiorna Dati da Google Drive"):
            st.cache_data.clear()

        col_casa_auto, col_ospite_auto = detect_team_columns(df_base)
        st.sidebar.markdown("---")
        st.sidebar.header("📌 Selezione Colonne Squadre")
        all_cols = list(df_base.columns)

        idx_casa = (
            all_cols.index(col_casa_auto) if col_casa_auto in all_cols else 0
        )
        idx_ospite = (
            all_cols.index(col_ospite_auto)
            if col_ospite_auto in all_cols
            else (1 if len(all_cols) > 1 else 0)
        )

        col_casa = st.sidebar.selectbox(
            "Colonna Squadra Casa (o Partita Intera):",
            all_cols,
            index=idx_casa,
        )
        col_ospite = st.sidebar.selectbox(
            "Colonna Squadra Ospite:", all_cols, index=idx_ospite
        )

        # SEZIONE TELEGRAM BOT NELLA SIDEBAR
        st.sidebar.markdown("---")
        st.sidebar.header("📲 NOTIFICHE TELEGRAM")
        if st.sidebar.button(
            "🚀 Invia Report 48h su Telegram", use_container_width=True
        ):
            genera_e_invia_report_48h_strategie(
                df_base, STRATEGIE_SALVATE, col_casa, col_ospite
            )

        if st.sidebar.button(
            "📈 Invia Report Esiti su Telegram", use_container_width=True
        ):
            invia_report_esiti_telegram(
                df_base, STRATEGIE_SALVATE, col_casa, col_ospite
            )

        # --- CORPO PRINCIPALE DASHBOARD ---
        if "🚨" in modalita:
            st.subheader(
                "🚨 Report Sottoperformance & Inversione Trend (Mean Reversion)"
            )
            st.caption(
                "Identifica Strategie e Mercati al Minimo Storico di Media Mobile pronti al recupero verso la Media Storica."
            )

            finestra_alert = st.sidebar.slider(
                "Finestra Media Mobile per Alert", 10, 50, 20, 5
            )

            alert_underperforming, alert_bounce_back = [], []

            def analizza_serie_per_trend(
                tipo_entita, nome_entita, mercato_entita, df_played, wr_target, dettagli_str
            ):
                tot = len(df_played)
                if tot >= finestra_alert:
                    df_played["MA"] = (
                        df_played["WIN"].rolling(window=finestra_alert).mean() * 100
                    )
                    ma_series = df_played["MA"].dropna()
                    if len(ma_series) >= 2:
                        mm_att = ma_series.iloc[-1]
                        mm_prev = ma_series.iloc[-2]
                        mm_min_storica = ma_series.min()
                        scostamento_da_media = mm_att - wr_target
                        distanza_da_minimo = mm_att - mm_min_storica
                        last_win = df_played["WIN"].iloc[-1]

                        if mm_att < wr_target:
                            in_zona_minimo = (
                                "🔴 SI (AL MINIMO STORICO)"
                                if mm_att <= (mm_min_storica + 2.0)
                                else "🟡 NO"
                            )

                            alert_underperforming.append({
                                "Tipo": tipo_entita,
                                "Nome / Mercato": nome_entita,
                                "Mercato": mercato_entita,
                                "Match Giocati": tot,
                                "WR Storico Target": f"{format_num_comma(wr_target)}%",
                                f"MM Attuale ({finestra_alert}p)": f"{format_num_comma(mm_att, 1)}%",
                                "MM Minima Storica": f"{format_num_comma(mm_min_storica, 1)}%",
                                "Distanza da Media": f"{format_num_comma(scostamento_da_media, 1)}%",
                                "Vicino al Minimo": in_zona_minimo,
                                "Filtri / Dettagli": dettagli_str,
                            })

                            if last_win == 1 and mm_att > mm_prev:
                                rimbalzo_val = mm_att - mm_prev
                                era_in_minimo = (
                                    "🔥 RIMBALZO DA MINIMO STORICO"
                                    if mm_prev <= (mm_min_storica + 3.0)
                                    else "📈 RIENTRO IN TREND"
                                )

                                alert_bounce_back.append({
                                    "Tipo": tipo_entita,
                                    "Nome / Mercato": nome_entita,
                                    "Mercato": mercato_entita,
                                    "Stato Inversione": era_in_minimo,
                                    "WR Storico Target": f"{format_num_comma(wr_target)}%",
                                    f"MM Prec ({finestra_alert}p)": f"{format_num_comma(mm_prev, 1)}%",
                                    f"MM Attuale ({finestra_alert}p)": f"{format_num_comma(mm_att, 1)}%",
                                    "Rimbalzo Ultimo Match": f"+{format_num_comma(rimbalzo_val, 1)}%",
                                    "MM Minima Registrata": f"{format_num_comma(mm_min_storica, 1)}%",
                                    "Filtri / Dettagli": dettagli_str,
                                })

            for item in ranked_strategies:
                name = item["nome"]
                wr_storico = item["win_rate_storico"]
                params = item["params"]
                df_strat = apply_filters(df_base, params)
                df_played_strat = df_strat[df_strat["GOL CASA"].notna()].copy()

                analizza_serie_per_trend(
                    "Strategia Salvata",
                    name,
                    params["MERCATO"],
                    df_played_strat,
                    wr_storico,
                    get_combination_string(params),
                )

            for m in MERCATI_TOTALI:
                params_m = {
                    "SOMMA": None,
                    "DC": None,
                    "C1": None,
                    "C2": None,
                    "MEDIA CASA": None,
                    "MEDIA OSPITE": None,
                    "MERCATO": m,
                }
                df_m = apply_filters(df_base, params_m)
                df_played_m = df_m[df_m["GOL CASA"].notna()].copy()
                tot_m = len(df_played_m)
                wr_globale_m = (
                    (df_played_m["WIN"].sum() / tot_m * 100) if tot_m > 0 else 0
                )

                analizza_serie_per_trend(
                    "Mercato DB Totale",
                    f"Database Totale - {m}",
                    m,
                    df_played_m,
                    wr_globale_m,
                    "Database Totale (Senza filtri extra)",
                )

            st.markdown(
                "### 🚀 SEGNALI DI INVERSIONE TREND (Punto di Inizio Investimento)"
            )
            st.caption(
                "Questi mercati erano in forte sottoperformance/al minimo storico e hanno registrato una WIN nell'ultimo match, avviando il ciclo rialzista verso la media."
            )
            if alert_bounce_back:
                st.dataframe(
                    pd.DataFrame(alert_bounce_back), use_container_width=True
                )
            else:
                st.info(
                    "Nessun segnale di inversione attivo registrato nell'ultima giornata."
                )

            st.markdown("---")

            st.markdown(
                "### 📉 MERCATI E STRATEGIE IN SOTTOPERFORMANCE (In Attesa del Minimo / Trigger)"
            )
            st.caption(
                "Elenco dei mercati attualmente sotto la media storica. Da monitorare per attendere il primo segnale WIN di inversione."
            )
            if alert_underperforming:
                st.dataframe(
                    pd.DataFrame(alert_underperforming),
                    use_container_width=True,
                )
            else:
                st.info(
                    "Tutte le strategie e i mercati sono stabili sopra la loro media target."
                )

        elif "📊" in modalita:
            strat_info = strat_map[strat_nome]
            params = strat_info["params"]
            win_rate_storico = strat_info["win_rate_storico"]

            df_strat = apply_filters(df_base, params)
            df_played = df_strat[df_strat["GOL CASA"].notna()].copy()
            tot_match = len(df_played)

            win_rate_reale = (
                (df_played["WIN"].sum() / tot_match * 100)
                if tot_match > 0
                else 0
            )
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
            finestra_ma = st.sidebar.slider(
                "Finestra Media Mobile (Partite)", 10, 50, 20, 5
            )

            mm_att, mm_min, mm_max = 0.0, 0.0, 0.0
            if tot_match >= finestra_ma:
                df_played["MA"] = (
                    df_played["WIN"].rolling(window=finestra_ma).mean() * 100
                )
                df_played["FREQ_CUM_DINAMICA"] = (
                    df_played["WIN"].expanding().mean() * 100
                )

                ma_valid = df_played["MA"].dropna()
                if len(ma_valid) > 0:
                    mm_att = ma_valid.iloc[-1]
                    mm_min = ma_valid.min()
                    mm_max = ma_valid.max()

            st.subheader(f"📊 Analisi Strategia: `{strat_nome}`")

            with st.container(border=True):
                st.markdown(get_combination_string(params))

            st.markdown("#### 📈 Metmetriche Principali & Cicli di Ritardo")

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
                    st.markdown(
                        f"### {format_num_comma(res_delays['ritardo_medio'], 2)}"
                    )

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

            render_tables(
                df_strat,
                quota_limite,
                col_casa,
                col_ospite,
                mercato=params["MERCATO"],
            )

        elif "📂" in modalita:
            st.subheader(
                f"📂 Analisi Database Totale — Mercato: `{mercato_totale_sel}`"
            )

            params_tot = {
                "SOMMA": None,
                "DC": None,
                "C1": None,
                "C2": None,
                "MEDIA CASA": None,
                "MEDIA OSPITE": None,
                "MERCATO": mercato_totale_sel,
            }

            df_tot = apply_filters(df_base, params_tot)
            df_played = df_tot[df_tot["GOL CASA"].notna()].copy()
            tot_match = len(df_played)

            win_rate_reale = (
                (df_played["WIN"].sum() / tot_match * 100)
                if tot_match > 0
                else 0
            )
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
            finestra_ma = st.sidebar.slider(
                "Finestra Media Mobile (Partite)", 10, 50, 20, 5
            )

            mm_att, mm_min, mm_max = 0.0, 0.0, 0.0
            if tot_match >= finestra_ma:
                df_played["MA"] = (
                    df_played["WIN"].rolling(window=finestra_ma).mean() * 100
                )
                df_played["FREQ_CUM_DINAMICA"] = (
                    df_played["WIN"].expanding().mean() * 100
                )

                ma_valid = df_played["MA"].dropna()
                if len(ma_valid) > 0:
                    mm_att = ma_valid.iloc[-1]
                    mm_min = ma_valid.min()
                    mm_max = ma_valid.max()

            with st.container(border=True):
                st.markdown(get_combination_string(params_tot))

            st.markdown(
                "#### 📈 Metrice Principali & Cicli di Ritardo (Database Completo)"
            )

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
                    st.markdown(
                        f"### {format_num_comma(res_delays['ritardo_medio'], 2)}"
                    )

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

            render_tables(
                df_tot,
                quota_limite,
                col_casa,
                col_ospite,
                mercato=mercato_totale_sel,
            )

        elif "🔥" in modalita:
            st.subheader("🔥 Strategie & Mercati in Ciclo Max da Puntare")
            st.caption(
                "Filtro attivo: mostra solo le strategie/mercati con Ciclo Attuale >= Ciclo Max Storico"
            )

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
                        wr = df_played["WIN"].sum() / tot * 100
                        q_fair = (100 / wr) if wr > 0 else 0.0

                        cicli_target.append({
                            "Tipo": "Strategia Salvata",
                            "Nome / Mercato": name,
                            "Mercato Specifico": params["MERCATO"],
                            "Ciclo Attuale": c_att,
                            "Ciclo Max Storico": c_max,
                            "Ritardo Medio": format_num_comma(
                                delays["ritardo_medio"], 2
                            ),
                            "Win Rate Reale": f"{format_num_comma(wr)}%",
                            "Quota Fair": format_num_comma(q_fair),
                            "Match Giocati": tot,
                            "Filtri / Dettagli": get_combination_string(params),
                        })

            for m in MERCATI_TOTALI:
                params_m = {
                    "SOMMA": None,
                    "DC": None,
                    "C1": None,
                    "C2": None,
                    "MEDIA CASA": None,
                    "MEDIA OSPITE": None,
                    "MERCATO": m,
                }
                df_m = apply_filters(df_base, params_m)
                df_played_m = df_m[df_m["GOL CASA"].notna()].copy()

                if len(df_played_m) > 0:
                    delays_m = calculate_delays_and_cycles(df_played_m["WIN"])
                    c_att_m = delays_m["ciclo_attuale"]
                    c_max_m = delays_m["ciclo_max_storico"]

                    if c_att_m >= c_max_m and c_max_m > 0:
                        tot_m = len(df_played_m)
                        wr_m = df_played_m["WIN"].sum() / tot_m * 100
                        q_fair_m = (100 / wr_m) if wr_m > 0 else 0.0

                        cicli_target.append({
                            "Tipo": "Mercato Totale",
                            "Nome / Mercato": f"Database Totale - {m}",
                            "Mercato Specifico": m,
                            "Ciclo Attuale": c_att_m,
                            "Ciclo Max Storico": c_max_m,
                            "Ritardo Medio": format_num_comma(
                                delays_m["ritardo_medio"], 2
                            ),
                            "Win Rate Reale": f"{format_num_comma(wr_m)}%",
                            "Quota Fair": format_num_comma(q_fair_m),
                            "Match Giocati": tot_m,
                            "Filtri / Dettagli": (
                                "Database Totale (Senza filtri extra)"
                            ),
                        })

            if cicli_target:
                df_cicli_res = pd.DataFrame(cicli_target)
                st.dataframe(df_cicli_res, use_container_width=True)
            else:
                st.info(
                    "Al momento nessuna strategia o mercato ha il Ciclo Attuale maggiore o uguale al Ciclo Max Storico."
                )

except Exception as e:
    st.error(f"❌ Errore durante l'esecuzione dell'applicazione: {e}")
