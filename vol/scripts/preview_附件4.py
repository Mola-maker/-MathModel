import pandas as pd
import json
import sys

try:
    df = pd.read_excel(r"/workspace/vol/data/附件4.xlsx")
except Exception:
    try:
        df = pd.read_csv(r"/workspace/vol/data/附件4.xlsx", encoding="gbk")
    except Exception:
        df = pd.read_csv(r"/workspace/vol/data/附件4.xlsx", encoding="latin-1")

info = {
    "file": "附件4.xlsx",
    "shape": list(df.shape),
    "columns": list(df.columns),
    "dtypes": {col: str(dt) for col, dt in df.dtypes.items()},
    "missing": {col: int(df[col].isnull().sum()) for col in df.columns},
    "missing_pct": {col: round(df[col].isnull().sum() / len(df) * 100, 2) for col in df.columns},
    "head": df.head(5).to_dict(orient="records"),
    "numeric_cols": list(df.select_dtypes(include="number").columns),
    "non_numeric_cols": list(df.select_dtypes(exclude="number").columns),
}

print(json.dumps(info, ensure_ascii=False, default=str))
