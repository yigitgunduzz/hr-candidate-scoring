import io
import pandas as pd

# CLI (dosyayı direkt çalıştırınca) için varsayılan path'ler
INPUT_XLSX = r"C:\Users\yigit.gunduz\PycharmProjects\PythonProject\hr_candidate_scoring_template.xlsx"
OUTPUT_XLSX = r"C:\Users\yigit.gunduz\PycharmProjects\PythonProject\ranked_candidates.xlsx"


def minmax_to_100(s: pd.Series) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce")
    mn, mx = s.min(), s.max()
    if pd.isna(mn) or pd.isna(mx) or mx == mn:
        return pd.Series([50.0] * len(s), index=s.index)  # herkes aynıysa nötr
    return (s - mn) * 100.0 / (mx - mn)


def numeric_0_100(s: pd.Series) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce").fillna(0)
    return s.clip(0, 100).astype(float)


def numeric_0_1(s: pd.Series) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce").fillna(0)
    return (s.clip(0, 1) * 100.0).astype(float)


def build_mapping_dict(mappings_df: pd.DataFrame) -> dict:
    # {Criterion: {Category: Score}}
    out = {}
    for _, r in mappings_df.iterrows():
        crit = str(r["Criterion"]).strip()
        cat = str(r["Category"]).strip()
        score = float(r["Score_0_100"])
        out.setdefault(crit, {})[cat] = score
    return out


def _read_excel_sources(input_xlsx, engine="openpyxl"):
    """
    input_xlsx şunlardan biri olabilir:
      - str path (C:\\...\\file.xlsx)
      - bytes (Streamlit uploaded.getvalue())
      - file-like (BytesIO gibi)
    """
    if isinstance(input_xlsx, (bytes, bytearray)):
        bio = io.BytesIO(input_xlsx)
        xls = pd.ExcelFile(bio, engine=engine)
    elif hasattr(input_xlsx, "read"):  # file-like
        xls = pd.ExcelFile(input_xlsx, engine=engine)
    else:
        # path
        xls = pd.ExcelFile(str(input_xlsx), engine=engine)

    candidates = pd.read_excel(xls, sheet_name="Candidates")
    weights = pd.read_excel(xls, sheet_name="Config_Weights")
    mappings_df = pd.read_excel(xls, sheet_name="Config_Mappings")
    return candidates, weights, mappings_df


def score_candidates(
    candidates: pd.DataFrame,
    weights: pd.DataFrame,
    mappings_df: pd.DataFrame,
    *,
    normalize_weights: bool = True,
    default_category_score: float = 60.0
):
    """
    Streamlit tarafının direkt çağıracağı fonksiyon.
    Girdi: DataFrame'ler
    Çıktı: ranked_df, weights_used_df, mappings_used_df
    """
    map_dict = build_mapping_dict(mappings_df)

    weights_used = weights.copy()
    weights_used["Weight"] = pd.to_numeric(weights_used["Weight"], errors="coerce").fillna(0)

    if normalize_weights:
        wsum = weights_used["Weight"].sum()
        if wsum == 0:
            raise ValueError("Config_Weights.Weight toplamı 0 olamaz.")
        weights_used["WeightNorm"] = weights_used["Weight"] / wsum
    else:
        weights_used["WeightNorm"] = weights_used["Weight"]

    scored = candidates.copy()
    score_cols = []

    for _, w in weights_used.iterrows():
        crit = w["Criterion"]
        method = w["Method"]

        if crit not in scored.columns:
            continue

        s = scored[crit]
        if method == "numeric_minmax":
            sc = minmax_to_100(s)
        elif method == "numeric_0_100":
            sc = numeric_0_100(s)
        elif method == "numeric_0_1":
            sc = numeric_0_1(s)
        elif method == "categorical_mapping":
            crit_map = map_dict.get(str(crit).strip(), {})
            sc = (
                s.astype(str)
                .map(lambda x: crit_map.get(x.strip(), default_category_score))
                .astype(float)
            )
        else:
            raise ValueError(f"Bilinmeyen Method: {method} (Criterion={crit})")

        colname = f"Score__{crit}"
        scored[colname] = sc
        score_cols.append((colname, float(w["WeightNorm"])))

    total = 0
    for colname, wnorm in score_cols:
        total += pd.to_numeric(scored[colname], errors="coerce").fillna(50.0) * wnorm

    scored["TotalScore_0_100"] = total.round(2)

    ranked = scored.sort_values("TotalScore_0_100", ascending=False).reset_index(drop=True)
    ranked.insert(0, "Rank", ranked.index + 1)

    # ✅ KRİTİK: mutlaka return
    return ranked, weights_used, mappings_df
