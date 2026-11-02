import streamlit as st
import pandas as pd
import numpy as np
import ccxt
import plotly.express as px
from datetime import datetime, timezone, date, time as dt_time

# ----------------- Seiten-Konfiguration -----------------
st.set_page_config(page_title="PZP Cycle Profiler (Toleranz)", page_icon="🎯", layout="wide")

st.title("🎯 PZP Zyklus-Profiler mit ±1-Kerzen-Toleranz")
st.caption("Berücksichtigt Markt-Ungenauigkeiten durch optionale ±1-Kerzen-Toleranz (Cluster-Glättung).")

# Session State Initialisierung für die Übernahme in die Hauptansicht
if 'override_cycle_length' not in st.session_state:
    st.session_state['override_cycle_length'] = None
if 'override_offset' not in st.session_state:
    st.session_state['override_offset'] = None
if 'override_auto_align' not in st.session_state:
    st.session_state['override_auto_align'] = None

# US-kompatible Datenquelle (Kraken)
@st.cache_resource
def get_exchange():
    return ccxt.kraken({
        'enableRateLimit': True,
    })

exchange = get_exchange()

# Timeframe-Konfigurationen
TF_CONFIG = {
    "15m": {"fetch_tf": "15m", "resample": None,  "limit": 720},
    "30m": {"fetch_tf": "30m", "resample": None,  "limit": 720},
    "1h":  {"fetch_tf": "1h",  "resample": None,  "limit": 720},
    "2h":  {"fetch_tf": "1h",  "resample": "2h",  "limit": 720},
    "4h":  {"fetch_tf": "4h",  "resample": None,  "limit": 720},
    "1d":  {"fetch_tf": "1d",  "resample": None,  "limit": 720},
    "2d":  {"fetch_tf": "1d",  "resample": "2D",  "limit": 720},
    "3d":  {"fetch_tf": "1d",  "resample": "3D",  "limit": 720},
    "4d":  {"fetch_tf": "1d",  "resample": "4D",  "limit": 720},
    "1w":  {"fetch_tf": "1w",  "resample": None,  "limit": 500}
}

# ----------------- Sidebar / Parameter -----------------
with st.sidebar:
    st.header("⚙️ Markt & Zyklus")
    
    market_list = ["BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "AVAX", "DOT", "LINK", "NEAR"]
    selected_coin = st.selectbox("Markt / Asset", options=market_list, index=0)
    
    tf_options = list(TF_CONFIG.keys())
    tf_choice = st.selectbox("Timeframe", options=tf_options, index=tf_options.index("1d"))
    
    st.markdown("---")
    st.subheader("🔁 Zyklus-Einstellungen")
    
    default_cycle_len = st.session_state['override_cycle_length'] if st.session_state['override_cycle_length'] is not None else 6
    cycle_length = st.number_input("Zykluslänge N (in Kerzen)", min_value=3, max_value=120, value=int(default_cycle_len), step=1)
    cycle_count = st.number_input("Anzahl Zyklen zum Vergleichen", min_value=2, max_value=40, value=10, step=1)
    
    st.markdown("##### 🔍 Zyklus-Scanner (Punkt-Analyse)")
    col_s1, col_s2 = st.columns(2)
    with col_s1:
        scan_min_n = st.number_input("Min. Zyklus (N)", min_value=3, max_value=120, value=3, step=1)
    with col_s2:
        scan_max_n = st.number_input("Max. Zyklus (N)", min_value=3, max_value=120, value=30, step=1)
    
    st.markdown("##### ⏱️ Toleranz-Filter")
    use_tolerance = st.checkbox("±1-Kerze Toleranz einbeziehen", value=True, help="Zählt einen Zyklus als Treffer, wenn die Trendwende an Pos -1, Pos oder Pos +1 stattfand.")
    
    st.markdown("##### Basis-Startzeitpunkt")
    col_d, col_t = st.columns([1, 1])
    with col_d:
        start_date_input = st.date_input("Startdatum", value=date(2024, 1, 1))
    with col_t:
        start_time_input = st.time_input("Startuhrzeit (UTC)", value=dt_time(0, 0), step=300)
        
    default_auto_align = st.session_state['override_auto_align'] if st.session_state['override_auto_align'] is not None else True
    auto_align = st.checkbox("🎯 Auto-Phase-Alignment (Startpunkt optimieren)", value=default_auto_align)
    
    manual_offset = 0
    if not auto_align:
        default_off = st.session_state['override_offset'] if st.session_state['override_offset'] is not None else 0
        manual_offset = st.slider("Manueller Start-Offset (Kerzen)", 0, int(cycle_length - 1), int(min(default_off, cycle_length - 1)))
        
    st.markdown("---")
    st.subheader("📐 PZP-Algorithmus Parameter")
    pL = st.number_input("Pivot Links (L)", min_value=1, value=2)
    pR = st.number_input("Pivot Rechts (R)", min_value=1, value=2)
    factor = st.number_input("Projektionsfaktor", min_value=0.05, value=0.5, step=0.05)
    
    if st.button("🔄 Neu berechnen", use_container_width=True):
        st.session_state['override_cycle_length'] = None
        st.session_state['override_offset'] = None
        st.session_state['override_auto_align'] = None
        st.rerun()

# ----------------- Daten abrufen -----------------
def fetch_candles(coin, tf_choice):
    cfg = TF_CONFIG[tf_choice]
    symbol = f"{coin}/USD"
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe=cfg["fetch_tf"], limit=cfg["limit"])
    if not ohlcv or len(ohlcv) < 30:
        return None
    
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    df.set_index('timestamp', inplace=True)
    
    if cfg["resample"]:
        df = df.resample(cfg["resample"]).agg({
            'Open': 'first',
            'High': 'max',
            'Low': 'min',
            'Close': 'last',
            'Volume': 'sum'
        }).dropna()
        
    return df

# ----------------- PZP Trendwechsel-Erkennung -----------------
def identify_trend_changes(df, L, R, f):
    highs = df['High'].values
    lows = df['Low'].values
    n = len(df)
    has_turn = np.zeros(n, dtype=bool)
    ph_indices = []
    pl_indices = []
    
    for i in range(L, n - R):
        if all(highs[i] > highs[i - k] for k in range(1, L + 1)) and all(highs[i] > highs[i + k] for k in range(1, R + 1)):
            ph_indices.append(i)
            if len(ph_indices) >= 2:
                dist = np.ceil(f * (ph_indices[-1] - ph_indices[-2]))
                target_bar = int(ph_indices[-1] + dist + R)
                if target_bar < n:
                    has_turn[target_bar] = True
                    
        if all(lows[i] < lows[i - k] for k in range(1, L + 1)) and all(lows[i] < lows[i + k] for k in range(1, R + 1)):
            pl_indices.append(i)
            if len(pl_indices) >= 2:
                dist = np.ceil(f * (pl_indices[-1] - pl_indices[-2]))
                target_bar = int(pl_indices[-1] + dist + R)
                if target_bar < n:
                    has_turn[target_bar] = True

    df['has_turn'] = has_turn
    return df

# ----------------- Punkt-Zyklus-Scanner (Erweitert mit Detail-Informationen je Zyklus) -----------------
def evaluate_turning_points(df_sub, min_n, max_n, c_count):
    turns_array = df_sub['has_turn'].values
    timestamps = df_sub.index
    total_len = len(turns_array)
    
    turn_indices = np.where(turns_array)[0]
    
    exact_records = []
    tol_records = []
    
    for pt_num, t_idx in enumerate(turn_indices, start=1):
        best_exact_pct = -1.0
        best_exact_details = [] # Speichert formatierte Strings für N, Offset und Position
        best_exact_primary_N = min_n
        best_exact_primary_offset = 0
        
        best_tol_pct = -1.0
        best_tol_details = []
        best_tol_primary_N = min_n
        best_tol_primary_offset = 0
        
        for N in range(int(min_n), int(max_n) + 1):
            max_exact_for_N = -1.0
            best_off_exact = 0
            
            max_tol_for_N = -1.0
            best_off_tol = 0
            
            for offset in range(N):
                if offset + c_count * N > total_len:
                    continue
                if not (offset <= t_idx < offset + c_count * N):
                    continue
                
                pos = (t_idx - offset) % N
                window = turns_array[offset : offset + c_count * N].reshape(c_count, N)
                
                # Exakt
                exact_hits = np.sum(window[:, pos])
                exact_pct = (exact_hits / c_count) * 100.0
                if exact_pct > max_exact_for_N:
                    max_exact_for_N = exact_pct
                    best_off_exact = offset
                
                # Toleranz
                valid_pos = [(pos - 1) % N, pos, (pos + 1) % N]
                tol_hits = np.sum(np.any(window[:, valid_pos], axis=1))
                tol_pct = (tol_hits / c_count) * 100.0
                if tol_pct > max_tol_for_N:
                    max_tol_for_N = tol_pct
                    best_off_tol = offset
            
            if max_exact_for_N >= 0:
                pos_calc = (t_idx - best_off_exact) % N
                detail_str = f"N={N} (Pos {pos_calc}, Offset +{best_off_exact})"
                
                if max_exact_for_N > best_exact_pct:
                    best_exact_pct = max_exact_for_N
                    best_exact_details = [detail_str]
                    best_exact_primary_N = N
                    best_exact_primary_offset = best_off_exact
                elif np.isclose(max_exact_for_N, best_exact_pct):
                    best_exact_details.append(detail_str)
                    
            if max_tol_for_N >= 0:
                pos_calc_tol = (t_idx - best_off_tol) % N
                detail_str_tol = f"N={N} (Pos {pos_calc_tol}, Offset +{best_off_tol})"
                
                if max_tol_for_N > best_tol_pct:
                    best_tol_pct = max_tol_for_N
                    best_tol_details = [detail_str_tol]
                    best_tol_primary_N = N
                    best_tol_primary_offset = best_off_tol
                elif np.isclose(max_tol_for_N, best_tol_pct):
                    best_tol_details.append(detail_str_tol)
        
        dt_str = timestamps[t_idx].strftime("%d.%m.%Y %H:%M")
        
        if best_exact_pct >= 0:
            exact_hits_cnt = int(round((best_exact_pct / 100.0) * c_count))
            exact_records.append({
                "Punkt": f"Punkt {pt_num}",
                "Zeitpunkt": dt_str,
                "Kerzen-Index": t_idx,
                "Max. Trefferquote (Exakt)": round(best_exact_pct, 1),
                "Treffer (Zyklen)": f"{exact_hits_cnt} / {c_count}",
                "Beste Zyklen, Positionen & Offsets": "; ".join(best_exact_details),
                "_raw_N": best_exact_primary_N,
                "_raw_offset": best_exact_primary_offset,
                "_raw_pct": best_exact_pct
            })
            
        if best_tol_pct >= 0:
            tol_hits_cnt = int(round((best_tol_pct / 100.0) * c_count))
            tol_records.append({
                "Punkt": f"Punkt {pt_num}",
                "Zeitpunkt": dt_str,
                "Kerzen-Index": t_idx,
                "Max. Trefferquote (±1 Toleranz)": round(best_tol_pct, 1),
                "Treffer (Zyklen)": f"{tol_hits_cnt} / {c_count}",
                "Beste Zyklen, Positionen & Offsets": "; ".join(best_tol_details),
                "_raw_N": best_tol_primary_N,
                "_raw_offset": best_tol_primary_offset,
                "_raw_pct": best_tol_pct
            })
            
    return pd.DataFrame(exact_records), pd.DataFrame(tol_records)

# ----------------- Hauptausführung -----------------
with st.spinner(f"Lade Kursdaten für {selected_coin}..."):
    df_raw = fetch_candles(selected_coin, tf_choice)

if df_raw is None or len(df_raw) < 50:
    st.error("Fehler beim Laden der Kursdaten.")
    st.stop()

df = identify_trend_changes(df_raw, pL, pR, factor)

start_dt = pd.to_datetime(datetime.combine(start_date_input, start_time_input)).tz_localize('UTC')
df_sub = df[df.index >= start_dt].copy()

scanner_cycle_count = int(cycle_count)

if len(df_sub) < (cycle_count * cycle_length + cycle_length):
    available_cycles = (len(df_sub) - cycle_length) // cycle_length
    if available_cycles < 2:
        st.error("Zu wenige Kerzen ab Startzeitpunkt vorhanden. Bitte wähle ein früheres Startdatum.")
        st.stop()
    cycle_count = available_cycles

# ----------------- Phasen-Optimierung -----------------
phase_eval = []

for offset in range(int(cycle_length)):
    sub_slice = df_sub.iloc[offset : offset + cycle_count * cycle_length].copy()
    sub_slice['Position'] = [idx % cycle_length for idx in range(len(sub_slice))]
    sub_slice['Cycle_ID'] = [idx // cycle_length + 1 for idx in range(len(sub_slice))]
    
    max_rate = 0.0
    best_pos = 0
    total_score = 0.0
    
    for pos in range(cycle_length):
        if use_tolerance:
            valid_pos = [(pos - 1) % cycle_length, pos, (pos + 1) % cycle_length]
            turns = sub_slice[(sub_slice['Position'].isin(valid_pos)) & (sub_slice['has_turn'] == True)]
        else:
            turns = sub_slice[(sub_slice['Position'] == pos) & (sub_slice['has_turn'] == True)]
            
        hit_cycles = turns['Cycle_ID'].nunique()
        pct = (hit_cycles / cycle_count) * 100
        total_score += pct**2
        if pct > max_rate:
            max_rate = pct
            best_pos = pos
            
    start_time_actual = sub_slice.index[0]
    phase_eval.append({
        "Offset (Kerzen)": offset,
        "Effektiver Start": start_time_actual.strftime("%d.%m.%y %H:%M"),
        "Spitzen-Trefferquote (%)": round(max_rate, 1),
        "Beste Position": best_pos,
        "Score": total_score
    })

phase_df = pd.DataFrame(phase_eval)

if auto_align:
    best_phase_row = phase_df.sort_values(by=["Spitzen-Trefferquote (%)", "Score"], ascending=False).iloc[0]
    chosen_offset = int(best_phase_row["Offset (Kerzen)"])
else:
    chosen_offset = manual_offset
    best_phase_row = phase_df[phase_df["Offset (Kerzen)"] == chosen_offset].iloc[0]

# ----------------- Finale Analyse mit gewähltem Offset -----------------
df_final = df_sub.iloc[chosen_offset : chosen_offset + cycle_count * cycle_length].copy()
df_final['Position'] = [idx % cycle_length for idx in range(len(df_final))]
df_final['Cycle_ID'] = [idx // cycle_length + 1 for idx in range(len(df_final))]

position_stats = []
for pos in range(cycle_length):
    exact_turns = df_final[(df_final['Position'] == pos) & (df_final['has_turn'] == True)]
    exact_cycles = exact_turns['Cycle_ID'].nunique()
    exact_pct = round((exact_cycles / cycle_count) * 100, 1)
    
    valid_pos = [(pos - 1) % cycle_length, pos, (pos + 1) % cycle_length]
    tol_turns = df_final[(df_final['Position'].isin(valid_pos)) & (df_final['has_turn'] == True)]
    tol_cycles = tol_turns['Cycle_ID'].nunique()
    tol_pct = round((tol_cycles / cycle_count) * 100, 1)
    
    position_stats.append({
        "Position": pos,
        "Exakt (%)": exact_pct,
        "Mit ±1 Toleranz (%)": tol_pct,
        "Treffer Zyklen (Toleranz)": f"{tol_cycles} / {cycle_count}"
    })

stats_df = pd.DataFrame(position_stats)
display_metric = "Mit ±1 Toleranz (%)" if use_tolerance else "Exakt (%)"

# ----------------- UI Dashboard -----------------
if auto_align:
    st.success(f"🎯 **Bester Startpunkt gefunden:** Versatz um **+{chosen_offset} Kerzen** erzielt **{best_phase_row['Spitzen-Trefferquote (%)']}% Treffer** auf Position **{int(best_phase_row['Beste Position'])}** ({'inkl. ±1 Toleranz' if use_tolerance else 'exakt'})!")

col1, col2, col3, col4 = st.columns(4)
col1.metric("Gewählter Offset", f"+{chosen_offset} Kerzen")
col2.metric("Effektiver Zyklus 1 Start", f"{df_final.index[0].strftime('%d.%m.%y %H:%M')}")
col3.metric("Zykluslänge", f"{cycle_length} Kerzen ({tf_choice})")
top_res = stats_df.sort_values(by=display_metric, ascending=False).iloc[0]
col4.metric(f"Top-Cluster ({display_metric})", f"Pos {int(top_res['Position'])}", f"{top_res[display_metric]}% Treffer")

st.markdown("---")

# Interaktives Balkendiagramm mit Vergleich Exakt vs. Toleranz
st.subheader("📊 Wahrscheinlichkeitsverteilung über den Zyklus")
plot_df = stats_df.melt(id_vars=["Position"], value_vars=["Exakt (%)", "Mit ±1 Toleranz (%)"], 
                        var_name="Methode", value_name="Quote")

fig = px.bar(
    plot_df,
    x="Position",
    y="Quote",
    color="Methode",
    barmode="group",
    text="Quote",
    labels={"Position": f"Kerzenposition im Zyklus (0 bis {cycle_length - 1})", "Quote": "Wahrscheinlichkeit (%)"},
    title=f"Zyklus-Trefferquote: Exakt vs. mit ±1 Kerze Toleranz ({selected_coin} / {tf_choice})",
    color_discrete_map={"Exakt (%)": "#3b82f6", "Mit ±1 Toleranz (%)": "#089981"}
)
fig.update_traces(texttemplate='%{text:.0f}%', textposition='outside')
fig.update_layout(
    yaxis=dict(range=[0, 115]),
    xaxis=dict(tickmode='linear', dtick=1),
    template="plotly_dark",
    height=450
)
st.plotly_chart(fig, use_container_width=True)

# Detailansichten
tab1, tab2, tab3, tab4 = st.tabs([
    "🏆 Rangliste & Matrix", 
    "🔬 Phasen-Vergleich", 
    "📅 Zyklus-Zeiten", 
    "🔍 Punkt-Zyklus-Scanner"
])

with tab1:
    c1, c2 = st.columns([1, 1])
    with c1:
        st.markdown("##### Rangliste der Positionen")
        st.dataframe(
            stats_df.sort_values(by=display_metric, ascending=False),
            column_config={
                "Position": st.column_config.NumberColumn("Position", format="%d"),
                "Exakt (%)": st.column_config.ProgressColumn("Exakt", format="%.1f%%", min_value=0, max_value=100),
                "Mit ±1 Toleranz (%)": st.column_config.ProgressColumn("Mit Toleranz (±1)", format="%.1f%%", min_value=0, max_value=100)
            },
            use_container_width=True,
            hide_index=True
        )
    with c2:
        st.markdown("##### Treffer-Matrix je Zyklus (Exakte Lage)")
        piv = pd.crosstab(
            index=df_final['Cycle_ID'],
            columns=df_final['Position'],
            values=df_final['has_turn'],
            aggfunc=lambda x: "✅" if any(x) else "—"
        ).fillna("—")
        piv.index.name = "Zyklus #"
        st.dataframe(piv, use_container_width=True)

with tab2:
    st.markdown("##### Vergleich aller getesteten Start-Offsets")
    st.caption("Zeigt die maximale Trefferquote für jeden möglichen Startversatz:")
    st.dataframe(
        phase_df.drop(columns=["Score"]),
        column_config={
            "Spitzen-Trefferquote (%)": st.column_config.ProgressColumn("Spitzenquote", format="%.1f%%", min_value=0, max_value=100),
            "Offset (Kerzen)": st.column_config.NumberColumn("Verschiebung", format="%d")
        },
        use_container_width=True,
        hide_index=True
    )

with tab3:
    st.markdown("##### 📅 Start- und Endzeitpunkte sowie Trendwenden der verglichenen Zyklen")
    cycle_times = []
    for cid in range(1, cycle_count + 1):
        c_sub = df_final[df_final['Cycle_ID'] == cid]
        if not c_sub.empty:
            turns_in_cycle = c_sub[c_sub['has_turn'] == True].index.strftime("%d.%m.%Y %H:%M").tolist()
            cycle_times.append({
                "Zyklus #": cid,
                "Start": c_sub.index[0].strftime("%d.%m.%Y %H:%M"),
                "Ende": c_sub.index[-1].strftime("%d.%m.%Y %H:%M"),
                "Anzahl Kerzen": len(c_sub),
                "Trendwenden im Zyklus": ", ".join(turns_in_cycle) if turns_in_cycle else "—"
            })
    st.dataframe(pd.DataFrame(cycle_times), use_container_width=True, hide_index=True)
    
    st.markdown("---")
    st.markdown("##### 🔍 Kerzen-Inspektor je Zyklus (Positionen & Trendwenden)")
    st.caption("Wähle einen spezifischen Zyklus aus, um die exakte Position (0 bis N-1) jeder einzelnen Kerze einzusehen:")
    
    selected_inspect_cycle = st.selectbox(
        "Zyklus für Detail-Inspektion auswählen", 
        options=list(range(1, cycle_count + 1)),
        format_func=lambda x: f"Zyklus {x} (Start: {df_final[df_final['Cycle_ID'] == x].index[0].strftime('%d.%m.%Y %H:%M')})"
    )
    
    inspect_df = df_final[df_final['Cycle_ID'] == selected_inspect_cycle].copy()
    if not inspect_df.empty:
        global_indices = [df.index.get_loc(idx) for idx in inspect_df.index]
        inspect_display = pd.DataFrame({
            "Kerzen-Position": inspect_df['Position'].values,
            "Datum / Uhrzeit (UTC)": inspect_df.index.strftime("%d.%m.%Y %H:%M"),
            "Globaler Kerzen-Index": global_indices,
            "Trendwende erkannt?": inspect_df['has_turn'].apply(lambda x: "✅ JA" if x else "—")
        })
        st.dataframe(inspect_display, use_container_width=True, hide_index=True)

with tab4:
    st.markdown("##### 🎯 Optimale Zykluslängen je historischem Trendwendepunkt")
    st.caption(
        f"Prüft für jeden aufgetretenen Trendwendepunkt ab dem Startdatum über **{scanner_cycle_count} Zyklen** "
        f"und alle Phasen-Offsets, bei welchen Zykluslängen (**N = {scan_min_n} bis {scan_max_n}**) dieser Punkt die höchste Wiederholungsrate erzielt."
    )
    
    if scan_min_n > scan_max_n:
        st.warning("⚠️ 'Min. Zyklus (N)' darf nicht größer als 'Max. Zyklus (N)' sein. Bitte in der Sidebar anpassen.")
    else:
        with st.spinner("Berechne beste Zykluslängen für alle Trendwendepunkte..."):
            exact_scan_df, tol_scan_df = evaluate_turning_points(
                df_sub=df_sub, 
                min_n=scan_min_n, 
                max_n=scan_max_n, 
                c_count=scanner_cycle_count
            )
            
        if exact_scan_df.empty and tol_scan_df.empty:
            st.warning(
                "Für die gewählte Kombination aus Startdatum, Zyklusanzahl und Zykluslängen konnten keine Punkte ausgewertet werden. "
                "Tipp: Wähle ein früheres Startdatum oder reduziere die 'Anzahl Zyklen zum Vergleichen'."
            )
        else:
            st.markdown("---")
            col_f1, col_f2 = st.columns(2)
            with col_f1:
                min_filter_pct = st.slider("Mindest-Trefferquote filtern (%)", min_value=0, max_value=100, value=0, step=5)
            
            filtered_exact_df = exact_scan_df[exact_scan_df["_raw_pct"] >= min_filter_pct].sort_values(by="_raw_pct", ascending=False)
            filtered_tol_df = tol_scan_df[tol_scan_df["_raw_pct"] >= min_filter_pct].sort_values(by="_raw_pct", ascending=False)
            
            col_t1, col_t2 = st.columns(2)
            
            with col_t1:
                st.markdown("###### 1. Beste Zyklen je Punkt — **Exakt**")
                display_exact = filtered_exact_df.drop(columns=["_raw_N", "_raw_offset", "_raw_pct"], errors='ignore')
                st.dataframe(
                    display_exact,
                    column_config={
                        "Kerzen-Index": st.column_config.NumberColumn("Kerzen-Idx", format="%d"),
                        "Max. Trefferquote (Exakt)": st.column_config.ProgressColumn(
                            "Max. Quote (Exakt)", format="%.1f%%", min_value=0, max_value=100
                        )
                    },
                    use_container_width=True,
                    hide_index=True
                )
                
            with col_t2:
                st.markdown("###### 2. Beste Zyklen je Punkt — **Mit ±1 Toleranz**")
                display_tol = filtered_tol_df.drop(columns=["_raw_N", "_raw_offset", "_raw_pct"], errors='ignore')
                st.dataframe(
                    display_tol,
                    column_config={
                        "Kerzen-Index": st.column_config.NumberColumn("Kerzen-Idx", format="%d"),
                        "Max. Trefferquote (±1 Toleranz)": st.column_config.ProgressColumn(
                            "Max. Quote (±1 Toleranz)", format="%.1f%%", min_value=0, max_value=100
                        )
                    },
                    use_container_width=True,
                    hide_index=True
                )
                
            st.markdown("---")
            st.markdown("##### 🚀 Zyklus in Hauptansicht übernehmen")
            st.caption("Wähle einen Punkt aus, um dessen optimale Zykluslänge und den Start-Offset direkt auf das Haupt-Dashboard zu übertragen:")
            
            combined_options = []
            source_records = tol_scan_df if not tol_scan_df.empty else exact_scan_df
            
            for _, row in source_records.iterrows():
                label = f"{row['Punkt']} ({row['Zeitpunkt']}) — N = {row['_raw_N']} (Offset: +{row['_raw_offset']}) — Quote: {row['_raw_pct']}%"
                combined_options.append({
                    "label": label,
                    "N": int(row['_raw_N']),
                    "offset": int(row['_raw_offset'])
                })
                
            if combined_options:
                selected_choice = st.selectbox(
                    "Punkt / Zyklus zur Übernahme auswählen", 
                    options=[opt["label"] for opt in combined_options]
                )
                
                if st.button("✨ Gewählten Zyklus & Offset in Hauptansicht anwenden", use_container_width=True):
                    chosen_item = next(opt for opt in combined_options if opt["label"] == selected_choice)
                    st.session_state['override_cycle_length'] = chosen_item["N"]
                    st.session_state['override_offset'] = chosen_item["offset"]
                    st.session_state['override_auto_align'] = False
                    st.success(f"Erfolgreich übernommen! Zykluslänge N = {chosen_item['N']}, Offset = +{chosen_item['offset']}. Aktualisiere...")
                    st.rerun()
