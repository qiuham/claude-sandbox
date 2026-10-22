"""Claude 支持的国家和地区（ISO 3166-1 alpha-2）。

名单来自 https://www.anthropic.com/supported-countries ，抓取日期 2026-10-23。
页面更新后改这里即可；只认两位代码，不认国家名字，避免语言和译名差异。

香港（HK）、澳门（MO）、中国大陆（CN）都不在名单里；台湾（TW）在。

"""

from __future__ import annotations

SOURCE = "https://www.anthropic.com/supported-countries"
CHECKED_ON = "2026-10-23"

SUPPORTED = frozenset({
    "AD", "AE", "AG", "AL", "AM", "AO", "AR", "AT", "AU", "AZ", "BA", "BB",
    "BD", "BE", "BF", "BG", "BH", "BI", "BJ", "BN", "BO", "BR", "BS", "BT",
    "BW", "BZ", "CA", "CD", "CF", "CG", "CH", "CI", "CL", "CM", "CO", "CR",
    "CV", "CY", "CZ", "DE", "DJ", "DK", "DM", "DO", "DZ", "EC", "EE", "EG",
    "ER", "ES", "ET", "FI", "FJ", "FM", "FR", "GA", "GB", "GD", "GE", "GH",
    "GM", "GN", "GQ", "GR", "GT", "GW", "GY", "HN", "HR", "HT", "HU", "ID",
    "IE", "IL", "IN", "IQ", "IS", "IT", "JM", "JO", "JP", "KE", "KG", "KH",
    "KI", "KM", "KN", "KR", "KW", "KZ", "LA", "LB", "LC", "LI", "LK", "LR",
    "LS", "LT", "LU", "LV", "LY", "MA", "MC", "MD", "ME", "MG", "MH", "MK",
    "ML", "MN", "MR", "MT", "MU", "MV", "MW", "MX", "MY", "MZ", "NA", "NE",
    "NG", "NI", "NL", "NO", "NP", "NR", "NZ", "OM", "PA", "PE", "PG", "PH",
    "PK", "PL", "PS", "PT", "PW", "PY", "QA", "RO", "RS", "RW", "SA", "SB",
    "SC", "SD", "SE", "SG", "SI", "SK", "SL", "SM", "SN", "SO", "SR", "SS",
    "ST", "SV", "SZ", "TD", "TG", "TH", "TJ", "TL", "TM", "TN", "TO", "TR",
    "TT", "TV", "TW", "TZ", "UA", "UG", "US", "UY", "UZ", "VA", "VC", "VN",
    "VU", "WS", "ZA", "ZM", "ZW",
})


def supported(code: str) -> bool:
    """两位国家码是否在名单里。空串当作未知，调用方自己决定。"""
    return str(code or "").upper() in SUPPORTED
