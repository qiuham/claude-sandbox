"""同进程的 DNS 中继：本地 SOCKS5，自己用 DoH 查域名，只把 IP 交给上游代理。

浏览器 ──域名──> 本中继 ──DoH（经代理连 1.1.1.1）──> Cloudflare
                      └── IP ──> 上游代理（Clash）──> 出口

启动和停止由上层（browser/tasks.py）控制：本模块不注册信号，也不退出进程。
"""

from __future__ import annotations

import contextlib
import socket
import struct
import threading

from ccs import config
from ccs.network.doh import ATYP_DOMAIN, ATYP_IPV4, ATYP_IPV6, DohClient, recvn, upstream_connect

BUF = 65536


def _dns_section(cfg: dict) -> dict:
    value = cfg.get("dns")
    return value if isinstance(value, dict) else cfg


def _timeout(cfg: dict, key: str) -> float:
    timeouts = (cfg.get("browser") or {}).get("timeouts") or {}
    return float(timeouts.get(key, config.DEFAULT_CONFIG["browser"]["timeouts"][key]))


def _reply(conn, code: int, addr: bytes = b"\x00\x00\x00\x00", port: int = 0) -> None:
    conn.sendall(b"\x05" + bytes([code]) + b"\x00\x01" + addr + struct.pack("!H", port))


def _close(sock) -> None:
    if sock is None:
        return
    with contextlib.suppress(OSError):
        sock.shutdown(socket.SHUT_RDWR)
    with contextlib.suppress(OSError):
        sock.close()


def _pipe(src, dst) -> None:
    with contextlib.suppress(Exception):
        while True:
            data = src.recv(BUF)
            if not data:
                break
            dst.sendall(data)
    _close(src)
    _close(dst)


class DnsRelay:
    """本地 SOCKS5 中继：域名在这里用 DoH 解析，上游只收 IP。"""

    def __init__(self, upstream: str, cfg: dict, log=None) -> None:
        dns = _dns_section(cfg)
        defaults = config.DEFAULT_CONFIG["dns"]
        self.upstream = upstream.replace("socks5h://", "socks5://")
        self.listen_host = str(dns.get("listen_host") or defaults["listen_host"])
        self.listen_port = int(dns.get("listen_port") or 0)
        self.client_timeout = float(dns.get("client_timeout_s")
                                    or defaults["client_timeout_s"])
        self.connect_timeout = float(dns.get("connect_timeout_s")
                                     or defaults["connect_timeout_s"])
        self.stop_timeout = _timeout(cfg, "relay_stop_s")
        self.log = log or (lambda _msg: None)
        self.doh = DohClient(self.upstream, cfg)
        self.addr = ""
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()
        self._conns: set = set()
        self._conns_lock = threading.Lock()

    # ------------------------------------------------------------ 生命周期

    def start(self) -> str:
        """开始监听，返回 socks5://127.0.0.1:<port>。重复调用返回同一个地址。"""
        if self._thread is not None:
            return self.addr
        sock = socket.socket()
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.listen_host, self.listen_port))
        sock.listen(128)
        self._sock = sock
        port = sock.getsockname()[1]
        self.addr = f"socks5://127.0.0.1:{port}"
        self._stopping.clear()
        self._thread = threading.Thread(target=self._accept_loop, args=(sock,),
                                        name="ccs-dns-relay", daemon=True)
        self._thread.start()
        return self.addr

    def stop(self) -> None:
        """关监听和全部活连接。可以重复调用。"""
        self._stopping.set()
        sock, self._sock = self._sock, None
        if sock is not None:
            _close(sock)
        with self._conns_lock:
            conns = list(self._conns)
            self._conns.clear()
        for conn in conns:
            _close(conn)
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=self.stop_timeout)
        self.addr = ""

    # ------------------------------------------------------------ 连接管理

    def _track(self, conn) -> None:
        with self._conns_lock:
            if not self._stopping.is_set():
                self._conns.add(conn)
                return
        _close(conn)                      # stop 已经开始，新连接直接关掉

    def _untrack(self, conn) -> None:
        with self._conns_lock:
            self._conns.discard(conn)

    def _accept_loop(self, sock: socket.socket) -> None:
        while not self._stopping.is_set():
            try:
                conn, _ = sock.accept()
            except OSError:
                break
            if self._stopping.is_set():
                _close(conn)
                break
            self._track(conn)
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn) -> None:
        upstream_sock = None
        try:
            target = self._negotiate(conn)
            if target is None:
                return
            host, hosts, port = target
            upstream_sock = self._connect_upstream(hosts, port)
            self._track(upstream_sock)
            _reply(conn, 0)
            self.log(f"OK {host}:{port} -> {hosts[0]}")
            threading.Thread(target=_pipe, args=(conn, upstream_sock), daemon=True).start()
            _pipe(upstream_sock, conn)
        except Exception as exc:  # noqa: BLE001 - 单条连接出错只能记日志，不能拖垮整个中继
            self.log(f"中继失败: {type(exc).__name__}: {exc}")
            with contextlib.suppress(Exception):
                _reply(conn, 4)
        finally:
            self._untrack(conn)
            self._untrack(upstream_sock)
            _close(conn)
            _close(upstream_sock)

    # ------------------------------------------------------------ 协议处理

    def _negotiate(self, conn) -> tuple[str, list[str], int] | None:
        """做 SOCKS5 握手，返回 (原始主机名, 候选 IP 列表, 端口)。非 CONNECT 回 0x07。"""
        conn.settimeout(self.client_timeout)
        recvn(conn, 1)
        methods = recvn(conn, recvn(conn, 1)[0])
        del methods
        conn.sendall(b"\x05\x00")

        _ver, cmd, _rsv, atyp = recvn(conn, 4)
        if cmd != 1:
            _reply(conn, 7)
            return None
        host, is_ip = self._read_host(conn, atyp)
        port = struct.unpack("!H", recvn(conn, 2))[0]
        conn.settimeout(None)
        candidates = [host] if is_ip else self.doh.resolve(host)
        if not candidates:
            msg = f"没有解析结果：{host}"
            raise OSError(msg)
        return host, candidates, port

    @staticmethod
    def _read_host(conn, atyp: int) -> tuple[str, bool]:
        if atyp == ATYP_IPV4:
            return socket.inet_ntoa(recvn(conn, 4)), True
        if atyp == ATYP_DOMAIN:
            length = recvn(conn, 1)[0]
            return recvn(conn, length).decode("utf-8", "replace"), False
        if atyp == ATYP_IPV6:
            return socket.inet_ntop(socket.AF_INET6, recvn(conn, 16)), True
        msg = f"不认识的地址类型 {atyp}"
        raise OSError(msg)

    def _connect_upstream(self, hosts: list[str], port: int) -> socket.socket:
        last: Exception | None = None
        for target in hosts:
            try:
                return upstream_connect(self.upstream, target, port, self.connect_timeout)
            except OSError as exc:
                last = exc
        raise last or OSError("所有地址都连不上")
