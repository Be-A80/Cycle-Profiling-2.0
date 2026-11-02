import math
import streamlit as st
import pandas as pd
import numpy as np
import ccxt
import plotly.express as px
from datetime import datetime, date, time as dt_time

# ----------------- Seiten-Konfiguration -----------------
st.set_page_config(page_title="PZP Cycle Profiler (Toleranz)", page_icon="🎯", layout="wide")

st.title("🎯 PZP Zyklus-Profiler mit ±1-Kerzen-Toleranz")
st.caption(
    "Ausgewertet werden die vom PZP-Algorithmus projizierten Wendepunkte (**PZP-Signale**), nicht die Pivots selbst. "
    "Optional mit ±1-Kerzen-Toleranz (Cluster-Glättung)."
)

# Unterhalb dieser Zykluslänge deckt das ±1-Fenster (3 Kerzen) einen zu großen Teil des Zyklus ab
MIN_N_TOLERANCE = 5
N_FUTURE_PROJECTIONS = 10
DT_FMT = "%d.%m.%Y %H:%M"

# Session State Initialisierung für die Übernahme in die Hauptansicht
for key in ('override_cycle_length', 'override_offset', 'override_auto_align', 'flash_message'):
    if key not in st.session_state:
        st.session_state[key] = None

if st.session_state['flash_message']:
    st.success(st.session_state['flash_message'])
    st.session_state['flash_message'] = None


# US-kompatible Datenquelle (Kraken)
@st.cache_resource
def get_exchange():
    return ccxt.kraken({
        'enableRateLimit': True,
    })


# Timeframe-Konfigurationen (Kraken liefert maximal 720 Kerzen je Abruf)
TF_CONFIG = {
    "15m": {"fetch_tf": "15m", "resample": None, "limit": 720, "delta": pd.Timedelta(minutes=15), "fetch_delta": pd.Timedelta(minutes=15)},
    "30m": {"fetch_tf": "30m", "resample": None, "limit": 720, "delta": pd.Timedelta(minutes=30), "fetch_delta": pd.Timedelta(minutes=30)},
    "1h":  {"fetch_tf": "1h",  "resample": None, "limit": 720, "delta": pd.Timedelta(hours=1),    "fetch_delta": pd.Timedelta(hours=1)},
    "2h":  {"fetch_tf": "1h",  "resample": "2h", "limit": 720, "delta": pd.Timedelta(hours=2),    "fetch_delta": pd.Timedelta(hours=1)},
    "4h":  {"fetch_tf": "4h",  "resample": None, "limit": 720, "delta": pd.Timedelta(hours=4),    "fetch_delta": pd.Timedelta(hours=4)},
    "1d":  {"fetch_tf": "1d",  "resample": None, "limit": 720, "delta": pd.Timedelta(days=1),     "fetch_delta": pd.Timedelta(days=1)},
    "2d":  {"fetch_tf": "1d",  "resample": "2D", "limit": 720, "delta": pd.Timedelta(days=2),     "fetch_delta": pd.Timedelta(days=1)},
    "3d":  {"fetch_tf": "1d",  "resample": "3D", "limit": 720, "delta": pd.Timedelta(days=3),     "fetch_delta": pd.Timedelta(days=1)},
    "4d":  {"fetch_tf": "1d",  "resample": "4D", "limit": 720, "delta": pd.Timedelta(days=4),     "fetch_delta": pd.Timedelta(days=1)},
    "1w":  {"fetch_tf": "1w",  "resample": None, "limit": 500, "delta": pd.Timedelta(weeks=1),    "fetch_delta": pd.Timedelta(weeks=1)},
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
    cycle_length = int(st.number_input("Zykluslänge N (in Kerzen)", min_value=3, max_value=120, value=int(default_cycle_len), step=1))
    cycle_count = int(st.number_input("Anzahl Zyklen zum Vergleichen", min_value=2, max_value=40, value=10, step=1))

    st.markdown("##### 🔍 Zyklus-Scanner (Punkt-Analyse)")
    col_s1, col_s2 = st.columns(2)
    with col_s1:
        scan_min_n = int(st.number_input("Min. Zyklus (N)", min_value=3, max_value=120, value=3, step=1))
    with col_s2:
        scan_max_n = int(st.number_input("Max. Zyklus (N)", min_value=3, max_value=120, value=30, step=1))

    st.markdown("##### ⏱️ Toleranz-Filter")
    use_tolerance = st.checkbox(
        "±1-Kerze Toleranz einbeziehen", value=True,
        help=f"Zählt einen Zyklus als Treffer, wenn das Signal eine Kerze vor, auf oder nach der Position lag. "
             f"Nur für Zykluslängen N ≥ {MIN_N_TOLERANCE} aussagekräftig."
    )

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
        manual_offset = st.slider("Manueller Start-Offset (Kerzen)", 0, cycle_length - 1, int(min(default_off, cycle_length - 1)))

    st.markdown("---")
    st.subheader("📐 PZP-Algorithmus Parameter")
    pL = int(st.number_input("Pivot Links (L)", min_value=1, value=1))
    pR = int(st.number_input("Pivot Rechts (R)", min_value=1, value=1))
    factor = float(st.number_input("Projektionsfaktor", min_value=0.05, value=0.5, step=0.05))

    if st.button("🔄 Neu berechnen", width="stretch"):
        st.session_state['override_cycle_length'] = None
        st.session_state['override_offset'] = None
        st.session_state['override_auto_align'] = None
        st.cache_data.clear()
        st.rerun()


# ----------------- Daten abrufen -----------------
@st.cache_data(ttl=300, show_spinner=False)
def fetch_candles(coin, tf_choice):
    """Lädt abgeschlossene Kerzen. Fehler werden als Exception weitergegeben (und damit nicht gecacht)."""
    cfg = TF_CONFIG[tf_choice]
    symbol = f"{coin}/USD"
    ohlcv = get_exchange().fetch_ohlcv(symbol, timeframe=cfg["fetch_tf"], limit=cfg["limit"])
    if not ohlcv or len(ohlcv) < 30:
        return None

    df = pd.DataFrame(ohlcv, columns=['timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    df = df.set_index('timestamp').sort_index()
    df = df[~df.index.duplicated(keep='last')]

    if cfg["resample"]:
        # origin="epoch" hält die Kerzengrenzen stabil, unabhängig davon, wo das 720er-Datenfenster gerade beginnt
        resampler = df.resample(cfg["resample"], origin="epoch")
        agg = resampler.agg({
            'Open': 'first',
            'High': 'max',
            'Low': 'min',
            'Close': 'last',
            'Volume': 'sum'
        })
        # Nur vollständige Kerzen behalten (unvollständiger erster/letzter Block fällt weg)
        expected = int(cfg["delta"] / cfg["fetch_delta"])
        counts = resampler['Close'].count()
        df = agg[counts >= expected].dropna()

    # Laufende (noch nicht abgeschlossene) Kerze entfernen
    now = pd.Timestamp.now(tz="UTC")
    df = df[df.index + cfg["delta"] <= now]

    return df


# ----------------- PZP Trendwechsel-Erkennung -----------------
def find_pivots(values, L, R, is_high):
    n = len(values)
    if n <= L + R:
        return np.array([], dtype=int)
    idx = np.arange(L, n - R)
    center = values[idx]
    mask = np.ones(len(idx), dtype=bool)
    for k in range(1, L + 1):
        mask &= (center > values[idx - k]) if is_high else (center < values[idx - k])
    for k in range(1, R + 1):
        mask &= (center > values[idx + k]) if is_high else (center < values[idx + k])
    return idx[mask]


def project_signals(pivots, n, R, f, out):
    # Projektion ab dem Pivot; +R, weil der Pivot erst R Kerzen später bestätigt ist
    for prev, cur in zip(pivots[:-1], pivots[1:]):
        target_bar = int(cur + np.ceil(f * (cur - prev)) + R)
        if target_bar < n:
            out[target_bar] = True


def identify_trend_changes(df, L, R, f):
    df = df.copy()
    n = len(df)
    signals = np.zeros(n, dtype=bool)
    project_signals(find_pivots(df['High'].values, L, R, True), n, R, f, signals)
    project_signals(find_pivots(df['Low'].values, L, R, False), n, R, f, signals)

    # ±1-Toleranz über den echten Kerzen-Index (auch über Zyklusgrenzen hinweg korrekt)
    signals_tol = signals.copy()
    signals_tol[1:] |= signals[:-1]
    signals_tol[:-1] |= signals[1:]

    df['has_turn'] = signals
    df['has_turn_tol'] = signals_tol
    return df


# ----------------- Statistik-Hilfsfunktionen -----------------
def binom_sf(k, n, p):
    """P(X >= k) für X ~ Bin(n, p)."""
    if k <= 0:
        return 1.0
    if p <= 0:
        return 0.0
    if p >= 1:
        return 1.0
    return min(1.0, sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k, n + 1)))


def comparison_window(t_idx, N, last_idx, c_count):
    """Bereich k_lo..k_hi (inkl. k=0 = Punkt selbst) für c_count Vergleichszyklen an t_idx + k*N.
    Bevorzugt Zyklen nach dem Punkt; reichen die Daten nicht, wird nach hinten aufgefüllt."""
    k_min = -(t_idx // N)
    k_max = (last_idx - t_idx) // N
    if k_max - k_min < c_count:
        return None
    k_hi = min(k_max, c_count)
    k_lo = k_hi - c_count
    if k_lo < k_min:
        k_lo = k_min
        k_hi = k_lo + c_count
    return k_lo, k_hi


def project_points(t_idx, N, hit_arr, timestamps, delta, count):
    """Die nächsten `count` Wiederholungen des Punkts (t_idx + k*N).
    Vergangene Termine mit ✅/❌, Termine in der Zukunft ohne Symbol."""
    last_idx = len(timestamps) - 1
    out = []
    for k in range(1, count + 1):
        j = t_idx + k * N
        if j <= last_idx:
            out.append(f"{timestamps[j].strftime(DT_FMT)} {'✅' if hit_arr[j] else '❌'}")
        else:
            out.append((timestamps[last_idx] + (j - last_idx) * delta).strftime(DT_FMT))
    return out


# ----------------- Punkt-Zyklus-Scanner -----------------
def evaluate_turning_points(df_sub, min_n, max_n, c_count, delta):
    signals = df_sub['has_turn'].values
    signals_tol = df_sub['has_turn_tol'].values
    timestamps = df_sub.index
    last_idx = len(df_sub) - 1

    n_values_exact = list(range(int(min_n), int(max_n) + 1))
    n_values_tol = [N for N in n_values_exact if N >= MIN_N_TOLERANCE]

    modes = [
        ("Max. Trefferquote (Exakt)", signals, n_values_exact, float(signals.mean())),
        ("Max. Trefferquote (±1 Toleranz)", signals_tol, n_values_tol, float(signals_tol.mean())),
    ]
    results = {name: [] for name, *_ in modes}

    for pt_num, t_idx in enumerate(np.where(signals)[0], start=1):
        dt_str = timestamps[t_idx].strftime(DT_FMT)

        for quote_col, hit_arr, n_values, base_p, in modes:
            best = None
            tie_details = []
            for N in n_values:
                window = comparison_window(t_idx, N, last_idx, c_count)
                if window is None:
                    continue
                k_lo, k_hi = window
                # Der Punkt selbst (k = 0) zählt nicht als Treffer
                hits = int(sum(hit_arr[t_idx + k * N] for k in range(k_lo, k_hi + 1) if k != 0))
                pct = hits / c_count * 100.0
                # Zyklusgrenzen so legen, dass der Punkt in der Zyklusmitte liegt
                offset = (t_idx - N // 2) % N
                pos = (t_idx - offset) % N
                detail = f"N={N} (Pos {pos}, Offset +{offset})"

                if best is None or pct > best["pct"] and not np.isclose(pct, best["pct"]):
                    best = {"N": N, "offset": offset, "pct": pct, "hits": hits, "k_lo": k_lo, "k_hi": k_hi}
                    tie_details = [detail]
                elif np.isclose(pct, best["pct"]):
                    tie_details.append(detail)

            if best is None:
                continue

            N = best["N"]
            # Bonferroni-Korrektur, da je Punkt mehrere N getestet und das beste gewählt wird
            p_value = min(1.0, binom_sf(best["hits"], c_count, base_p) * len(n_values))
            lift = (best["pct"] / (base_p * 100.0)) if base_p > 0 else np.nan
            win_start = timestamps[t_idx + best["k_lo"] * N].strftime("%d.%m.%y")
            win_end = timestamps[t_idx + best["k_hi"] * N].strftime("%d.%m.%y")

            record = {
                "Punkt": f"Punkt {pt_num}",
                "Zeitpunkt": dt_str,
                "Kerzen-Index": int(t_idx),
                quote_col: round(best["pct"], 1),
                "Treffer (Zyklen)": f"{best['hits']} / {c_count}",
                "Zufalls-Basis (%)": round(base_p * 100.0, 1),
                "Lift": round(lift, 2) if not np.isnan(lift) else None,
                "p-Wert (korr.)": round(p_value, 4),
                "Beste Zyklen, Positionen & Offsets": "; ".join(tie_details),
                "Vergleichsfenster": f"{win_start} – {win_end}",
            }
            for i, proj in enumerate(project_points(t_idx, N, hit_arr, timestamps, delta, N_FUTURE_PROJECTIONS), start=1):
                record[f"Proj. {i}"] = proj
            record.update({"_raw_N": N, "_raw_offset": best["offset"], "_raw_pct": best["pct"], "_raw_p": p_value})
            results[quote_col].append(record)

    return (pd.DataFrame(results["Max. Trefferquote (Exakt)"]),
            pd.DataFrame(results["Max. Trefferquote (±1 Toleranz)"]))


# ----------------- Hauptausführung -----------------
with st.spinner(f"Lade Kursdaten für {selected_coin}..."):
    try:
        df_raw = fetch_candles(selected_coin, tf_choice)
    except ccxt.BaseError as e:
        st.error(f"Fehler beim Abruf von Kraken: {e}")
        st.stop()

if df_raw is None or len(df_raw) < 50:
    st.error("Fehler beim Laden der Kursdaten (zu wenige Kerzen).")
    st.stop()

df = identify_trend_changes(df_raw, pL, pR, factor)

start_dt = pd.Timestamp(datetime.combine(start_date_input, start_time_input)).tz_localize('UTC')
df_sub = df[df.index >= start_dt].copy()

if start_dt < df.index[0]:
    st.info(
        f"ℹ️ Kraken liefert für **{tf_choice}** maximal {TF_CONFIG[tf_choice]['limit']} Basis-Kerzen. "
        f"Die Daten beginnen erst am **{df.index[0].strftime(DT_FMT)}** – das gewählte Startdatum liegt davor und wirkt sich nicht aus."
    )

if len(df_sub) < (cycle_count * cycle_length + cycle_length):
    available_cycles = (len(df_sub) - cycle_length) // cycle_length
    if available_cycles < 2:
        st.error("Zu wenige Kerzen ab Startzeitpunkt vorhanden. Bitte wähle ein früheres Startdatum.")
        st.stop()
    st.warning(f"⚠️ Nicht genug Daten für {cycle_count} Zyklen – es werden {available_cycles} Zyklen verglichen.")
    cycle_count = available_cycles

# Scanner nutzt dieselbe (ggf. reduzierte) Zyklusanzahl wie die Hauptansicht
scanner_cycle_count = cycle_count

tolerance_possible = cycle_length >= MIN_N_TOLERANCE
if use_tolerance and not tolerance_possible:
    st.warning(
        f"⚠️ Bei N = {cycle_length} deckt das ±1-Fenster {3}/{cycle_length} des Zyklus ab – die Toleranz wäre nicht aussagekräftig "
        f"und wird erst ab N ≥ {MIN_N_TOLERANCE} verwendet. Es wird exakt ausgewertet."
    )
use_tol_eff = use_tolerance and tolerance_possible

exact_arr = df_sub['has_turn'].values
tol_arr = df_sub['has_turn_tol'].values
metric_arr = tol_arr if use_tol_eff else exact_arr
window_len = cycle_count * cycle_length

# ----------------- Phasen-Optimierung -----------------
phase_eval = []
for offset in range(cycle_length):
    mat = metric_arr[offset: offset + window_len].reshape(cycle_count, cycle_length)
    rates = mat.mean(axis=0) * 100.0
    best_pos = int(np.argmax(rates))
    phase_eval.append({
        "Offset (Kerzen)": offset,
        "Effektiver Start": df_sub.index[offset].strftime("%d.%m.%y %H:%M"),
        "Spitzen-Trefferquote (%)": round(float(rates[best_pos]), 1),
        "Beste Position": best_pos,
        "Score": float((rates ** 2).sum())
    })

phase_df = pd.DataFrame(phase_eval)

if auto_align:
    best_phase_row = phase_df.sort_values(by=["Spitzen-Trefferquote (%)", "Score"], ascending=False).iloc[0]
    chosen_offset = int(best_phase_row["Offset (Kerzen)"])
else:
    chosen_offset = int(manual_offset)
    best_phase_row = phase_df[phase_df["Offset (Kerzen)"] == chosen_offset].iloc[0]

# ----------------- Finale Analyse mit gewähltem Offset -----------------
df_final = df_sub.iloc[chosen_offset: chosen_offset + window_len].copy()
df_final['Position'] = np.arange(len(df_final)) % cycle_length
df_final['Cycle_ID'] = np.arange(len(df_final)) // cycle_length + 1

exact_mat = df_final['has_turn'].values.reshape(cycle_count, cycle_length)
tol_mat = df_final['has_turn_tol'].values.reshape(cycle_count, cycle_length)
exact_cycles = exact_mat.sum(axis=0)
tol_cycles = tol_mat.sum(axis=0)

position_stats = []
for pos in range(cycle_length):
    position_stats.append({
        "Position": pos,
        "Exakt (%)": round(exact_cycles[pos] / cycle_count * 100, 1),
        "Mit ±1 Toleranz (%)": round(tol_cycles[pos] / cycle_count * 100, 1) if tolerance_possible else np.nan,
        "Treffer Zyklen (Exakt)": f"{int(exact_cycles[pos])} / {cycle_count}",
        "Treffer Zyklen (Toleranz)": f"{int(tol_cycles[pos])} / {cycle_count}" if tolerance_possible else "—",
    })

stats_df = pd.DataFrame(position_stats)
display_metric = "Mit ±1 Toleranz (%)" if use_tol_eff else "Exakt (%)"

# ----------------- UI Dashboard -----------------
if auto_align:
    st.success(
        f"🎯 **Bester Startpunkt gefunden:** Versatz um **+{chosen_offset} Kerzen** erzielt "
        f"**{best_phase_row['Spitzen-Trefferquote (%)']}% Treffer** auf Position **{int(best_phase_row['Beste Position'])}** "
        f"({'inkl. ±1 Toleranz' if use_tol_eff else 'exakt'})!"
    )

base_rate = float(metric_arr.mean() * 100.0)
st.caption(
    f"Zufalls-Basis: {base_rate:.1f}% aller Kerzen ab Start tragen ein Signal ({'±1' if use_tol_eff else 'exakt'}). "
    f"Quoten in dieser Größenordnung sind auch ohne echten Zyklus zu erwarten."
)

col1, col2, col3, col4 = st.columns(4)
col1.metric("Gewählter Offset", f"+{chosen_offset} Kerzen")
col2.metric("Effektiver Zyklus 1 Start", f"{df_final.index[0].strftime('%d.%m.%y %H:%M')}")
col3.metric("Zykluslänge", f"{cycle_length} Kerzen ({tf_choice})")
top_res = stats_df.sort_values(by=display_metric, ascending=False).iloc[0]
col4.metric(f"Top-Cluster ({display_metric})", f"Pos {int(top_res['Position'])}", f"{top_res[display_metric]}% Treffer")

st.markdown("---")

st.subheader("📊 Wahrscheinlichkeitsverteilung über den Zyklus")
value_vars = ["Exakt (%)", "Mit ±1 Toleranz (%)"] if tolerance_possible else ["Exakt (%)"]
plot_df = stats_df.melt(id_vars=["Position"], value_vars=value_vars, var_name="Methode", value_name="Quote")

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
fig.add_hline(y=base_rate, line_dash="dot", line_color="#f59e0b", annotation_text="Zufalls-Basis")
fig.update_layout(
    yaxis=dict(range=[0, 115]),
    xaxis=dict(tickmode='linear', dtick=1),
    template="plotly_dark",
    height=450
)
st.plotly_chart(fig)

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
            width="stretch",
            hide_index=True
        )
    with c2:
        st.markdown("##### Treffer-Matrix je Zyklus (Exakte Lage)")
        piv = pd.DataFrame(
            np.where(exact_mat, "✅", "—"),
            index=pd.Index(range(1, cycle_count + 1), name="Zyklus #"),
            columns=range(cycle_length)
        )
        st.dataframe(piv, width="stretch")

with tab2:
    st.markdown("##### Vergleich aller getesteten Start-Offsets")
    st.caption("Zeigt die maximale Trefferquote für jeden möglichen Startversatz:")
    st.dataframe(
        phase_df.drop(columns=["Score"]),
        column_config={
            "Spitzen-Trefferquote (%)": st.column_config.ProgressColumn("Spitzenquote", format="%.1f%%", min_value=0, max_value=100),
            "Offset (Kerzen)": st.column_config.NumberColumn("Verschiebung", format="%d")
        },
        width="stretch",
        hide_index=True
    )

with tab3:
    st.markdown("##### 📅 Start- und Endzeitpunkte sowie PZP-Signale der verglichenen Zyklen")
    cycle_times = []
    for cid in range(1, cycle_count + 1):
        c_sub = df_final[df_final['Cycle_ID'] == cid]
        if not c_sub.empty:
            signals_in_cycle = c_sub[c_sub['has_turn']].index.strftime(DT_FMT).tolist()
            cycle_times.append({
                "Zyklus #": cid,
                "Start": c_sub.index[0].strftime(DT_FMT),
                "Ende": c_sub.index[-1].strftime(DT_FMT),
                "Anzahl Kerzen": len(c_sub),
                "PZP-Signale im Zyklus": ", ".join(signals_in_cycle) if signals_in_cycle else "—"
            })
    st.dataframe(pd.DataFrame(cycle_times), width="stretch", hide_index=True)

    st.markdown("---")
    st.markdown("##### 🔍 Kerzen-Inspektor je Zyklus (Positionen & PZP-Signale)")
    st.caption("Wähle einen spezifischen Zyklus aus, um die exakte Position (0 bis N-1) jeder einzelnen Kerze einzusehen:")

    selected_inspect_cycle = st.selectbox(
        "Zyklus für Detail-Inspektion auswählen",
        options=list(range(1, cycle_count + 1)),
        format_func=lambda x: f"Zyklus {x} (Start: {df_final[df_final['Cycle_ID'] == x].index[0].strftime(DT_FMT)})"
    )

    inspect_df = df_final[df_final['Cycle_ID'] == selected_inspect_cycle]
    if not inspect_df.empty:
        inspect_display = pd.DataFrame({
            "Kerzen-Position": inspect_df['Position'].values,
            "Datum / Uhrzeit (UTC)": inspect_df.index.strftime(DT_FMT),
            "Globaler Kerzen-Index": df.index.get_indexer(inspect_df.index),
            "PZP-Signal?": np.where(inspect_df['has_turn'].values, "✅ JA", "—"),
            "Signal im ±1-Fenster?": np.where(inspect_df['has_turn_tol'].values, "✅", "—"),
        })
        st.dataframe(inspect_display, width="stretch", hide_index=True)

with tab4:
    st.markdown("##### 🎯 Optimale Zykluslängen je PZP-Signal")
    st.caption(
        f"Prüft für jedes PZP-Signal ab dem Startdatum, bei welcher Zykluslänge (**N = {scan_min_n} bis {scan_max_n}**) "
        f"es sich in **{scanner_cycle_count} Vergleichszyklen** am häufigsten wiederholt. Der Punkt selbst zählt nicht als Treffer. "
        f"Verglichen werden bevorzugt die Zyklen *nach* dem Punkt; reichen die Daten nicht, werden frühere Zyklen genutzt. "
        f"Toleranz-Auswertung nur für N ≥ {MIN_N_TOLERANCE}."
    )
    st.caption(
        "**Zufalls-Basis** = erwartete Quote ohne Zyklus · **Lift** = Quote / Basis · "
        "**p-Wert (korr.)** = Wahrscheinlichkeit, diese Quote zufällig zu erreichen (Bonferroni-korrigiert über alle getesteten N; < 0,05 = auffällig). "
        f"**Proj. 1–{N_FUTURE_PROJECTIONS}** = nächste Wiederholungen ab dem Punkt (erstes bestes N): ✅ eingetroffen, ❌ nicht eingetroffen, ohne Symbol = Zukunft."
    )

    if scan_min_n > scan_max_n:
        st.warning("⚠️ 'Min. Zyklus (N)' darf nicht größer als 'Max. Zyklus (N)' sein. Bitte in der Sidebar anpassen.")
    else:
        with st.spinner("Berechne beste Zykluslängen für alle PZP-Signale..."):
            exact_scan_df, tol_scan_df = evaluate_turning_points(
                df_sub=df_sub,
                min_n=scan_min_n,
                max_n=scan_max_n,
                c_count=scanner_cycle_count,
                delta=TF_CONFIG[tf_choice]["delta"]
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
            with col_f2:
                only_significant = st.checkbox("Nur auffällige Punkte (p-Wert < 0,05)", value=False)

            raw_cols = ["_raw_N", "_raw_offset", "_raw_pct", "_raw_p"]
            proj_config = {
                f"Proj. {i}": st.column_config.TextColumn(f"Proj. {i}") for i in range(1, N_FUTURE_PROJECTIONS + 1)
            }

            def filter_scan(scan_df):
                if scan_df.empty:
                    return scan_df
                mask = scan_df["_raw_pct"] >= min_filter_pct
                if only_significant:
                    mask &= scan_df["_raw_p"] < 0.05
                return scan_df[mask].sort_values(by=["_raw_pct", "_raw_p"], ascending=[False, True])

            def show_scan_table(scan_df, quote_col, quote_label):
                if scan_df.empty:
                    st.info("Keine Punkte für diese Auswertung.")
                    return
                st.dataframe(
                    scan_df.drop(columns=raw_cols, errors='ignore'),
                    column_config={
                        "Kerzen-Index": st.column_config.NumberColumn("Kerzen-Idx", format="%d"),
                        quote_col: st.column_config.ProgressColumn(quote_label, format="%.1f%%", min_value=0, max_value=100),
                        "Zufalls-Basis (%)": st.column_config.NumberColumn("Zufalls-Basis", format="%.1f%%"),
                        "Lift": st.column_config.NumberColumn("Lift", format="%.2f×"),
                        "p-Wert (korr.)": st.column_config.NumberColumn("p-Wert (korr.)", format="%.4f"),
                        **proj_config,
                    },
                    width="stretch",
                    hide_index=True
                )

            st.markdown("###### 1. Beste Zyklen je Punkt — **Exakt**")
            show_scan_table(filter_scan(exact_scan_df), "Max. Trefferquote (Exakt)", "Max. Quote (Exakt)")

            st.markdown("###### 2. Beste Zyklen je Punkt — **Mit ±1 Toleranz**")
            show_scan_table(filter_scan(tol_scan_df), "Max. Trefferquote (±1 Toleranz)", "Max. Quote (±1 Toleranz)")

            st.markdown("---")
            st.markdown("##### 🚀 Zyklus in Hauptansicht übernehmen")
            st.caption(
                "Wähle einen Punkt aus, um dessen optimale Zykluslänge und den Start-Offset direkt auf das Haupt-Dashboard zu übertragen. "
                "Hinweis: Die Hauptansicht wertet die ersten Zyklen ab Startdatum aus, der Scanner das 'Vergleichsfenster' – die Quoten können daher abweichen."
            )

            use_tol_source = use_tolerance and not tol_scan_df.empty
            source_records = tol_scan_df if use_tol_source else exact_scan_df
            st.caption(f"Quelle: Tabelle {'2 (±1 Toleranz)' if use_tol_source else '1 (Exakt)'} – entsprechend der Toleranz-Einstellung in der Sidebar.")

            combined_options = []
            for _, row in source_records.iterrows():
                label = (
                    f"{row['Punkt']} ({row['Zeitpunkt']}) — N = {int(row['_raw_N'])} (Offset: +{int(row['_raw_offset'])}) — "
                    f"Quote: {row['_raw_pct']:.1f}% — p = {row['_raw_p']:.3f}"
                )
                combined_options.append({"label": label, "N": int(row['_raw_N']), "offset": int(row['_raw_offset'])})

            if combined_options:
                selected_idx = st.selectbox(
                    "Punkt / Zyklus zur Übernahme auswählen",
                    options=list(range(len(combined_options))),
                    format_func=lambda i: combined_options[i]["label"]
                )

                if st.button("✨ Gewählten Zyklus & Offset in Hauptansicht anwenden", width="stretch"):
                    chosen_item = combined_options[selected_idx]
                    st.session_state['override_cycle_length'] = chosen_item["N"]
                    st.session_state['override_offset'] = chosen_item["offset"]
                    st.session_state['override_auto_align'] = False
                    st.session_state['flash_message'] = (
                        f"Erfolgreich übernommen! Zykluslänge N = {chosen_item['N']}, Offset = +{chosen_item['offset']}."
                    )
                    st.rerun()
