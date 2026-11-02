"""通达信-问小达数据抓取（兼容入口）。

实际实现在 etl/tdx_*.py：
  - etl/tdx_config.py    配置
  - etl/tdx_calendar.py  交易日历
  - etl/tdx_progress.py  断点续传
  - etl/tdx_client.py    HTTP 客户端
  - etl/tdx_fetch.py     抓取主流程

保留本文件是为了兼容 `import xdget_daily; xdget_daily.run(date)`。
"""
from etl.tdx_fetch import run, fetch_today_index, today, type_params

__all__ = ["run", "fetch_today_index", "today", "type_params"]

if __name__ == "__main__":
    run(today)