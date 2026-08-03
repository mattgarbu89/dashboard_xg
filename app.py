import io
import re
import zipfile
import pandas as pd
import requests
import streamlit as st

# Configurazione pagina
st.set_page_config(
    page_title='Dashboard Incroci xG', page_icon='⚽', layout='wide'
)

# ==========================================
# LINK GOOGLE SHEETS
# ==========================================
LINK_GOOGLE_DRIVE = 'https://docs.google.com/spreadsheets/d/1xmLiTz2YDi7XSKHwli1noUTgc2F0xxIxS5NJJ4digCE/edit?usp=sharing'


# Funzione per Convertire il Link Google Fogli/Drive in Link di Download XLSX Diretto
def get_drive_direct_url(url):
  file_id_match = re.search(r'/d/([a-zA-Z0-9_-]+)', url)
  if file_id_match:
    file_id = file_id_match.group(1)
    # Esporta direttamente in formato XLSX per Google Fogli
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


st.title('⚽ Dashboard Analisi Pareggi (X)')

# Pulsante per forzare l'aggiornamento manuale dei dati
if st.sidebar.button('🔄 Aggiorna Dati da Google Drive'):
  st.cache_data.clear()

# Caricamento Automatico da Drive/Sheets
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
    df_raw['WIN'] = (df_raw['GOL CASA'] == df_raw['GOL OSPITE']).astype(int)
    st.success('✅ Dati collegati e aggiornati automaticamente!')

    # COMBINAZIONI SALVATE
    COMBINAZIONI = {
        '1. Opzione 2 (313 Match | SOMMA >= -1, MC >= 1.1)': {
            'SOMMA': -1.0,
            'MEDIA CASA': 1.1,
            'MEDIA OSPITE': None,
            'C1': None,
            'C2': None,
            'DC': None,
        },
        '2. Gold Standard (140 Match | SOMMA >= -0.5, DC >= 0)': {
            'SOMMA': -0.5,
            'MEDIA CASA': 1.1,
            'MEDIA OSPITE': 1.51,
            'C1': None,
            'C2': None,
            'DC': 0.0,
        },
        '3. Top Stabilità (396 Match | C1 >= -1, MO <= 1.7)': {
            'SOMMA': None,
            'MEDIA CASA': None,
            'MEDIA OSPITE': 1.7,
            'C1': -1.0,
            'C2': None,
            'DC': None,
        },
        '4. Bilanciata 300+ (304 Match | SOMMA >= -2, MC >= 0.8, MO <= 1.5)': {
            'SOMMA': -2.0,
            'MEDIA CASA': 0.8,
            'MEDIA OSPITE': 1.5,
            'C1': None,
            'C2': None,
            'DC': None,
        },
        '5. Base Standard (263 Match | SOMMA >= -1.03, MO <= 1.54)': {
            'SOMMA': -1.03,
            'MEDIA CASA': None,
            'MEDIA OSPITE': 1.54,
            'C1': None,
            'C2': None,
            'DC': None,
        },
    }

    # SELEZIONE DALLA SIDEBAR
    st.sidebar.header('Filtri Strategia')
    scelta = st.sidebar.selectbox(
        'Seleziona Combinazione Salvata', list(COMBINAZIONI.keys())
    )
    finestra_ma = st.sidebar.slider(
        'Finestra Media Mobile', min_value=10, max_value=30, value=20, step=5
    )

    params = COMBINAZIONI[scelta]

    # FILTRAGGIO
    mask = df_raw['GOL CASA'].notna() & df_raw['GOL OSPITE'].notna()
    if params['SOMMA'] is not None:
      mask &= df_raw['SOMMA'] >= params['SOMMA']
    if params['MEDIA CASA'] is not None:
      mask &= df_raw['MEDIA CASA'] >= params['MEDIA CASA']
    if params['MEDIA OSPITE'] is not None:
      mask &= df_raw['MEDIA OSPITE'] <= params['MEDIA OSPITE']
    if params['C1'] is not None:
      mask &= df_raw['C1'] >= params['C1']
    if params['DC'] is not None:
      mask &= df_raw['DC'] >= params['DC']

    df = df_raw[mask].copy().reset_index(drop=True)
    tot_match = len(df)

    if tot_match >= finestra_ma:
      wins = df['WIN'].sum()
      freq_cum = (wins / tot_match) * 100
      df['MA'] = df['WIN'].rolling(window=finestra_ma).mean() * 100

      # RITARDI
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

      # METRICHE
      st.subheader(f'Analisi: {scelta}')
      col1, col2, col3, col4, col5 = st.columns(5)
      col1.metric('Match Totali', tot_match)
      col2.metric('Win Rate (X)', f'{freq_cum:.1f}%')
      col3.metric('Ritardo Attuale', rit_att)
      col4.metric('Ritardo Max', rit_max)
      col5.metric(f'MM Attuale ({finestra_ma}p)', f'{mm_att:.1f}%')

      col_m1, col_m2 = st.columns(2)
      col_m1.metric('MM Minima', f'{mm_min:.1f}%')
      col_m2.metric('MM Massima', f'{mm_max:.1f}%')

      # GRAFICO
      chart_data = pd.DataFrame({
          f'Media Mobile ({finestra_ma} match)': df['MA'],
          'Frequenza Cumulativa': freq_cum,
          'Breakeven Quota 3.30': 30.30,
      })
      st.line_chart(chart_data)
    else:
      st.warning(f'Partite insufficienti ({tot_match}) per calcolare la MM.')

  else:
    st.error(
        'Impossibile scaricare il file. Assicurati che il Foglio Google sia'
        ' condiviso con "Chiunque abbia il link".'
    )

except Exception as e:
  st.error(f"Errore durante l'elaborazione dei dati: {e}")
