import io
import re
import zipfile
import pandas as pd
import requests
import streamlit as st

# Configurazione pagina
st.set_page_config(
    page_title='Dashboard Strategie & Filtri xG', page_icon='⚽', layout='wide'
)

# ==========================================
# LINK GOOGLE SHEETS
# ==========================================
LINK_GOOGLE_DRIVE = 'https://docs.google.com/spreadsheets/d/1xmLiTz2YDi7XSKHwli1noUTgc2F0xxIxS5NJJ4digCE/edit?usp=sharing'


def get_drive_direct_url(url):
  file_id_match = re.search(r'/d/([a-zA-Z0-9_-]+)', url)
  if file_id_match:
    file_id = file_id_match.group(1)
    return f'https://docs.google.com/spreadsheets/d/{file_id}/export?format=xlsx'
  return url


def load_clean_df(file_bytes):
  output = io.BytesIO()
  with zipfile.ZipFile(file_bytes, 'r') as zin:
    with zipfile.ZipFile(output, 'w') as zout:
      for item in zin.infolist():
        buffer = zin.read(item.filename)
        if 'sheet' in item.filename and item.filename.endswith('.xml'):
          buffer = re.sub(b'<autoFilter[^>]*/>', b'', buffer)
          buffer = re.sub(
              b'<autoFilter[^>]*>.*?</autoFilter>',
              b'',
              buffer,
              flags=re.DOTALL,
          )
          buffer = re.sub(
              b'<customFilters[^>]*>.*?</customFilters>',
              b'',
              buffer,
              flags=re.DOTALL,
          )
          buffer = re.sub(b'<filter[^>]*/>', b'', buffer)
        zout.writestr(item, buffer)
  output.seek(0)
  return pd.read_excel(output, sheet_name='INCROCI GEMINI', engine='openpyxl')


def evaluate_market(row, market):
  gc = row['GOL CASA']
  go = row['GOL OSPITE']
  gt = gc + go

  if market == '1':
    return int(gc > go)
  elif market == 'X':
    return int(gc == go)
  elif market == '2':
    return int(go > gc)
  elif market == '1X':
    return int(gc >= go)
  elif market == 'X2':
    return int(go >= gc)
  elif market == '12':
    return int(gc != go)
  elif market == 'ESITO 1-1':
    return int(gc == 1 and go == 1)

  elif market == 'OVER 1,5':
    return int(gt > 1.5)
  elif market == 'OVER 2,5':
    return int(gt > 2.5)
  elif market == 'OVER 3,5':
    return int(gt > 3.5)
  elif market == 'UNDER 1,5':
    return int(gt < 1.5)
  elif market == 'UNDER 2,5':
    return int(gt < 2.5)
  elif market == 'UNDER 3,5':
    return int(gt < 3.5)

  elif market == 'GOL CASA':
    return int(gc > 0)
  elif market == 'OVER 1,5 CASA':
    return int(gc > 1.5)
  elif market == 'OVER 2,5 CASA':
    return int(gc > 2.5)
  elif market == 'UNDER 1,5 CASA':
    return int(gc < 1.5)
  elif market == 'UNDER 2,5 CASA':
    return int(gc < 2.5)

  elif market == 'GOL OSPITE':
    return int(go > 0)
  elif market == 'OVER 1,5 OSPITE':
    return int(go > 1.5)
  elif market == 'OVER 2,5 OSPITE':
    return int(go > 2.5)
  elif market == 'UNDER 1,5 OSPITE':
    return int(go < 1.5)
  elif market == 'UNDER 2,5 OSPITE':
    return int(go < 2.5)

  return 0


st.title('⚽ Dashboard Analisi xG & Mercati')

if st.sidebar.button('🔄 Aggiorna Dati da Google Drive'):
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
  with st.spinner('Lettura dati in corso...'):
    df_raw = fetch_data_from_drive(direct_url)

  if df_raw is not None:
    df_base = df_raw[
        df_raw['GOL CASA'].notna() & df_raw['GOL OSPITE'].notna()
    ].copy()
    df_base.reset_index(drop=True, inplace=True)

    MERCATI = [
        '1',
        'X',
        '2',
        '1X',
        'X2',
        '12',
        'ESITO 1-1',
        'OVER 1,5',
        'OVER 2,5',
        'OVER 3,5',
        'UNDER 1,5',
        'UNDER 2,5',
        'UNDER 3,5',
        'GOL CASA',
        'OVER 1,5 CASA',
        'OVER 2,5 CASA',
        'UNDER 1,5 CASA',
        'UNDER 2,5 CASA',
        'GOL OSPITE',
        'OVER 1,5 OSPITE',
        'OVER 2,5 OSPITE',
        'UNDER 1,5 OSPITE',
        'UNDER 2,5 OSPITE',
    ]

    # ==========================================
    # LE 5 STRATEGIE FORNITE
    # ==========================================
    STRATEGIE_SALVATE = {
        '1. Esito 1-1 (C2 <= 0 | Media Ospite < 1.50)': {
            'SOMMA': None,
            'DC': None,
            'C1': None,
            'C2': 0.0,
            'MEDIA CASA': None,
            'MEDIA OSPITE': 1.50,
            'MEDIA_OSPITE_OP': '<',
            'MERCATO': 'ESITO 1-1',
        },
        '2. Under 2,5 Ospite (C2 <= -4)': {
            'SOMMA': None,
            'DC': None,
            'C1': None,
            'C2': -4.0,
            'MEDIA CASA': None,
            'MEDIA OSPITE': None,
            'MERCATO': 'UNDER 2,5 OSPITE',
        },
        '3. Esito X - Base (Somma >= -1.03 | Media Ospite <= -1.54)': {
            'SOMMA': -1.03,
            'DC': None,
            'C1': None,
            'C2': None,
            'MEDIA CASA': None,
            'MEDIA OSPITE': -1.54,
            'MEDIA_OSPITE_OP': '<=',
            'MERCATO': 'X',
        },
        '4. Esito X - Gold (Somma >= -0.79 | Media Casa >= 1.1 | Media Ospite <= 1.51)': {
            'SOMMA': -0.79,
            'DC': None,
            'C1': None,
            'C2': None,
            'MEDIA CASA': 1.1,
            'MEDIA OSPITE': 1.51,
            'MEDIA_OSPITE_OP': '<=',
            'MERCATO': 'X',
        },
        '5. Esito X - Stabilità (C1 >= -1.02 | DC >= 0.62 | Media Ospite <= 1.51)': {
            'SOMMA': None,
            'DC': 0.62,
            'C1': -1.02,
            'C2': None,
            'MEDIA CASA': None,
            'MEDIA OSPITE': 1.51,
            'MEDIA_OSPITE_OP': '<=',
            'MERCATO': 'X',
        },
        'Filtro Manuale Personalizzato': 'MANUALE',
    }

    # SELETTORE PRINCIPALE
    st.sidebar.header('📌 SELEZIONA MODALITÀ')
    modalita = st.sidebar.radio(
        'Scegli il tipo di analisi:',
        [
            '1. Mercati Singoli (Database Totale)',
            '2. Strategie xG & Filtri Salvati',
        ],
    )

    df = df_base.copy()
    titolo_analisi = ''

    # -------------------------------------------------------------------------
    # MODALITÀ 1: MERCATI SINGOLI SU TUTTE LE PARTITE
    # -------------------------------------------------------------------------
    if '1.' in modalita:
      st.sidebar.markdown('---')
      st.sidebar.subheader('Mercato da Analizzare')
      mercato_scelto = st.sidebar.selectbox('Seleziona Mercato', MERCATI)
      titolo_analisi = (
          f'Analisi Mercato: {mercato_scelto} (Su tutte le partite)'
      )
      df['WIN'] = df.apply(
          lambda row: evaluate_market(row, mercato_scelto), axis=1
      )

    # -------------------------------------------------------------------------
    # MODALITÀ 2: STRATEGIE E FILTRI xG SALVATI
    # -------------------------------------------------------------------------
    else:
      st.sidebar.markdown('---')
      st.sidebar.subheader('Scegli Strategia Salvata')
      strat_nome = st.sidebar.selectbox(
          'Strategia con Filtri', list(STRATEGIE_SALVATE.keys())
      )

      # Se sceglie una strategia salvata preimpostata
      if strat_nome != 'Filtro Manuale Personalizzato':
        params = STRATEGIE_SALVATE[strat_nome]
        somma_val = params.get('SOMMA')
        dc_val = params.get('DC')
        c1_val = params.get('C1')
        c2_val = params.get('C2')
        mc_val = params.get('MEDIA CASA')
        mo_val = params.get('MEDIA OSPITE')
        mo_op = params.get('MEDIA_OSPITE_OP', '<=')
        mercato_target = params['MERCATO']
        titolo_analisi = f'Strategia Salvata: {strat_nome}'

      # Se sceglie il Filtro Manuale Personalizzato
      else:
        st.sidebar.markdown('---')
        st.sidebar.subheader('Imposta Filtri Manuali')
        mercato_target = st.sidebar.selectbox('Mercato Target', MERCATI, index=1)

        use_somma = st.sidebar.checkbox('Filtra per SOMMA Minima', value=False)
        somma_val = (
            st.sidebar.number_input(
                'SOMMA Minima', value=-1.00, step=0.01, format='%.2f'
            )
            if use_somma
            else None
        )

        use_dc = st.sidebar.checkbox('Filtra per DC Minima', value=False)
        dc_val = (
            st.sidebar.number_input(
                'DC Minima', value=0.00, step=0.01, format='%.2f'
            )
            if use_dc
            else None
        )

        use_c1 = st.sidebar.checkbox('Filtra per C1 Minimo', value=False)
        c1_val = (
            st.sidebar.number_input(
                'C1 Minimo', value=-1.00, step=0.01, format='%.2f'
            )
            if use_c1
            else None
        )

        use_c2 = st.sidebar.checkbox('Filtra per C2 Massimo', value=False)
        c2_val = (
            st.sidebar.number_input(
                'C2 Massimo', value=0.00, step=0.01, format='%.2f'
            )
            if use_c2
            else None
        )

        use_mc = st.sidebar.checkbox('Filtra per Media Casa Minima', value=False)
        mc_val = (
            st.sidebar.number_input(
                'Media Casa Minima', value=1.10, step=0.01, format='%.2f'
            )
            if use_mc
            else None
        )

        use_mo = st.sidebar.checkbox('Filtra per Media Ospite', value=False)
        mo_val = (
            st.sidebar.number_input(
                'Media Ospite Soglia', value=1.50, step=0.01, format='%.2f'
            )
            if use_mo
            else None
        )
        mo_op = '<='  # Default manuale

        titolo_analisi = f'Filtro Manuale Personalizzato - Target: {mercato_target}'

      # Applicazione dei Filtri al Database
      mask = pd.Series([True] * len(df))
      if somma_val is not None and 'SOMMA' in df.columns:
        mask &= df['SOMMA'] >= somma_val
      if dc_val is not None and 'DC' in df.columns:
        mask &= df['DC'] >= dc_val
      if c1_val is not None and 'C1' in df.columns:
        mask &= df['C1'] >= c1_val
      if c2_val is not None and 'C2' in df.columns:
        mask &= df['C2'] <= c2_val
      if mc_val is not None and 'MEDIA CASA' in df.columns:
        mask &= df['MEDIA CASA'] >= mc_val
      if mo_val is not None and 'MEDIA OSPITE' in df.columns:
        if mo_op == '<':
          mask &= df['MEDIA OSPITE'] < mo_val
        else:
          mask &= df['MEDIA OSPITE'] <= mo_val

      df = df[mask].copy().reset_index(drop=True)
      df['WIN'] = df.apply(
          lambda row: evaluate_market(row, mercato_target), axis=1
      )

    # -------------------------------------------------------------------------
    # IMPOSTAZIONI MEDIA MOBILE E DASHBOARD
    # -------------------------------------------------------------------------
    st.sidebar.markdown('---')
    finestra_ma = st.sidebar.slider(
        'Finestra Media Mobile (Partite)',
        min_value=10,
        max_value=50,
        value=20,
        step=5,
    )

    tot_match = len(df)

    if tot_match >= finestra_ma:
      wins = df['WIN'].sum()
      freq_cum = (wins / tot_match) * 100
      df['MA'] = df['WIN'].rolling(window=finestra_ma).mean() * 100

      # Calcolo Ritardi
      rit_att, rit_max, curr_r = 0, 0, 0
      for res in df['WIN']:
        if res == 0:
          curr_r += 1
          if curr_r > rit_max:
            rit_max = curr_r
        else:
          curr_r = 0
      rit_att = curr_r

      ma_clean = df['MA'].dropna()
      mm_att = ma_clean.iloc[-1]
      mm_min = ma_clean.min()
      mm_max = ma_clean.max()

      # VISUALIZZAZIONE METRICHE
      st.subheader(f'📊 {titolo_analisi}')
      col1, col2, col3, col4, col5 = st.columns(5)
      col1.metric('Match Filtrati / Totali', tot_match)
      col2.metric('Win Rate Totale', f'{freq_cum:.1f}%')
      col3.metric('Ritardo Attuale', rit_att)
      col4.metric('Ritardo Max Storico', rit_max)
      col5.metric(f'MM Attuale ({finestra_ma}p)', f'{mm_att:.1f}%')

      col_m1, col_m2 = st.columns(2)
      col_m1.metric('MM Minima Registrata', f'{mm_min:.1f}%')
      col_m2.metric('MM Massima Registrata', f'{mm_max:.1f}%')

      # GRAFICO
      chart_data = pd.DataFrame({
          f'Media Mobile ({finestra_ma} match)': df['MA'],
          'Frequenza Cumulativa Totale': freq_cum,
      })
      st.line_chart(chart_data)

      # TABELLA ULTIME PARTITE
      st.subheader('📋 Ultime Partite Processate')
      cols_disponibili = list(df.columns)
      cols_da_mostrare = []

      for c in cols_disponibili:
        if any(
            k in c.upper()
            for k in [
                'CASA',
                'OSPITE',
                'SQUADRA',
                'MATCH',
                'PARTITA',
                'GOL',
                'SOMMA',
                'DC',
                'C1',
                'C2',
                'WIN',
            ]
        ):
          cols_da_mostrare.append(c)

      if not cols_da_mostrare:
        cols_da_mostrare = cols_disponibili[:6]

      st.dataframe(df[cols_da_mostrare].tail(15).iloc[::-1])

    else:
      st.warning(
          f'Partite insufficienti ({tot_match}) con questi criteri per'
          f' calcolare la Media Mobile da {finestra_ma} gare.'
      )

  else:
    st.error(
        'Impossibile leggere il file. Verifica la condivisione del Foglio'
        ' Google.'
    )

except Exception as e:
  st.error(f"Errore durante l'elaborazione dei dati: {e}")
