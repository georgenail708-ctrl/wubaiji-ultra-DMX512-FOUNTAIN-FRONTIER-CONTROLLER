"""局域网 LLM 服务自动发现 —— 免记 IP、免手改配置。

场景：两台电脑同一 WiFi，一台跑 llama-server/Ollama（有 GPU），另一台用实验室。
每次换机都要查 IP、改配置很烦；本模块扫本网段常见 LLM 端口，直接给出可用的端点。

探测方式（按可靠性）：
  1) 优先 HTTP 探活（/health 或 /api/tags），能确认是 LLM 服务；
  2) TCP 连通只作为弱信号（端口开着但不是 LLM 的情况很多）。
并发扫描（线程池），单点超时短（默认 0.6s），24 位网段 ~ 全扫通常 2~5 秒。
"""
from __future__ import annotations

import concurrent.futures
import ipaddress
import json
import socket
import urllib.request
from typing import Iterable

# 常见本地 LLM 服务端口（llama.cpp / Ollama / LM Studio / vLLM / text-generation-webui）
DEFAULT_PORTS = (8080, 11434, 8000, 1234, 5000)

# 各端口的协议与端点模板（OpenAI 兼容口是通用解）
_PORT_HINTS = {
    11434: ("ollama", "/v1/chat/completions", "/api/tags"),
    8080: ("llamacpp", "/v1/chat/completions", "/health"),
    8000: ("llamacpp", "/v1/chat/completions", "/health"),
    1234: ("lmstudio", "/v1/chat/completions", "/v1/models"),
    5000: ("openai-compat", "/v1/chat/completions", "/v1/models"),
}


def local_ipv4() -> str:
    """本机在同一网段的出口 IPv4（用于推断 /24 网段）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return ""
    finally:
        s.close()


def _http_probe(host: str, port: int, timeout: float) -> dict | None:
    """HTTP 探活：命中 LLM 特征端点才算数。返回服务描述或 None。"""
    fam, path, health = _PORT_HINTS.get(port, ("openai-compat", "/v1/chat/completions", "/health"))
    base = f"http://{host}:{port}"
    for probe in (health, "/v1/models", "/api/tags"):
        try:
            with urllib.request.urlopen(base + probe, timeout=timeout) as r:
                if r.status != 200:
                    continue
                raw = r.read(4096)
                detail = ""
                try:
                    j = json.loads(raw.decode("utf-8", "replace"))
                    if isinstance(j, dict):
                        # Ollama /api/tags 给模型列表
                        models = [m.get("name") for m in (j.get("models") or []) if isinstance(m, dict)]
                        # OpenAI /v1/models
                        models += [m.get("id") for m in (j.get("data") or []) if isinstance(m, dict)]
                        models = [m for m in models if m]
                        if models:
                            detail = models[0][:60]
                except Exception:
                    pass
                return {"host": host, "port": port, "base": base,
                        "url": base + path, "kind": fam,
                        "health_path": probe, "model_hint": detail}
        except Exception:
            continue
    return None


def _tcp_open(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def scan_hosts(hosts: Iterable[int], ports: Iterable[int] = DEFAULT_PORTS,
               timeout: float = 0.6) -> list[dict]:
    """扫指定主机号（最后一段）的常见 LLM 端口。返回可用服务列表（已排序）。"""
    me = local_ipv4()
    if not me:
        return []
    net = ipaddress.ip_network(me + "/24", strict=False)
    prefix = str(net.network_address).rsplit(".", 1)[0]
    found: list[dict] = []

    def check(ip: str, port: int):
        s = _http_probe(ip, port, timeout)
        if s:
            return s
        if _tcp_open(ip, port, timeout):          # 弱信号：端口开但未确认为 LLM
            return {"host": ip, "port": port, "base": f"http://{ip}:{port}",
                    "url": f"http://{ip}:{port}/v1/chat/completions",
                    "kind": "unknown", "health_path": "", "model_hint": "",
                    "weak": True}
        return None

    tasks = [(f"{prefix}.{h}", p) for h in hosts for p in ports]
    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as ex:
        futs = [ex.submit(check, ip, p) for ip, p in tasks]
        for f in concurrent.futures.as_completed(futs):
            try:
                r = f.result()
            except Exception:
                r = None
            if r:
                found.append(r)
    # 本机排最前（同机最省事），其余按 IP 排
    found.sort(key=lambda x: (0 if x["host"] == me else 1, x["host"], x["port"]))
    return found


def scan_lan(full: bool = True, timeout: float = 0.6) -> list[dict]:
    """扫本 /24 网段。full=True 全 254 台；否则只扫常见尾号（快）。"""
    hosts = range(1, 255) if full else (1, 2, 100, 101, 102, 150, 153, 200, 254)
    return scan_hosts(hosts, timeout=timeout)


def scan_specific(ips: Iterable[str], ports: Iterable[int] = DEFAULT_PORTS,
                  timeout: float = 0.8) -> list[dict]:
    """扫指定 IP 列表（用户已知对方 IP 时直接指定，最快）。"""
    out: list[dict] = []
    tasks = [(ip, p) for ip in ips for p in ports]
    with concurrent.futures.ThreadPoolExecutor(max_workers=32) as ex:
        futs = {ex.submit(_http_probe, ip, p, timeout): (ip, p) for ip, p in tasks}
        for f in concurrent.futures.as_completed(futs):
            try:
                r = f.result()
            except Exception:
                r = None
            if r:
                out.append(r)
    out.sort(key=lambda x: (x["host"], x["port"]))
    return out
