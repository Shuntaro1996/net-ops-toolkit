"""
core/validator.py — 入力バリデーションユーティリティ

対象:
- IPアドレス（IPv4）
- ホスト名
- SNMP Community文字列
- タイムアウト・リトライ数の範囲チェック
"""
import re
import ipaddress
from typing import Tuple


def validate_ip(ip: str) -> Tuple[bool, str]:
    """
    IPv4アドレスの書式を検証する。

    Returns:
        (True, "") if valid
        (False, error_message) if invalid
    """
    if not ip or not isinstance(ip, str):
        return False, "IPアドレスが空です"
    ip = ip.strip()
    try:
        ipaddress.IPv4Address(ip)
        return True, ""
    except ipaddress.AddressValueError:
        return False, f"'{ip}' は有効なIPv4アドレスではありません（例: 192.168.1.1）"


def validate_hostname(hostname: str) -> Tuple[bool, str]:
    """
    ホスト名の書式を検証する（RFC 952 / RFC 1123）。

    Returns:
        (True, "") if valid
        (False, error_message) if invalid
    """
    if not hostname or not isinstance(hostname, str):
        return False, "ホスト名が空です"
    hostname = hostname.strip()
    if len(hostname) > 253:
        return False, "ホスト名が長すぎます（253文字以内）"
    # 各ラベルの検証
    label_pattern = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?$")
    labels = hostname.split(".")
    for label in labels:
        if not label:
            return False, f"ホスト名に連続したドットが含まれています: '{hostname}'"
        if not label_pattern.match(label):
            return False, f"無効なラベル '{label}' が含まれています"
    return True, ""


def validate_ip_or_hostname(value: str) -> Tuple[bool, str]:
    """IPアドレスまたはホスト名として有効か検証する。"""
    ip_ok, _ = validate_ip(value)
    if ip_ok:
        return True, ""
    host_ok, host_err = validate_hostname(value)
    if host_ok:
        return True, ""
    return False, f"'{value}' は有効なIPアドレスでもホスト名でもありません"


def validate_timeout(timeout: float) -> Tuple[bool, str]:
    """タイムアウト値（秒）の範囲を検証する（0.1〜30秒）。"""
    try:
        t = float(timeout)
    except (TypeError, ValueError):
        return False, "タイムアウトは数値で指定してください"
    if t < 0.1 or t > 30:
        return False, f"タイムアウトは 0.1〜30 秒の範囲で指定してください（指定値: {t}）"
    return True, ""


def validate_retry(retry: int) -> Tuple[bool, str]:
    """リトライ回数の範囲を検証する（1〜10回）。"""
    try:
        r = int(retry)
    except (TypeError, ValueError):
        return False, "リトライ回数は整数で指定してください"
    if r < 1 or r > 10:
        return False, f"リトライ回数は 1〜10 の範囲で指定してください（指定値: {r}）"
    return True, ""


def validate_device_row(host: str, ip: str, timeout: float, retry: int) -> list[str]:
    """
    1機器分の全フィールドをまとめて検証し、エラーメッセージのリストを返す。
    空リストなら全OK。
    """
    errors = []
    if not host or not str(host).strip():
        errors.append("Host名が空です")

    # IPアドレスは厳密にIPv4として検証（ホスト名は不可）
    ip_ok, ip_err = validate_ip(str(ip))
    if not ip_ok:
        # IPv4でなければホスト名としても許可する
        host_ok, _ = validate_hostname(str(ip))
        if not host_ok:
            errors.append(ip_err)

    to_ok, to_err = validate_timeout(timeout)
    if not to_ok:
        errors.append(to_err)

    rt_ok, rt_err = validate_retry(retry)
    if not rt_ok:
        errors.append(rt_err)

    return errors
