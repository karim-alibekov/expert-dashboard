# -*- coding: utf-8 -*-
# БИЗНЕС-ДАШБОРД: оцениваем совпадение "О себе" только с тремя полями:
# Специализации, Ключевые слова, Решаемые задачи.

import os
import io
import math
import pandas as pd
import numpy as np
import streamlit as st
import plotly.express as px

# ------------------------- PAGE CONFIG (первая st-команда!) -------------------
st.set_page_config(page_title="Экспертный штат: совпадение профилей", layout="wide")

# ------------------------- CONFIG ---------------------------------------------
# Укажи пути к файлам при необходимости
FILE_PATHS = [
    "Профили_ВЭС.xlsx",
    "Профили_Специалисты.xlsx",
    "Профили_эксперты.xlsx",
]

AUTO_DISCOVER = False
SEARCH_DIR = "/Users/karimalibekov/Desktop/stat_consult_analysis"

ABOUT_COL = "Коротко о себе"

# В расчет метрик включаем ТОЛЬКО эти три поля
COMPARE_COLS = ["Специализации", "Ключевые слова", "Решаемые задачи"]

# человекочитаемые названия метрик
METRIC_LABELS = {
    "Специализации": "Specialty Fit",
    "Ключевые слова": "Keyword Fit",
    "Решаемые задачи": "Task Fit",
}

ID_CANDIDATES = ["ФИО", "Фамилия Имя Отчество", "Имя", "Фамилия и Имя", "ФИО полностью"]

# Пороги (фиксированные)
LOW_THR = 0.40       # ниже — "Низкий (Исправить)"
HIGH_THR = 0.60      # выше — "Высокий (ОК)"
TARGET_PTA = 0.65    # целевой средний PTA компании (для KPI)

# ------------------------- HELPERS --------------------------------------------
def discover_files(folder: str):
    if not os.path.isdir(folder):
        return []
    return sorted([
        os.path.join(folder, name)
        for name in os.listdir(folder)
        if name.startswith("Профили_") and name.endswith(".xlsx")
    ])

@st.cache_data(show_spinner=True)
def load_all(paths):
    frames = []
    for p in paths:
        if not os.path.exists(p):
            st.warning(f"Файл не найден: {p}")
            continue
        try:
            df = pd.read_excel(p)
            df["__source"] = os.path.basename(p)
            frames.append(df)
        except Exception as e:
            st.warning(f"Не удалось прочитать {p}: {e}")
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    # базовая чистка строк
    df = df.applymap(lambda x: x.strip() if isinstance(x, str) else x)
    df = df.drop_duplicates()
    return df

def pick_id_col(df: pd.DataFrame):
    for c in ID_CANDIDATES:
        if c in df.columns:
            return c
    return df.columns[0]

@st.cache_resource(show_spinner=True)
def load_model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")

@st.cache_data(show_spinner=True)
def compute_similarities(df_in: pd.DataFrame, about_col: str, compare_cols: list) -> pd.DataFrame:
    """
    Считаем косинусные сходства между эмбеддингами 'Коротко о себе' и тремя полями:
    'Специализации', 'Ключевые слова', 'Решаемые задачи'.
    PTA = среднее этих трех метрик по доступным столбцам.
    """
    from sentence_transformers import util

    df = df_in.copy()
    sim_cols = []

    # если нет about — вернем пустые метрики
    if about_col not in df.columns:
        for c in compare_cols:
            df[f"sim_{c}"] = np.nan
        df["PTA"] = np.nan
        return df

    model = load_model()

    # валидные "О себе"
    mask = df[about_col].notna() & (df[about_col].astype(str).str.len() >= 5)
    df["_has_about"] = mask

    about_texts = df.loc[mask, about_col].astype(str).tolist()
    if len(about_texts) == 0:
        for c in compare_cols:
            df[f"sim_{c}"] = np.nan
        df["PTA"] = np.nan
        return df

    about_embs = model.encode(about_texts, convert_to_tensor=True, show_progress_bar=True)
    idx_map = {idx: i for i, idx in enumerate(df.index[df["_has_about"]])}

    # по каждому из трех столбцов
    for col in compare_cols:
        values = []
        for idx, row in df.iterrows():
            if not row["_has_about"]:
                values.append(np.nan)
                continue
            other = row.get(col, None)
            if pd.isna(other) or len(str(other)) < 3:
                values.append(np.nan)
                continue
            emb_other = model.encode(str(other), convert_to_tensor=True)
            emb_about = about_embs[idx_map[idx]]
            cos = float(util.cos_sim(emb_about, emb_other))
            values.append(cos)
        sim_name = f"sim_{col}"
        df[sim_name] = values
        sim_cols.append(sim_name)

    # PTA = среднее по трем sim_* (по доступным)
    df["PTA"] = df[sim_cols].mean(axis=1)
    return df

def cohort_label(pta: float) -> str:
    if pd.isna(pta): return "Недостаточно данных"
    if pta < LOW_THR: return "Низкий (Исправить)"
    if pta < HIGH_THR: return "Средний (Внимание)"
    return "Высокий (ОК)"

def to_excel_bytes(df: pd.DataFrame, sheet_name: str = "data") -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
    buf.seek(0)
    return buf.read()

def fmt_pct(x):
    try:
        return f"{x:.0%}"
    except:
        return "—"

def safe_mean(series):
    return float(series.mean()) if len(series) else float("nan")

# ------------------------- LOAD DATA ------------------------------------------
if AUTO_DISCOVER:
    FILE_PATHS = discover_files(SEARCH_DIR)

df_raw = load_all(FILE_PATHS)
if df_raw.empty:
    st.error("Нет данных. Проверь пути к Excel или включи AUTO_DISCOVER.")
    st.stop()

ID_COL = pick_id_col(df_raw)

# оставим только реально существующие из трех нужных столбцов
compare_cols = [c for c in COMPARE_COLS if c in df_raw.columns]
missing = [c for c in COMPARE_COLS if c not in df_raw.columns]
if missing:
    st.warning("Отсутствуют столбцы (не будут учтены в расчете): " + ", ".join(missing))

with st.spinner("Считаем совпадение 'О себе' с фактами по 3 метрикам…"):
    df = compute_similarities(df_raw, ABOUT_COL, compare_cols)

df["Когорта"] = df["PTA"].apply(cohort_label)

# для удобства: список sim_* колонок и маппинг метрик
sim_cols_present = [f"sim_{c}" for c in compare_cols]
metric_names = [METRIC_LABELS[c] for c in compare_cols]

# ------------------------- SIDEBAR FILTERS ------------------------------------
st.sidebar.header("Фильтры")
role_vals = ["Все"] + (sorted(df["Роль"].dropna().unique().tolist()) if "Роль" in df.columns else [])
city_vals = ["Все"] + (sorted(df["Город"].dropna().unique().tolist()) if "Город" in df.columns else [])

sel_role = st.sidebar.selectbox("Роль", role_vals, index=0)
sel_city = st.sidebar.selectbox("Город", city_vals, index=0)

data = df.copy()
if "Роль" in data.columns and sel_role != "Все":
    data = data[data["Роль"] == sel_role]
if "Город" in data.columns and sel_city != "Все":
    data = data[data["Город"] == sel_city]

# ------------------------- HEADER ---------------------------------------------
st.markdown("## Совпадение профилей с реальными компетенциями (бизнес-дашборд)")
st.caption(
    "Оцениваем совпадение “О себе” только с **тремя ключевыми полями**: "
    "Специализации (Specialty Fit), Ключевые слова (Keyword Fit) и Решаемые задачи (Task Fit). "
    "PTA = среднее этих трех показателей. "
    "Когорты: **Высокий (ОК)** ≥ 0.60, **Средний (Внимание)** 0.40–0.59, **Низкий (Исправить)** < 0.40."
)

# ------------------------- TABS -----------------------------------------------
tab1, tab2, tab3 = st.tabs([
    "Executive Overview",
    "Risk & Actions",
    "People Ops",
])

# ========================= TAB 1: EXECUTIVE OVERVIEW ==========================
with tab1:
    c1, c2, c3, c4 = st.columns(4)
    overall_pta = safe_mean(data["PTA"])
    share_high = (data["Когорта"] == "Высокий (ОК)").mean() if len(data) else 0
    share_mid  = (data["Когорта"] == "Средний (Внимание)").mean() if len(data) else 0
    share_low  = (data["Когорта"] == "Низкий (Исправить)").mean() if len(data) else 0

    c1.metric("Средний PTA (3 метрики)", f"{overall_pta:.3f}" if not math.isnan(overall_pta) else "—",
              delta=f"Цель: {TARGET_PTA:.2f}")
    c2.metric("% Высокий (ОК)", fmt_pct(share_high))
    c3.metric("% Средний (Внимание)", fmt_pct(share_mid))
    c4.metric("% Низкий (Исправить)", fmt_pct(share_low))

    st.markdown("---")
    left, right = st.columns([1,1])

    # Распределение когорт
    with left:
        st.subheader("Распределение по когортам")
        if len(data):
            coh = (data["Когорта"]
                   .value_counts()
                   .reindex(["Высокий (ОК)", "Средний (Внимание)", "Низкий (Исправить)"])
                   .fillna(0)
                   .reset_index())
            coh.columns = ["Когорта", "Кол-во"]
            st.plotly_chart(px.pie(coh, values="Кол-во", names="Когорта", hole=0.45),
                            use_container_width=True)
        else:
            st.info("Нет данных под текущие фильтры.")

    # Средние значения по трем метрикам (что слабее/сильнее)
    with right:
        st.subheader("Где сильнее/слабее (средние по 3 метрикам)")
        if sim_cols_present:
            bar_df = data[sim_cols_present].mean().reset_index()
            bar_df.columns = ["Колонка", "Среднее"]
            # заменим технические имена на бизнес-лейблы
            bar_df["Метрика"] = bar_df["Колонка"].str.replace("sim_", "", regex=False).map(METRIC_LABELS)
            bar_df = bar_df.sort_values("Среднее", ascending=False)
            st.plotly_chart(px.bar(bar_df, x="Метрика", y="Среднее"), use_container_width=True)
        else:
            st.info("Нет рассчитанных метрик (sim_*). Проверь входные столбцы.")

    # Разрез по ролям
    st.subheader("PTA по ролям (топ-10)")
    if "Роль" in data.columns and len(data):
        role_df = (data.groupby("Роль", as_index=False)["PTA"]
                   .mean().sort_values("PTA", ascending=False).head(10))
        st.plotly_chart(px.bar(role_df, x="Роль", y="PTA"), use_container_width=True)
    else:
        st.info("Недостаточно данных по ролям.")

    # Разрез по городам
    st.subheader("PTA по городам (топ-10)")
    if "Город" in data.columns and len(data):
        city_df = (data.groupby("Город", as_index=False)["PTA"]
                   .mean().sort_values("PTA", ascending=False).head(10))
        st.plotly_chart(px.bar(city_df, x="Город", y="PTA"), use_container_width=True)
    else:
        st.info("Недостаточно данных по городам.")

    # Авто-выводы
    st.markdown("### 🧩 Ключевые выводы")
    bullets = []
    if not math.isnan(overall_pta):
        bullets.append(f"- Средний PTA (по 3 метрикам): **{overall_pta:.2f}** (цель: **{TARGET_PTA:.2f}**).")
    bullets.append(f"- Структура когорт: **ОК** {fmt_pct(share_high)}, **Внимание** {fmt_pct(share_mid)}, **Исправить** {fmt_pct(share_low)}.")
    if sim_cols_present:
        means_map = {METRIC_LABELS[c]: data[f"sim_{c}"].mean() for c in compare_cols}
        weakest = min(means_map, key=means_map.get) if means_map else None
        strongest = max(means_map, key=means_map.get) if means_map else None
        if weakest:
            bullets.append(f"- Слабее всего: **{weakest}**; сильнее всего: **{strongest}**.")
    if not bullets:
        bullets.append("- Недостаточно данных для выводов.")
    st.markdown("\n".join(bullets))

# ========================= TAB 2: RISK & ACTIONS ==============================
with tab2:
    st.subheader("Риск и действия (сфокусировано на 3 метриках)")

    cols_show = [ID_COL, "Роль", "Город", "Когорта", "PTA", ABOUT_COL]
    # Добавим три метрики, если они есть
    for c in compare_cols:
        coln = f"sim_{c}"
        if coln in data.columns:
            cols_show.append(coln)

    # Низкая когорта — в исправление
    st.markdown("#### 🔴 Профили для исправления (Низкий PTA)")
    fix_df = data[data["Когорта"] == "Низкий (Исправить)"].sort_values("PTA")
    if len(fix_df):
        st.dataframe(fix_df[cols_show].head(30), use_container_width=True, height=360)
        st.download_button("⬇️ Скачать список на исправление (Excel)",
                           data=to_excel_bytes(fix_df[cols_show], "to_fix"),
                           file_name="profiles_to_fix.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    else:
        st.info("Нет профилей в зоне 'Низкий (Исправить)' под текущие фильтры.")

    st.markdown("---")

    # Средняя когорта — обратить внимание
    st.markdown("#### 🟡 Профили под внимание (Средний PTA)")
    watch_df = data[data["Когорта"] == "Средний (Внимание)"].sort_values("PTA")
    if len(watch_df):
        st.dataframe(watch_df[cols_show].head(30), use_container_width=True, height=360)
        st.download_button("⬇️ Скачать список под внимание (Excel)",
                           data=to_excel_bytes(watch_df[cols_show], "to_watch"),
                           file_name="profiles_to_watch.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    else:
        st.info("Нет профилей в когорте 'Средний (Внимание)' под текущие фильтры.")

    st.markdown("---")

    # Высокая когорта — ОК (можно ставить на клиента)
    st.markdown("#### 🟢 Готовы к клиенту (Высокий PTA)")
    ok_df = data[data["Когорта"] == "Высокий (ОК)"].sort_values("PTA", ascending=False)
    if len(ok_df):
        st.dataframe(ok_df[cols_show].head(30), use_container_width=True, height=360)
        st.download_button("⬇️ Скачать готовых к клиенту (Excel)",
                           data=to_excel_bytes(ok_df[cols_show], "ready"),
                           file_name="profiles_ready.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    else:
        st.info("Нет профилей с 'Высокий (ОК)' под текущие фильтры.")

    st.markdown("---")

    # Рекомендации (на основе слабой метрики)
    st.markdown("### 📌 Рекомендации")
    recs = []
    # какие из трех метрик слабее по среднему
    if sim_cols_present:
        means_map = {METRIC_LABELS[c]: data[f"sim_{c}"].mean() for c in compare_cols}
        if means_map:
            # сортируем по возрастанию (слабее — выше приоритет)
            weak_sorted = sorted(means_map.items(), key=lambda x: x[1])
            for metric, val in weak_sorted:
                if metric == "Keyword Fit":
                    recs.append("- **Keyword Fit** низкий → стандартизировать словарь ключевых слов (синонимы → базовые формы, отсеять общие слова). Добавить 5–7 точных маркеров компетенций в 'О себе'.")
                elif metric == "Task Fit":
                    recs.append("- **Task Fit** низкий → привести 'О себе' к языку реальных задач (начинать с глаголов: «диагностирую», «провожу», «внедряю» + 2–3 типовых кейса).")
                elif metric == "Specialty Fit":
                    recs.append("- **Specialty Fit** низкий → сверить список специализаций с реальными кейсами; убрать общий шум, добавить точные направления.")
    # общие рекомендации по когортам
    share_high = (data["Когорта"] == "Высокий (ОК)").mean() if len(data) else 0
    share_mid  = (data["Когорта"] == "Средний (Внимание)").mean() if len(data) else 0
    share_low  = (data["Когорта"] == "Низкий (Исправить)").mean() if len(data) else 0
    overall_pta = safe_mean(data["PTA"])

    if overall_pta < TARGET_PTA and not math.isnan(overall_pta):
        recs.append(f"- Средний PTA = {overall_pta:.2f} ниже цели {TARGET_PTA:.2f} → начать с 'Низкий (Исправить)' и затем обработать 'Средний (Внимание)'.")
    if share_low > 0.15:
        recs.append("- 'Низкий (Исправить)' > 15% → провести экспресс-аудит профилей с шаблоном 'как должно быть' и примерами.")
    if share_mid > 0.30:
        recs.append("- Высокая доля 'Средний (Внимание)' → короткий воркшоп по самопрезентации и тезаурусу ключевых слов.")
    if not recs:
        recs.append("- Уровень соответствует ожиданиям. Рекомендуется ежемесячный мониторинг и точечные правки.")
    st.markdown("\n".join(recs))

# ========================= TAB 3: PEOPLE OPS =================================
with tab3:
    st.subheader("Операции: найм, обучение, заполненность")

    # Hiring: где PTA низок и доля 'ОК' мала → усиливать найм / наставничество
    st.markdown("#### Hiring Map — где усиливать найм/наставничество")
    if "Город" in data.columns and len(data):
        by_city = (data.groupby("Город")
                        .agg(PTA=("PTA", "mean"),
                             OKshare=("Когорта", lambda s: (s=="Высокий (ОК)").mean()),
                             Count=("PTA", "size"))
                        .reset_index())
        hire_need = by_city[(by_city["PTA"] < 0.55) & (by_city["OKshare"] < 0.50)]
        st.dataframe(hire_need.sort_values(["PTA", "OKshare"]).head(20),
                     use_container_width=True, height=320)
        st.download_button("⬇️ Скачать Hiring Map (Excel)",
                           data=to_excel_bytes(hire_need, "hiring_map"),
                           file_name="hiring_map.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    else:
        st.info("Недостаточно данных по городам для Hiring Map.")

    st.markdown("---")

    # Training Plan: какие из трех метрик слабее всего по компании/выборке
    st.markdown("#### Training Plan — что именно подтягивать")
    if sim_cols_present:
        train_df = pd.DataFrame({
            "Метрика": [METRIC_LABELS[c] for c in compare_cols],
            "Среднее": [data[f"sim_{c}"].mean() for c in compare_cols],
        }).sort_values("Среднее")
        st.dataframe(train_df, use_container_width=True, height=280)
        st.download_button("⬇️ Скачать Training Plan (Excel)",
                           data=to_excel_bytes(train_df, "training_plan"),
                           file_name="training_plan.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    else:
        st.info("Нет рассчитанных метрик — сформировать план обучения нельзя.")

    st.markdown("---")

    # Completeness: заполненность трех ключевых полей + 'О себе'
    st.markdown("#### Заполненность профилей (4 ключевых поля)")
    key_cols = [ABOUT_COL] + compare_cols
    present_cols = [c for c in key_cols if c in data.columns]
    if present_cols:
        compl = data[present_cols].notna().mean(axis=1)
        compl_df = data[[ID_COL, "Роль", "Город", "PTA", "Когорта"]].copy()
        compl_df["Completeness"] = compl.values
        st.dataframe(compl_df.sort_values("Completeness").head(30),
                     use_container_width=True, height=320)
        st.download_button("⬇️ Скачать список на донаполнение (Excel)",
                           data=to_excel_bytes(compl_df.sort_values("Completeness"), "completeness"),
                           file_name="profiles_completeness.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    else:
        st.info("Ключевые поля для расчёта заполненности не найдены.")
