# precompute_metrics.py
import os
import pandas as pd
import numpy as np
from tqdm import tqdm
from sentence_transformers import SentenceTransformer, util

# === Настройки ===
ABOUT_COL = "Коротко о себе"
COMPARE_COLS = ["Специализации", "Ключевые слова", "Решаемые задачи"]

INPUT_FILES = [
    "Профили_ВЭС.xlsx",
    "Профили_Специалисты.xlsx",
    "Профили_эксперты.xlsx",
]

OUTPUT_PARQUET = "profiles_with_metrics.parquet"
OUTPUT_EXCEL = "profiles_with_metrics.xlsx"

# === Шаг 1. Загрузка и объединение ===
print("🔹 Loading data...")
frames = []
for file in INPUT_FILES:
    if os.path.exists(file):
        frames.append(pd.read_excel(file))
if not frames:
    raise FileNotFoundError("Не найдено ни одного файла профилей!")

df = pd.concat(frames, ignore_index=True).drop_duplicates()
df = df.applymap(lambda x: x.strip() if isinstance(x, str) else x)

# === Шаг 2. Модель ===
print("🔹 Loading sentence-transformer model...")
model = SentenceTransformer("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")

# === Шаг 3. Предрасчёт эмбеддингов ===
print("🔹 Encoding 'Коротко о себе'...")
mask = df[ABOUT_COL].notna() & (df[ABOUT_COL].astype(str).str.len() >= 5)
about_texts = df.loc[mask, ABOUT_COL].astype(str).tolist()
about_embs = model.encode(about_texts, convert_to_tensor=True, show_progress_bar=True)
idx_map = {idx: i for i, idx in enumerate(df.index[mask])}

# === Шаг 4. Семантическое сходство ===
sim_cols = []
print("🔹 Computing semantic similarities...")
for col in COMPARE_COLS:
    sims = []
    for idx, row in tqdm(df.iterrows(), total=len(df), desc=f"Processing {col}"):
        if not mask[idx]:
            sims.append(np.nan)
            continue
        other = row.get(col, None)
        if pd.isna(other) or len(str(other)) < 3:
            sims.append(np.nan)
            continue
        emb_other = model.encode(str(other), convert_to_tensor=True)
        emb_about = about_embs[idx_map[idx]]
        sims.append(float(util.cos_sim(emb_about, emb_other)))
    cname = f"sim_{col}"
    df[cname] = sims
    sim_cols.append(cname)

# === Шаг 5. PTA и когорты ===
df["PTA"] = df[sim_cols].mean(axis=1)

def define_cohort(pta):
    if pd.isna(pta):
        return "Недостаточно данных"
    if pta < 0.40:
        return "Низкий (Исправить)"
    if pta < 0.60:
        return "Средний (Внимание)"
    return "Высокий (ОК)"

df["Когорта"] = df["PTA"].apply(define_cohort)

# === Шаг 6. Сохранение ===
print("💾 Saving precomputed metrics...")
df.to_parquet(OUTPUT_PARQUET, index=False)
df.to_excel(OUTPUT_EXCEL, index=False)
print(f"✅ Done! Saved {OUTPUT_PARQUET} and {OUTPUT_EXCEL}")