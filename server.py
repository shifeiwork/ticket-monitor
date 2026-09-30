# -*- coding: utf-8 -*-
"""
12306 余票监测 - 本地代理服务

作用：浏览器直接调 12306 接口会被同源策略（CORS）拦截，
所以由这个本地服务代为请求，再转发给页面。

启动：python server.py
页面：浏览器打开 http://127.0.0.1:8765
"""

import json
import os
import re
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, urlencode

import requests

PORT = 8766
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- 站点代号

STATION_CACHE = {}
_station_lock = threading.Lock()


def station_cache_path():
    return os.path.join(BASE_DIR, "station_codes.json")


def load_station_cache():
    global STATION_CACHE
    try:
        with open(station_cache_path(), "r", encoding="utf-8") as f:
            STATION_CACHE = json.load(f)
    except Exception:
        STATION_CACHE = {}


def download_station_codes(session):
    """从 12306 下载站点代号表并缓存。返回 {中文名: 电报码}。"""
    global STATION_CACHE
    url = "https://kyfw.12306.cn/otn/resources/js/framework/station_name.js"
    resp = session.get(url, timeout=15)
    resp.encoding = "utf-8"
    names = re.findall(r"@[a-z]+\|([^|]+)\|([A-Z]+)\|", resp.text)
    mapping = {}
    for cn, code in names:
        mapping[cn] = code
    with _station_lock:
        STATION_CACHE = mapping
        try:
            with open(station_cache_path(), "w", encoding="utf-8") as f:
                json.dump(mapping, f, ensure_ascii=False)
        except Exception:
            pass
    return mapping


def get_station_code(session, name):
    """查站点电报码，支持模糊匹配。"""
    name = (name or "").strip()
    if not name:
        return None
    with _station_lock:
        cache_empty = not STATION_CACHE
    if cache_empty:
        download_station_codes(session)
    with _station_lock:
        if name in STATION_CACHE:
            return STATION_CACHE[name]
        # 模糊：站名包含输入词，取第一个
        for cn, code in STATION_CACHE.items():
            if name in cn:
                return code
    return None


# ---------------------------------------------------------------- 12306 查询

def make_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://kyfw.12306.cn/otn/leftTicket/init",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    return s


def query_tickets(from_name, to_name, train_date):
    """
    查余票。返回 dict。

    train_date 格式: 2026-09-24
    """
    session = make_session()
    try:
        # 第一步：访问查询页，建立会话
        session.get("https://kyfw.12306.cn/otn/leftTicket/init", timeout=15)

        from_code = get_station_code(session, from_name)
        to_code = get_station_code(session, to_name)
        if not from_code or not to_code:
            return {
                "ok": False,
                "error": "站点代号查询失败：%s / %s" % (from_name, to_name),
                "trains": [],
            }

        # 第二步：查余票
        api = "https://kyfw.12306.cn/otn/leftTicket/query"
        params = {
            "leftTicketDTO.train_date": train_date,
            "leftTicketDTO.from_station": from_code,
            "leftTicketDTO.to_station": to_code,
            "purpose_codes": "ADULT",
        }
        resp = session.get(api, params=params, timeout=15)
        if resp.status_code != 200:
            return {
                "ok": False,
                "error": "接口返回 %d" % resp.status_code,
                "trains": [],
            }

        data = resp.json()
        if not data.get("data"):
            return {
                "ok": True,
                "trains": [],
                "raw_msg": data.get("messages", ""),
            }

        result = data["data"].get("result", []) or []
        trains = []
        for row in result:
            f = row.split("|")
            # 12306 字段索引（经验证）：
            # 30=二等座 31=一等座 32=商务座
            # 值为"有"或数字=有票，"无"或空=无票
            train = {
                "车次": f[3],
                "出发站": f[6],
                "到达站": f[7],
                "发车": f[8],
                "到达": f[9],
                "历时": f[10],
                "二等座": f[30],
                "一等座": f[31],
                "商务座": f[32],
                "无座": f[26],
                "软卧": f[23],
                "硬卧": f[28],
                "硬座": f[29],
                "候补": f[37] if len(f) > 37 else "",
            }
            trains.append(train)

        trains.sort(key=lambda t: t["发车"])
        return {"ok": True, "trains": trains}

    except requests.exceptions.RequestException as e:
        return {"ok": False, "error": "网络请求异常：%s" % e, "trains": []}
    except Exception as e:
        return {"ok": False, "error": "查询异常：%s" % e, "trains": []}


# ---------------------------------------------------------------- 12306 预订直链

# 12306 前端页面读取 URL 参数后，会自动筛选出指定车次并预选席别，
# 实现"打开即定位、只看符合条件的车次"。
BOOKING_BASE = "https://kyfw.12306.cn/otn/leftTicket/init.html"

# 席别 -> 12306 seatTypes 参数代码
SEAT_TYPE_CODES = {
    "商务座": "M",
    "一等座": "P",
    "二等座": "O",
    "软座": "5",
    "硬座": "6",
    "软卧": "3",
    "硬卧": "4",
    "无座": "9",
}


def build_booking_url(from_name, to_name, train_date, train_no, seat_types):
    """
    生成 12306 预订页直链，已按 车次 + 席别 筛选。

    返回 str；若站点代号缺失返回 None。
    """
    session = make_session()
    from_code = get_station_code(session, from_name)
    to_code = get_station_code(session, to_name)
    if not from_code or not to_code:
        return None
    if not train_no:
        return None

    codes = [SEAT_TYPE_CODES.get(s) for s in (seat_types or [])]
    codes = [c for c in codes if c]

    params = [
        ("leftTicketDTO.train_date", train_date),
        ("leftTicketDTO.from_station", from_code),
        ("leftTicketDTO.to_station", to_code),
        ("leftTicketDTO.train_no", train_no),
    ]
    if codes:
        params.append(("leftTicketDTO.seatTypes", "".join(codes)))

    return BOOKING_BASE + "?" + urlencode(params)


# ---------------------------------------------------------------- HTTP 服务

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_error(404)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            return self._send_file(
                os.path.join(BASE_DIR, "index.html"),
                "text/html; charset=utf-8",
            )

        if path == "/api/stations":
            with _station_lock:
                stations = [
                    {"name": name, "code": code}
                    for name, code in STATION_CACHE.items()
                ]
            return self._send_json({"ok": True, "stations": stations})

        if path == "/api/query":
            qs = parse_qs(parsed.query)

            def one(k, d=""):
                return qs.get(k, [d])[0]

            res = query_tickets(
                from_name=one("from"),
                to_name=one("to"),
                train_date=one("date"),
            )
            # 附上站点代号，供前端拼接预订直链
            try:
                sess = make_session()
                res["from_code"] = get_station_code(sess, one("from"))
                res["to_code"] = get_station_code(sess, one("to"))
            except Exception:
                pass
            return self._send_json(res)

        if path == "/api/booking-url":
            qs = parse_qs(parsed.query)

            def one(k, d=""):
                return qs.get(k, [d])[0]

            seat_list = qs.get("seat", [])
            url = build_booking_url(
                from_name=one("from"),
                to_name=one("to"),
                train_date=one("date"),
                train_no=one("train_no"),
                seat_types=seat_list,
            )
            return self._send_json({"ok": url is not None, "url": url})

        self.send_error(404)


def main():
    os.chdir(BASE_DIR)
    load_station_cache()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = "http://127.0.0.1:%d" % PORT
    print("=" * 56)
    print("  12306 余票监测服务已启动")
    print("  请在浏览器打开：%s" % url)
    print("  关闭本窗口即停止服务")
    print("=" * 56)
    # 启动后 1 秒自动打开浏览器
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")


if __name__ == "__main__":
    main()
