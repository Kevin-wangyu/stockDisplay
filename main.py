"""FastAPI 入口。

启动：
    uvicorn main:app --reload --port 8000
或：
    python main.py

接口：
    GET /api/codes                                     股票列表
    GET /api/indicators                                所有可用指标
    GET /api/stock/{code}?start=&end=&indicators=a,b   个股 K 线 + 指标
    GET /api/stock/{code}/sectors                      个股所属行业/概念
    GET /api/sector/{kind}/{code}?start=&end=&indicators=a,b
                                                       行业/概念 K 线 + 指标
    GET /                                              前端页面
"""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from config import setup_logging
import queries

setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="Stock Display API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ───────────────────────── 工具 ─────────────────────────

def _records(df: pd.DataFrame) -> list[dict]:
    """DataFrame → JSON 友好 records：日期转 str，NaN/NaT 转 None。"""
    df = df.copy()
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.strftime("%Y-%m-%d")
    # 把 NaN / NaT / pd.NA 统一成 None
    df = df.astype(object).where(pd.notnull(df), None)
    return df.to_dict(orient="records")


def _parse_indicators(raw: Optional[str]) -> Optional[list[str]]:
    """把 "a,b,c" 解析成 ["a","b","c"]；空串或 None 表示使用默认指标集。"""
    if not raw:
        return None
    names = [x.strip() for x in raw.split(",") if x.strip()]
    return names or None


# ───────────────────────── 接口 ─────────────────────────

@app.get("/api/codes")
def api_codes():
    df = queries.list_codes()
    return _records(df)


@app.get("/api/indicators")
def api_indicators():
    return queries.list_available_indicators()

@app.get("/api/indicator_groups")
def api_indicator_groups():
    """返回级联下拉分组元数据。"""
    import indicators
    return {"cumulative_groups": indicators.CUMULATIVE_GROUPS}

@app.get("/api/stock/{code}/date_range")
def api_stock_date_range(code: str):
    try:
        return queries.get_stock_date_range(code)
    except Exception as e:
        logger.exception("查询日期范围失败 code=%s", code)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/concept/date_top10")
def api_concept_date_top10(trade_date: str = Query(..., description="YYYY-MM-DD")):
    try:
        return queries.get_concept_date_top10(trade_date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("概念榜单查询失败 %s", trade_date)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/concept/limit_up_stocks")
def api_concept_limit_up_stocks(
    concept_code: str = Query(...),
    trade_date:   str = Query(...),
    window_days:  int = Query(10, ge=1, le=60),
):
    try:
        return queries.get_concept_limit_up_stocks(
            concept_code, trade_date, window_days)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("涨停股查询失败 concept=%s", concept_code)
        raise HTTPException(status_code=500, detail=str(e))
    
@app.get("/api/trading_day_neighbor")
def api_trading_day_neighbor(
    date: str = Query(...),
    direction: str = Query(..., pattern="^(prev|next)$"),
):
    try:
        return queries.get_trading_day_neighbor(date, direction)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
@app.get("/api/trading_days_around")
def api_trading_days_around(
    date: str = Query(..., description="YYYY-MM-DD"),
    before: int = Query(400, ge=1, le=500),
    after:  int = Query(0,   ge=0, le=500),
):
    try:
        return queries.get_trading_days_around(date, before, after)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/stock/{code}")
def api_stock_chart(
    code: str,
    start: str = Query(..., description="YYYY-MM-DD"),
    end: str = Query(..., description="YYYY-MM-DD"),
    indicators: Optional[str] = Query(None, description="逗号分隔的指标名"),
):
    try:
        df = queries.get_stock_chart(
            code=code, start=start, end=end,
            indicators=_parse_indicators(indicators),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("查询失败 code=%s", code)
        raise HTTPException(status_code=500, detail=str(e))

    # 缺失交易日检测（不抛错，失败时返回空列表）
    try:
        missing = queries.get_missing_trading_days(code, start, end, kind="stock")
    except Exception:
        logger.exception("缺失检测失败 code=%s", code)
        missing = []

    return {
        "rows": _records(df),
        "missing_dates": missing,
    }


@app.get("/api/stock/{code}/sectors")
def api_stock_sectors(code: str):
    try:
        data = queries.get_stock_sectors(code)
    except Exception as e:
        logger.exception("查询映射失败 code=%s", code)
        raise HTTPException(status_code=500, detail=str(e))
    return JSONResponse(content=data)


@app.get("/api/sector/{kind}/{key}")
def api_sector_chart(
    kind: str,
    key: str,
    start: str = Query(..., description="YYYY-MM-DD"),
    end: str = Query(..., description="YYYY-MM-DD"),
    indicators: Optional[str] = Query(None),
):
    # key 是 6 位数字 → 按 code 查；否则 → 按 name 查
    key_col = "code" if (key.isdigit() and len(key) == 6) else "name"
    try:
        df = queries.get_sector_chart(
            sector_key=key,
            start=start,
            end=end,
            kind=kind,
            indicators=_parse_indicators(indicators),
            key_col=key_col,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("查询失败 kind=%s key=%s", kind, key)
        raise HTTPException(status_code=500, detail=str(e))
    return _records(df)

@app.get("/api/stock/{code}/industry_chart")
def api_stock_industry_chart(
    code: str,
    start: str = Query(..., description="YYYY-MM-DD"),
    end: str = Query(..., description="YYYY-MM-DD"),
    indicators: Optional[str] = Query(None),
):
    try:
        df = queries.get_stock_industry_chart(
            code=code, start=start, end=end,
            indicators=_parse_indicators(indicators),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("行业查询失败 code=%s", code)
        raise HTTPException(status_code=500, detail=str(e))

    # 行业名一并返回给前端
    industry_name = df.attrs.get("industry_name", "")
    records = _records(df)
    return {"industry_name": industry_name, "rows": records}

@app.get("/api/stock/{code}/concept_panel")
def api_concept_panel(
    code: str,
    trade_date: str = Query(..., description="YYYY-MM-DD"),
    days: int = Query(120, ge=1, le=1000),
):
    try:
        return queries.get_concept_panel(code, trade_date, days)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("概念面板查询失败 code=%s", code)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/concept/limit_up_ladder")
def api_limit_up_ladder(
    trade_date: str = Query(..., description="YYYY-MM-DD"),
    min_days:   int = Query(2, ge=2, le=20),
):
    try:
        return queries.get_limit_up_ladder(trade_date, min_days)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("涨停天梯查询失败 %s", trade_date)
        raise HTTPException(status_code=500, detail=str(e))
    
# 静态前端（放在最后，避免抢走 /api 路由）
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")


# ───────────────────────── 直接运行入口 ─────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8001, reload=True)