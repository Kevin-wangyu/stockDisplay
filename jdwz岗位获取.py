import csv
import json
import os
import random
import time
from pathlib import Path

import requests

URL = "https://wzryzk.81rc.mil.cn/qjwzwb/stuchooseexam/querykspoFullByAjax.htm"
REFERER = "https://wzryzk.81rc.mil.cn/qjwzwb/stuchooseexam/viewPosition.htm?xjks=0&sfzx=&kqtjbz="

# 不要把 Cookie/examid/userid 写死在代码里
COOKIE = 'uid=112; Hm_lvt_ad65617c1b92d555455e06647127f54e=1793774331; HMACCOUNT=7E169B478F84A7A0; JSESSIONID=8A23331C7F2A726E55620E589DB635BE; Hm_lpvt_ad65617c1b92d555455e06647127f54e=1793776262; uid=112; Hm_lvt_ad65617c1b92d555455e06647127f54e=1793774331; HMACCOUNT=7E169B478F84A7A0; JSESSIONID=4FB94F429455E846B9EB8EB8B62B77C0; Hm_lpvt_ad65617c1b92d555455e06647127f54e=1793776211; uid=112; Hm_lvt_ad65617c1b92d555455e06647127f54e=1793774331; HMACCOUNT=7E169B478F84A7A0; Hm_lpvt_ad65617c1b92d555455e06647127f54e=1793775646; JSESSIONID=8A23331C7F2A726E55620E589DB635BE; uid=112; Hm_lvt_ad65617c1b92d555455e06647127f54e=1793774331; HMACCOUNT=7E169B478F84A7A0; Hm_lpvt_ad65617c1b92d555455e06647127f54e=1793776262; SERVERID=af1d1d3c67513255d64441d4c96744e7|1793776300|1793774332'
EXAM_ID = '5083fd4b2a75e4d1'
USER_ID = '9654b864bf5caf1c'

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/156.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Content-Type": "application/x-www-form-urlencoded",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://wzryzk.81rc.mil.cn",
    "Referer": REFERER,
    "Cookie": COOKIE,
}

BASE_PAYLOAD = {
    "xl": "2||||5",
    "sxzydm": "A07",
    "examid": EXAM_ID,
    "userid": USER_ID,
}

OUT_JSONL = Path("wzryzk_jobs.jsonl")


def fetch_page(session: requests.Session, page: int) -> dict:
    data = dict(BASE_PAYLOAD)
    data["page"] = str(page)

    for attempt in range(3):
        r = session.post(URL, data=data, timeout=20)

        # 遇到限流/临时服务错误，指数退避后重试
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep((2 ** attempt) + random.random())
            continue

        r.raise_for_status()

        try:
            obj = r.json()
        except ValueError:
            raise RuntimeError("返回非 JSON，可能会话失效或被拦截，停止，不要绕过登录/验证码")

        z = obj.get("zzpoList") or {}
        if z.get("code") != 200:
            raise RuntimeError(f"接口返回异常: {z.get('message')}")

        return z["data"]

    raise RuntimeError(f"第 {page} 页重试失败")


def main():
    session = requests.Session()
    session.headers.update(HEADERS)

    first = fetch_page(session, 1)
    total = int(first["total"])
    total_page = int(first["totalPage"])
    print(f"total={total}, totalPage={total_page}")

    seen = set()

    # 追加写入，保留原始字段；中断后可按 zwdm 去重续爬
    with OUT_JSONL.open("a", encoding="utf-8") as f:
        for page in range(1, total_page + 1):
            if page > 1:
                # 串行低频：每页间隔 3~8 秒，不要并发
                time.sleep(random.uniform(3, 8))

            data = first if page == 1 else fetch_page(session, page)

            for item in data.get("list", []):
                zwdm = item.get("zwdm")
                if zwdm and zwdm in seen:
                    continue
                if zwdm:
                    seen.add(zwdm)

                f.write(json.dumps(item, ensure_ascii=False) + "\n")

            print(f"page {page}/{total_page}, rows={len(data.get('list', []))}")


def jsonl_to_csv(jsonl_path: Path, csv_path: Path):
    rows = [
        json.loads(line)
        for line in jsonl_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    fields = []
    for row in rows:
        for k in row:
            if k not in fields:
                fields.append(k)

    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
    jsonl_to_csv(OUT_JSONL, Path("wzryzk_jobs.csv"))