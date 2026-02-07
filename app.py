import io
import streamlit as st
import pandas as pd
from scoring import score_candidates

st.set_page_config(page_title="HR Candidate Scoring", layout="wide")
st.title("HR Candidate Scoring (Dynamic Excel)")
st.caption("Herhangi bir Excel yükle → kolonları seç → ağırlık/method belirle → skorla → sonucu indir")

METHODS = ["numeric_minmax", "numeric_0_100", "numeric_0_1", "categorical_mapping"]
DIRECTIONS = ["higher_better", "lower_better"]

uploaded = st.file_uploader("Excel dosyanı yükle (.xlsx)", type=["xlsx"])

if uploaded is None:
    st.info("Devam etmek için bir Excel dosyası yükle.")
    st.stop()

file_bytes = uploaded.getvalue()
xls = pd.ExcelFile(io.BytesIO(file_bytes), engine="openpyxl")

# Sheet seçimi
sheet_name = st.selectbox("Hangi sheet okunacak?", xls.sheet_names, index=0)
df = pd.read_excel(xls, sheet_name=sheet_name)

st.subheader("Aday Datası Önizleme")
st.dataframe(df.head(15), use_container_width=True)

# Kolon seçimi
all_cols = list(df.columns)
default_selected = all_cols[: min(8, len(all_cols))]

selected_cols = st.multiselect(
    "Skorlamaya dahil edilecek kolonları seç",
    options=all_cols,
    default=default_selected
)

if not selected_cols:
    st.warning("En az 1 kolon seçmelisin.")
    st.stop()

# Basit tip tahmini (numeric mi categorical mı)
def guess_method(series: pd.Series) -> str:
    # numerikse numeric_minmax, değilse categorical
    s = pd.to_numeric(series, errors="coerce")
    numeric_ratio = s.notna().mean()
    # çoğu numeric parse oluyorsa numeric kabul et
    if numeric_ratio >= 0.8:
        # değerler 0-1 mi?
        if s.dropna().between(0, 1).mean() >= 0.95:
            return "numeric_0_1"
        # değerler 0-100 mü?
        if s.dropna().between(0, 100).mean() >= 0.95:
            return "numeric_0_100"
        return "numeric_minmax"
    return "categorical_mapping"

def make_default_weights(selected_cols):
    rows = []
    for c in selected_cols:
        method = guess_method(df[c])
        rows.append({
            "Criterion": c,
            "Weight": round(1.0 / len(selected_cols), 4),
            "Method": method,
            "Direction": "higher_better"
        })
    return pd.DataFrame(rows)

# Session state: kullanıcı düzenleyince kaybolmasın
if "weights_df" not in st.session_state or st.session_state.get("weights_for_cols") != tuple(selected_cols):
    st.session_state["weights_df"] = make_default_weights(selected_cols)
    st.session_state["weights_for_cols"] = tuple(selected_cols)

weights_df = st.session_state["weights_df"]

st.subheader("1) Ağırlık / Method / Direction ayarları")
st.caption("Weight toplamı otomatik normalize edilir. Direction: lower_better seçersen ters çevrilir.")
edited_weights = st.data_editor(
    weights_df,
    use_container_width=True,
    num_rows="dynamic",
    column_config={
        "Method": st.column_config.SelectboxColumn("Method", options=METHODS),
        "Direction": st.column_config.SelectboxColumn("Direction", options=DIRECTIONS),
    },
    key="weights_editor_dynamic"
)

# Mapping tablosunu otomatik üret
def build_mappings_from_weights(df: pd.DataFrame, weights_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, w in weights_df.iterrows():
        crit = w["Criterion"]
        method = w["Method"]
        if crit not in df.columns:
            continue
        if method == "categorical_mapping":
            cats = df[crit].dropna().astype(str).str.strip().unique().tolist()
            cats = sorted(cats)
            for cat in cats:
                rows.append({
                    "Criterion": crit,
                    "Category": cat,
                    "Score_0_100": 60.0
                })
    return pd.DataFrame(rows)

# Mevcut mapping state’i koru: (Criterion, Category) eşleşiyorsa eski skorları sakla
def merge_keep_existing_scores(old_map: pd.DataFrame, new_map: pd.DataFrame) -> pd.DataFrame:
    if old_map is None or old_map.empty:
        return new_map
    old_key = old_map.copy()
    old_key["__k"] = old_key["Criterion"].astype(str) + "||" + old_key["Category"].astype(str)
    new_key = new_map.copy()
    new_key["__k"] = new_key["Criterion"].astype(str) + "||" + new_key["Category"].astype(str)

    old_scores = dict(zip(old_key["__k"], old_key["Score_0_100"]))
    new_key["Score_0_100"] = new_key["__k"].map(lambda k: old_scores.get(k, 60.0))
    return new_key.drop(columns=["__k"])

new_map = build_mappings_from_weights(df, edited_weights)

if "mappings_df" not in st.session_state:
    st.session_state["mappings_df"] = new_map
else:
    st.session_state["mappings_df"] = merge_keep_existing_scores(st.session_state["mappings_df"], new_map)

st.subheader("2) Kategorik mapping (otomatik üretildi)")
st.caption("Kategorik seçtiğin kolonların unique değerleri burada listelenir. Score_0_100 değerlerini IK ayarlar.")
edited_mappings = st.data_editor(
    st.session_state["mappings_df"],
    use_container_width=True,
    num_rows="dynamic",
    key="mappings_editor_dynamic"
)

# Direction = lower_better için basit tersleme: score = 100 - score
def apply_direction_inversion(df_scored: pd.DataFrame, weights_df: pd.DataFrame) -> pd.DataFrame:
    df_scored = df_scored.copy()
    for _, w in weights_df.iterrows():
        crit = w["Criterion"]
        direction = w.get("Direction", "higher_better")
        colname = f"Score__{crit}"
        if colname in df_scored.columns and direction == "lower_better":
            df_scored[colname] = 100.0 - pd.to_numeric(df_scored[colname], errors="coerce").fillna(50.0)
    return df_scored

st.divider()
st.subheader("3) Skorla ve sırala")

top_n = st.slider("Kaç aday gösterilsin?", 10, min(500, len(df)), min(100, len(df)), 10)

if st.button("Skorla", type="primary"):
    try:
        ranked, used_weights, used_mappings = score_candidates(df, edited_weights, edited_mappings)

        # Direction terslemeyi uygula (score_candidates Direction'ı kullanmıyor, burada uyguluyoruz)
        ranked = apply_direction_inversion(ranked, used_weights)

        # TotalScore'u direction sonrası yeniden hesaplamak için:
        # Score__ kolonları değiştiyse TotalScore'u tekrar hesaplayalım.
        # (weights normalize zaten used_weights'ta var)
        score_cols = []
        for _, w in used_weights.iterrows():
            crit = w["Criterion"]
            if f"Score__{crit}" in ranked.columns:
                score_cols.append((f"Score__{crit}", float(w.get("WeightNorm", 0))))

        total = 0
        for col, wn in score_cols:
            total += pd.to_numeric(ranked[col], errors="coerce").fillna(50.0) * wn

        ranked["TotalScore_0_100"] = total.round(2)
        ranked = ranked.sort_values("TotalScore_0_100", ascending=False).reset_index(drop=True)
        ranked["Rank"] = ranked.index + 1
        cols = ["Rank"] + [c for c in ranked.columns if c != "Rank"]
        ranked = ranked[cols]

        st.success("Skorlama tamamlandı.")
        st.dataframe(ranked.head(top_n), use_container_width=True)

        out = io.BytesIO()
        with pd.ExcelWriter(out, engine="openpyxl") as writer:
            ranked.to_excel(writer, index=False, sheet_name="Ranked")
            used_weights.to_excel(writer, index=False, sheet_name="Weights_Used")
            edited_mappings.to_excel(writer, index=False, sheet_name="Mappings_Used")
        out.seek(0)

        st.download_button(
            "Ranked Excel'i indir",
            data=out,
            file_name="ranked_candidates.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    except Exception as e:
        st.error(f"Hata: {e}")
