"""Rôles sémantiques probables des colonnes (multilingue). Ce sont des indices pour les suggestions et le modèle,
jamais une décision de calcul cachée : le plan doit référencer explicitement les champs."""

from __future__ import annotations

import re
import unicodedata

ROLE_KEYWORDS: dict[str, list[str]] = {
    "amount": [
        "montant", "total", "ventes", "vente", "chiffre", "ca", "ttc", "ht", "revenu", "recette", "prix total", "encaissement",
        "amount", "sales", "revenue", "turnover", "total price", "net", "gross", "value",
        "сумма", "выручка", "продажи", "доход", "стоимость", "итог",
        "金额", "销售额", "收入", "营业额", "总价", "销售",
        "betrag", "umsatz", "erlös", "erloes", "summe", "wert", "verkauf",
        "importe", "ventas", "ingresos", "monto", "facturación", "facturacion", "valor",
    ],
    "cost": [
        "coût", "cout", "couts", "coûts", "achat", "prix d'achat", "charges", "dépense", "depense",
        "cost", "costs", "cogs", "expense", "purchase price",
        "себестоимость", "затраты", "расход", "издержки",
        "成本", "费用", "支出",
        "kosten", "einkaufspreis", "aufwand", "ausgabe",
        "coste", "costo", "gasto", "gastos",
    ],
    "quantity": [
        "quantité", "quantite", "qté", "qte", "nombre d'unités", "unités", "unites",
        "quantity", "qty", "units", "volume",
        "количество", "кол-во", "шт",
        "数量", "件数",
        "menge", "anzahl", "stück", "stueck",
        "cantidad", "unidades", "uds",
    ],
    "unit_price": ["prix unitaire", "pu", "unit price", "price", "prix", "цена", "单价", "价格", "preis", "einzelpreis", "precio", "precio unitario"],
    "date": [
        "date", "jour", "mois", "période", "periode", "day", "month", "period", "time", "timestamp", "created",
        "дата", "день", "месяц", "период", "日期", "时间", "月份", "datum", "tag", "monat", "zeitraum", "fecha", "día", "dia", "mes", "periodo",
    ],
    "order_id": [
        "commande", "n° commande", "numéro de commande", "no commande", "facture", "ticket", "transaction",
        "order", "order id", "order_id", "invoice", "receipt", "transaction id",
        "заказ", "номер заказа", "счет", "счёт", "чек", "订单", "订单号", "发票", "交易",
        "bestellung", "bestellnummer", "auftrag", "rechnung", "beleg", "pedido", "factura", "n.º pedido", "ticket",
    ],
    "customer": ["client", "clients", "customer", "buyer", "account", "клиент", "покупатель", "客户", "顾客", "kunde", "kunden", "cliente"],
    "product": [
        "produit", "article", "référence", "reference", "sku", "item", "product", "товар", "продукт", "артикул",
        "产品", "商品", "货品", "produkt", "artikel", "producto", "artículo", "articulo",
    ],
    "category": [
        "catégorie", "categorie", "famille", "gamme", "type", "segment", "category", "family", "group",
        "категория", "группа", "тип", "类别", "类型", "分类", "kategorie", "gruppe", "categoría", "categoria", "familia",
    ],
    "region": [
        "région", "region", "pays", "ville", "magasin", "boutique", "agence", "site", "country", "city", "store", "shop", "branch",
        "регион", "страна", "город", "магазин", "филиал", "地区", "区域", "国家", "城市", "门店", "land", "stadt", "filiale", "país", "pais", "ciudad", "tienda",
    ],
    "currency": ["devise", "monnaie", "currency", "валюта", "货币", "币种", "währung", "waehrung", "moneda", "divisa"],
    "status": [
        "statut", "état", "etat", "status", "state", "статус", "состояние", "状态", "zustand", "estado",
    ],
    "channel": ["canal", "channel", "source", "канал", "渠道", "kanal"],
    "numerator": ["conversions", "succès", "réussites", "convertis", "clics", "achats", "conversions", "converted", "успех", "转化", "erfolge", "éxitos"],
    "denominator": ["visites", "sessions", "essais", "tentatives", "impressions", "visits", "trials", "attempts", "визиты", "访问", "besuche", "visitas"],
    "tax_mode_ht": ["ht", "hors taxe", "net", "excl", "без ндс", "不含税", "netto", "sin iva"],
    "tax_mode_ttc": ["ttc", "toutes taxes", "gross", "incl", "с ндс", "含税", "brutto", "con iva"],
}

CANCEL_WORDS = [
    "annul", "rembours", "avoir", "retour", "cancel", "refund", "return", "void", "credit note",
    "отмен", "возврат", "取消", "退款", "退货", "storn", "erstatt", "rückgabe", "ruckgabe", "cancelad", "reembols", "devoluc", "devuelt",
]


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).lower().strip()
    return re.sub(r"[_\-./]+", " ", t)


def _match(name: str, kw: str) -> bool:
    if re.match(r"^[一-鿿]", kw):
        return kw in name
    return re.search(rf"(^|\s|\(){re.escape(kw)}($|\s|\)|s\b)", name) is not None


def guess_roles(header: str, col_type: str) -> list[str]:
    name = _norm(header)
    roles: list[str] = []
    for role, kws in ROLE_KEYWORDS.items():
        if any(_match(name, _norm(k)) for k in kws):
            roles.append(role)
    numeric = col_type in ("number", "money", "percent")
    filtered: list[str] = []
    for r in roles:
        if r in ("amount", "cost", "quantity", "unit_price", "numerator", "denominator", "tax_mode_ht", "tax_mode_ttc") and not numeric:
            continue
        if r == "date" and col_type != "date":
            continue
        # Une colonne numérique nommée « Commandes » est un comptage, pas un identifiant : l'utilisateur peut la typer
        # explicitement « identifiant » dans l'écran de vérification si c'est un numéro de commande.
        if r in ("product", "category", "region", "customer", "status", "channel", "currency", "order_id") and numeric:
            continue
        filtered.append(r)
    if col_type == "money" and "amount" not in filtered and "cost" not in filtered and "unit_price" not in filtered:
        filtered.append("amount")
    if col_type == "date" and "date" not in filtered:
        filtered.append("date")
    # « total » dans un nom de colonne numérique = montant ; un coût n'est jamais un montant de vente
    if "cost" in filtered and "amount" in filtered:
        filtered.remove("amount")
    if "unit_price" in filtered and "amount" in filtered and not any(_match(name, _norm(k)) for k in ("total", "montant", "amount", "сумма", "金额", "betrag", "importe")):
        filtered.remove("amount")
    return filtered


def is_cancel_value(value: str) -> bool:
    v = _norm(value)
    return any(w in v for w in CANCEL_WORDS)
