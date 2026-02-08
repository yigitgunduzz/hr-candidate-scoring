import io
import pandas as pd
import streamlit as st
from scoring import score_candidates

# -------------------- Page --------------------
st.set_page_config(page_title="HR Candidate Scoring", page_icon="✅", layout="wide")

st.markdown(
    """
<style>
.block-container { padding-top: 1.0rem; padding-bottom: 2.0rem; max-width: 1200px; }
h1, h2, h3 { letter-spacing: -0.2px; }
.small { font-size: 0.9rem; opacity: 0.85; }
.badge { display:inline-block; padding:4px 10px; border-radius:999px; border:1px solid rgba(49,51,63,.18); }
.card { padding:14px 16px; border-radius:14px; border:1px solid rgba(49,51,63,.16); background: rgba(255,255,255,0.02); }
.card h4 { margin: 0 0 6px 0; font-size: 0.95rem; opacity: .85; }
.card .v { font-size: 1.4rem; font-weight: 750; }
.hr { height:1px; background: rgba(49,51,63,.14); margin: 14px 0; }
.stButton>button { border-radius: 12px; padding: 0.58rem 1.0rem; font-weight: 650; }
[data-testid="stDataEditor"], [data-testid="stDataFrame"] { border-radius: 14px; }
</style>
""",
    unsafe_allow_html=True,
)

METHODS = ["numeric_minmax", "numeric_0_100", "numeric_0_1", "categorical_mapping"]
DIRECTIONS = ["higher_better", "lower_better"]

# -------------------- Helpers --------------------
def guess_method(series: pd.Series) -> str:
    s = pd.to_numeric(series, errors="coerce")
    numeric_ratio = s.notna().mean()
    if numeric_ratio >= 0.8:
        if len(s.dropna()) > 0 and s.dropna().between(0, 1).mean() >= 0.95:
            return "numeric_0_1"
        if len(s.dropna()) > 0 and s.dropna().between(0, 100).mean() >= 0.95:
            return "numeric_0_100"
        return "numeric_minmax"
    return "categorical_mapping"

def make_weights(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    w = round(1.0 / max(1, len(cols)), 4)
    rows = []
    for c in cols:
        rows.append(
            {"Criterion": c, "Weight": w, "Method": guess_method(df[c]), "Direction": "higher_better"}
        )
    return pd.DataFrame(rows)

def build_mapping(df: pd.DataFrame, weights_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, w in weights_df.iterrows():
        crit = w["Criterion"]
        if w["Method"] != "categorical_mapping":
            continue
        cats = df[crit].dropna().astype(str).str.strip().unique().tolist()
        cats = sorted(cats)
        for cat in cats:
            rows.append({"Criterion": crit, "Category": cat, "Score_0_100": 60.0})
    return pd.DataFrame(rows)

def merge_keep_scores(old_map: pd.DataFrame, new_map: pd.DataFrame) -> pd.DataFrame:
    if old_map is None or old_map.empty:
        return new_map
    old = old_map.copy()
    new = new_map.copy()
    old["__k"] = old["Criterion"].astype(str) + "||" + old["Category"].astype(str)
    new["__k"] = new["Criterion"].astype(str) + "||" + new["Category"].astype(str)
    old_scores = dict(zip(old["__k"], old["Score_0_100"]))
    new["Score_0_100"] = new["__k"].map(lambda k: old_scores.get(k, 60.0))
    return new.drop(columns=["__k"])

def apply_direction(df_scored: pd.DataFrame, weights_df: pd.DataFrame) -> pd.DataFrame:
    out = df_scored.copy()
    for _, w in weights_df.iterrows():
        crit = w["Criterion"]
        direction = w.get("Direction", "higher_better")
        col = f"Score__{crit}"
        if col in out.columns and direction == "lower_better":
            out[col] = 100.0 - pd.to_numeric(out[col], errors="coerce").fillna(50.0)
    return out

def recompute_total(df_scored: pd.DataFrame, weights_df: pd.DataFrame) -> pd.DataFrame:
    out = df_scored.copy()
    score_cols = []
    for _, w in weights_df.iterrows():
        crit = w["Criterion"]
        col = f"Score__{crit}"
        if col in out.columns:
            score_cols.append((col, float(w.get("WeightNorm", 0))))
    total = 0
    for col, wn in score_cols:
        total += pd.to_numeric(out[col], errors="coerce").fillna(50.0) * wn
    out["TotalScore_0_100"] = total.round(2)
    out = out.sort_values("TotalScore_0_100", ascending=False).reset_index(drop=True)
    out.insert(0, "Rank", out.index + 1)
    return out

def kpi(title: str, value: str, hint: str = ""):
    st.markdown(
        f"""
<div class="card">
  <h4>{title}</h4>
  <div class="v">{value}</div>
  <div class="small">{hint}</div>
</div>
""",
        unsafe_allow_html=True,
    )

# -------------------- Header --------------------
st.markdown('<span class="badge">MVP</span> <span class="badge">Dynamic Excel</span>', unsafe_allow_html=True)
st.title("HR Candidate Scoring")
st.write("Excel yükle → kriterleri seç → tek tıkla skorla. Gelişmiş ayarlar sadece gerektiğinde açılır.")

st.markdown('<div class="hr"></div>', unsafe_allow_html=True)

# -------------------- Session state --------------------
if "step" not in st.session_state:
    st.session_state.step = 1

def goto(n: int):
    st.session_state.step = n

# -------------------- Stepper --------------------
steps = {
    1: "Dosya",
    2: "Kriterler",
    3: "Skorla",
    4: "Sonuç",
}
progress = (st.session_state.step - 1) / (len(steps) - 1)
st.progress(progress, text=f"Adım {st.session_state.step}/4 — {steps[st.session_state.step]}")

# -------------------- STEP 1: Upload --------------------
if st.session_state.step == 1:
    st.subheader("1) Excel yükle")
    uploaded = st.file_uploader("Excel dosyan (.xlsx)", type=["xlsx"])

    c1, c2 = st.columns([2, 1])
    with c2:
        st.info("İpucu: Uygulama kolon isimlerine bağlı değil. Türkçe/İngilizce fark etmez.")

    if uploaded is None:
        st.stop()

    file_bytes = uploaded.getvalue()
    xls = pd.ExcelFile(io.BytesIO(file_bytes), engine="openpyxl")

    sheet = st.selectbox("Hangi sheet okunacak?", xls.sheet_names, index=0)
    df = pd.read_excel(xls, sheet_name=sheet)

    st.session_state["file_bytes"] = file_bytes
    st.session_state["sheet"] = sheet
    st.session_state["df"] = df

    a, b, c = st.columns(3)
    with a: kpi("Aday sayısı", f"{len(df)}", "Yüklenen satır sayısı")
    with b: kpi("Kolon sayısı", f"{len(df.columns)}", "Kriterleri birazdan seçeceksin")
    with c: kpi("Sheet", sheet, "Excel içinden seçildi")

    st.dataframe(df.head(15), use_container_width=True)

    st.button("Devam →", type="primary", on_click=lambda: goto(2))

# -------------------- STEP 2: Criteria selection --------------------
if st.session_state.step == 2:
    df = st.session_state["df"]

    st.subheader("2) Kriterleri seç")
    st.write("Skorlamaya dahil edeceğin kolonları seç. Sistem otomatik yöntem ve ağırlık önerir.")

    # quick presets (optional)
    with st.expander("Hızlı seçim önerileri", expanded=False):
        st.caption("Sık kullanılan aday kriterleri için öneriler. Sadece kolaylık için.")
        st.write("Örnek: GPA, Tecrübe, Dil Seviyesi, Sertifika, Okul/Şirket Seviyesi, Maaş Beklentisi…")

    all_cols = list(df.columns)

    # smart defaults: pick columns with more signal (not too unique like IDs/emails)
    def is_probably_id(col: str) -> bool:
        s = col.lower()
        return any(x in s for x in ["id", "mail", "eposta", "e-posta", "phone", "telefon", "tc", "kimlik"])

    candidates = []
    for c in all_cols:
        nunique = df[c].nunique(dropna=True)
        # skip ultra-unique columns (likely identifiers), but keep numeric
        if is_probably_id(c) and nunique > len(df) * 0.8:
            continue
        candidates.append(c)

    default_selected = candidates[: min(10, len(candidates))] if candidates else all_cols[: min(8, len(all_cols))]

    selected = st.multiselect("Kriter kolonları", options=all_cols, default=default_selected)

    if not selected:
        st.warning("En az 1 kriter seçmelisin.")
        st.stop()

    # build weights (store)
    if st.session_state.get("weights_for_cols") != tuple(selected):
        st.session_state["weights_df"] = make_weights(df, selected)
        st.session_state["weights_for_cols"] = tuple(selected)

    # Minimal UI: show only 3 most important columns, advanced in expander
    st.markdown("#### Önerilen ayarlar (dilersen düzenle)")
    edited_weights = st.data_editor(
        st.session_state["weights_df"],
        use_container_width=True,
        num_rows="dynamic",
        column_config={
            "Method": st.column_config.SelectboxColumn("Method", options=METHODS),
            "Direction": st.column_config.SelectboxColumn("Direction", options=DIRECTIONS),
        },
        key="weights_editor_product",
    )

    # auto mapping only if needed
    new_map = build_mapping(df, edited_weights)
    if "mappings_df" not in st.session_state:
        st.session_state["mappings_df"] = new_map
    else:
        st.session_state["mappings_df"] = merge_keep_scores(st.session_state["mappings_df"], new_map)

    # Advanced settings hidden by default
    with st.expander("Gelişmiş ayarlar (opsiyonel)", expanded=False):
        default_cat = st.number_input("Bulunmayan kategori varsayılan skoru", 0.0, 100.0, 60.0, 1.0)
        normalize = st.toggle("Ağırlıkları normalize et (önerilir)", value=True)
        st.session_state["default_cat"] = float(default_cat)
        st.session_state["normalize"] = bool(normalize)

        if not st.session_state["mappings_df"].empty:
            st.markdown("**Kategorik Mapping** (sadece kategorik kriterlerde çıkar)")
            st.caption("Sadece gerekli olduğunda açtık. Score_0_100 değerlerini düzenleyebilirsin.")
            st.session_state["mappings_df"] = st.data_editor(
                st.session_state["mappings_df"],
                use_container_width=True,
                num_rows="dynamic",
                key="mappings_editor_product",
            )

    st.session_state["weights_df"] = edited_weights

    nav1, nav2 = st.columns([1, 1])
    with nav1:
        st.button("← Geri", on_click=lambda: goto(1))
    with nav2:
        st.button("Devam →", type="primary", on_click=lambda: goto(3))

# -------------------- STEP 3: Score --------------------
if st.session_state.step == 3:
    df = st.session_state["df"]
    weights_df = st.session_state["weights_df"]
    mappings_df = st.session_state.get("mappings_df", pd.DataFrame(columns=["Criterion", "Category", "Score_0_100"]))
    default_cat = st.session_state.get("default_cat", 60.0)
    normalize = st.session_state.get("normalize", True)

    st.subheader("3) Skorla")
    st.write("Tek tıkla skorla. Sonuç ekranında grafikler, filtre ve indirme var.")

    c1, c2, c3 = st.columns(3)
    with c1: kpi("Kriter sayısı", str(len(weights_df)), "Seçtiğin kolonlar")
    with c2: kpi("Kategorik kriter", str((weights_df["Method"]=="categorical_mapping").sum()), "Mapping gerekebilir")
    with c3:
        wsum = pd.to_numeric(weights_df["Weight"], errors="coerce").fillna(0).sum()
        kpi("Weight toplamı", f"{wsum:.2f}", "Normalize açıksa otomatik 1 olur")

    st.markdown('<div class="hr"></div>', unsafe_allow_html=True)

    colA, colB = st.columns([1, 2])
    with colA:
        run = st.button("✅ Skoru Hesapla", type="primary", use_container_width=True)
        st.button("← Geri", on_click=lambda: goto(2), use_container_width=True)
    with colB:
        st.caption("Not: Direction=lower_better seçili kriterlerde skor ters çevrilir (100 - skor).")

    if not run:
        st.stop()

    try:
        ranked, used_weights, _ = score_candidates(
            df, weights_df, mappings_df,
            normalize_weights=normalize,
            default_category_score=default_cat,
        )
        ranked = apply_direction(ranked, used_weights)
        ranked = recompute_total(ranked, used_weights)

    except Exception as e:
        st.exception(e)
        st.stop()

    st.session_state["ranked"] = ranked
    st.session_state["used_weights"] = used_weights
    goto(4)
    st.rerun()

# -------------------- STEP 4: Results --------------------
if st.session_state.step == 4:
    ranked = st.session_state["ranked"]
    used_weights = st.session_state["used_weights"]

    st.subheader("4) Sonuç")
    st.write("Aşağıda özet, grafikler ve indirilebilir çıktı var.")

    r1, r2, r3, r4 = st.columns(4)
    with r1: kpi("Top skor", f"{ranked['TotalScore_0_100'].max():.2f}", "En yüksek aday")
    with r2: kpi("Ortalama", f"{ranked['TotalScore_0_100'].mean():.2f}", "Havuz ortalaması")
    with r3: kpi("Median", f"{ranked['TotalScore_0_100'].median():.2f}", "Ortanca")
    with r4: kpi("Aday", f"{len(ranked)}", "Toplam sıralanan aday")

    st.markdown('<div class="hr"></div>', unsafe_allow_html=True)

    # Charts
    c1, c2 = st.columns([2, 1])
    with c1:
        st.markdown("#### Skor dağılımı")
        # bucket histogram
        bins = pd.cut(ranked["TotalScore_0_100"], bins=[0,10,20,30,40,50,60,70,80,90,100], include_lowest=True)
        hist = bins.value_counts().sort_index()
        st.bar_chart(hist)
    with c2:
        st.markdown("#### Top 10")
        top10 = ranked.head(10)[["Rank", "TotalScore_0_100"]].copy()
        st.dataframe(top10, use_container_width=True, hide_index=True)

    st.markdown("#### Filtre & Liste")
    q1, q2, q3 = st.columns([2, 1, 1])
    with q1:
        query = st.text_input("Ara (herhangi bir kolonda)", value="")
    with q2:
        min_s = st.number_input("Min skor", 0.0, 100.0, 0.0, 1.0)
    with q3:
        max_s = st.number_input("Max skor", 0.0, 100.0, 100.0, 1.0)

    filtered = ranked[(ranked["TotalScore_0_100"] >= min_s) & (ranked["TotalScore_0_100"] <= max_s)]
    if query.strip():
        q = query.strip().lower()
        mask = filtered.astype(str).apply(lambda row: row.str.lower().str.contains(q, na=False)).any(axis=1)
        filtered = filtered[mask]

    show_n = st.slider("Gösterilecek satır", 10, min(500, len(filtered)), min(100, len(filtered)), 10)
    st.dataframe(filtered.head(show_n), use_container_width=True)

    # Explainability: pick one candidate and show contribution
    with st.expander("Bir adayın skor kırılımı (isteğe bağlı)", expanded=False):
        pick_rank = st.number_input("Rank seç", 1, int(ranked["Rank"].max()), 1, 1)
        row = ranked[ranked["Rank"] == int(pick_rank)].iloc[0]
        score_cols = [c for c in ranked.columns if c.startswith("Score__")]
        contrib = []
        for _, w in used_weights.iterrows():
            crit = w["Criterion"]
            col = f"Score__{crit}"
            if col in ranked.columns:
                wn = float(w.get("WeightNorm", 0))
                contrib.append({"Kriter": crit, "Skor(0-100)": float(row[col]), "Ağırlık": wn, "Katkı": float(row[col]) * wn})
        contrib_df = pd.DataFrame(contrib).sort_values("Katkı", ascending=False)
        st.dataframe(contrib_df.head(12), use_container_width=True, hide_index=True)
        st.caption("Katkı = Skor × Ağırlık (normalize sonrası). En çok katkı yapan kriterler üstte.")

    # Download
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        ranked.to_excel(writer, index=False, sheet_name="Ranked")
        used_weights.to_excel(writer, index=False, sheet_name="Weights_Used")
    out.seek(0)

    st.download_button(
        "⬇️ Ranked Excel'i indir",
        data=out,
        file_name="ranked_candidates.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
    )

    nav1, nav2 = st.columns([1, 1])
    with nav1:
        st.button("← Geri (Skorla)", on_click=lambda: goto(3))
    with nav2:
        st.button("🔄 Yeni dosya ile başla", on_click=lambda: (st.session_state.clear(), goto(1)))
