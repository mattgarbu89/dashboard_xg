import io
import re
import zipfile
import pandas as pd
import requests
import streamlit as st

# Configurazione pagina
st.set_page_config(
    page_title='Dashboard Analisi Mercati xG', page_icon='⚽', layout='wide'
)

# ==========================================
# LINK GOOGLE SHEETS
# ==========================================
LINK_GOOGLE_DRIVE = 'https://docs.google.com/spreadsheets/d/1xmLiTz2YDi7XSKHwli1noUTgc2F0xxIxS5NJJ4digCE/edit?usp=sharing'


# Funzione per Convertire il Link Google Fogli in Download XLSX Diretto
def get_drive_direct_url(url):
  file_id_match = re.search(r'/d/([a-zA-Z0-9_-]+)', url)
  if file_id_match:
    file_id = file_id_match.group(1)
    return f'https://docs.google.com/spreadsheets/d/{file_id}/export?format=xlsx'
  return url


# Funzione di pulizia e caricamento Excel
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


# Funzione per determinare l'esito del mercato
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


st.title('⚽ Dashboard Analisi Tutti i Mercati')

# Pulsante per forzare l'aggiornamento dei dati
if st.sidebar.button('🔄 Aggiorna Dati da Google Drive'):
  st.cache_data.clear()

direct_url = get_drive_direct_url(LINK_GOOGLE_DRIVE)


@st.cache_data(ttl=300)  # Aggiorna automaticamente i dati ogni 5 minuti
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
    # Filtriamo solo le partite con punteggio presente
    df = df_raw[
        df_raw['GOL CASA'].notna() & df_raw['GOL OSPITE'].notna()
    ].copy()
    df.reset_index(drop=True, inplace=True)

    # SELEZIONE MERCATI DALLA SIDEBAR
    MERCATI = [
        '1',
        'X',
        '2',
        '1X',
        'X2',
        '12',
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

    st.sidebar.header('Seleziona Analisi')
    mercato_scelto = st.sidebar.selectbox('Mercato da Analizzare', MERCATI)
    finestra_ma = st.sidebar.slider(
        'Finestra Media Mobile (Partite)',
        min_value=10,
        max_value=50,
        value=20,
        step=5,
    )

    # Calcolo Esito del Mercato
    df['WIN'] = df.apply(
        lambda row: evaluate_market(row, mercato_scelto), axis=1
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

      # METRICHE IN EVIDENZA
      st.subheader(f'Analisi Mercato: {mercato_scelto}')
      col1, col2, col3, col4, col5 = st.columns(5)
      col1.metric('Match Analizzati', tot_match)
      col2.metric('Win Rate Totale', f'{freq_cum:.1f}%')
      col3.metric('Ritardo Attuale', rit_att)
      col4.metric('Ritardo Max Storico', rit_max)
      col5.metric(f'MM Attuale ({finestra_ma}p)', f'{mm_att:.1f}%')

      col_m1, col_m2 = st.columns(2)
      col_m1.metric('MM Minima Registrata', f'{mm_min:.1f}%')
      col_m2.metric('MM Massima Registrata', f'{mm_max:.1f}%')

      # GRAFICO ANDAMENTO
      chart_data = pd.DataFrame({
          f'Media Mobile ({finestra_ma} match)': df['MA'],
          'Frequenza Cumulativa Totale': freq_cum,
      })
      st.line_chart(chart_data)

      # TABELLA ULTIME PARTITE ANALIZZATE (GESTIONE DINAMICA DELLE COLONNE)
      st.subheader('📋 Ultime Partite Processate')

      # Rileva automaticamente le colonne disponibili per la tabella
      cols_disponibili = list(df.columns)
      cols_da_mostrare = []

      # Cerca colonne squadre o informazioni utili
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
                'WIN',
            ]
        ):
          cols_da_mostrare.append(c)

      if not cols_da_mostrare:
        cols_da_mostrare = cols_disponibili[:5]

      st.dataframe(df[cols_da_mostrare].tail(15).iloc[::-1])

    else:
      st.warning(
          f'Partite insufficienti ({tot_match}) nel foglio per calcolare la MM.'
      )

  else:
    st.error(
        'Impossibile leggere il file. Verifica la condivisione del Foglio'
        ' Google.'
    )

except Exception as e:
  st.error(f"Errore durante l'elaborazione dei dati: {e}")
