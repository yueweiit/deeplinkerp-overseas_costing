"""不依赖 Frappe 的来源归一化。未知/部分内容保留，不推断成最终值。"""
from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


import hashlib
import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone

PARSER_VERSION = 'logistics-settlement-4'


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(',', ':'))


def digest(*values):
    return hashlib.sha256(dumps(values).encode()).hexdigest()


def norm(value):
    text = unicodedata.normalize('NFKD', str(value or '')).lower()
    return re.sub(r'[^a-z0-9\u4e00-\u9fff]', '', ''.join(c for c in text if not unicodedata.combining(c)))


def identity(value):
    return unicodedata.normalize('NFKC', str(value or '')).strip().casefold()


def decoded(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            pass
    return value


def number(value):
    if value in (None, ''):
        return None
    text = str(value).strip().replace(' ', '')
    if ',' in text:
        if re.fullmatch(r'-?\d{1,3}(,\d{3})+(\.\d+)?', text):
            text = text.replace(',', '')
        elif re.fullmatch(r'-?\d+,\d{1,2}', text):
            text = text.replace(',', '.')
    try:
        result = Decimal(text)
        return str(result) if result.is_finite() else None
    except InvalidOperation:
        return None


def currency(value):
    value = decoded(value)
    if isinstance(value, list):
        value = value[0] if len(value) == 1 else ''
    key = norm(value)
    if key in ('rmb', 'cny', '人民币', '人民币rmb', '人民币cny', 'rmb人民币', 'cny人民币',
               '元', '人民币元') or str(value or '').strip() in {'¥', '￥'}:
        return 'RMB'
    if key in ('usd', '美元', '美金', 'dolar', 'dolares', '美元dolar', '美元usd', 'usd美元'):
        return 'USD'
    if key in ('mxn', 'peso', 'pesos', '比索', '墨西哥比索', '墨西哥比索mxn', '比索mxn', '比索peso', 'mxn比索'):
        return 'MXN'
    return str(value or '').strip().upper()


def timestamp(value):
    if not value:
        return ''
    result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return result.replace(tzinfo=result.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat()


def attachment_identity(attachment):
    # Poll attempts and health timestamps are not document content.
    keys = ('file_id', 'source_kind', 'source_position', 'file_name', 'bucket', 'object_key',
            'sha256', 'content_hash', 'file_size', 'size', 'actual_size', 'status', 'archive_status', 'retired_at')
    return {key: attachment.get(key) for key in keys}


def stable_lines(lines):
    return sorted(({k: v for k, v in line.items() if k not in {'source_position', 'raw'}} for line in lines), key=dumps)


def is_logistics_expense(fields):
    # Keep this exact category contract aligned with costing_read.is_logistics_purchase.
    # A selected service child is authoritative across renamed upper categories.
    def key(value):
        return re.sub(r'\s+', '', value).lower()

    def path(value):
        if isinstance(value, str):
            if value.strip().startswith('['):
                try:
                    value = json.loads(value)
                except ValueError:
                    return None
            else:
                value = re.split(r'→|->|>|/|／', value)
        if not isinstance(value, list) or len(value) not in (1, 2) or any(not isinstance(v, str) for v in value):
            return None
        return [key(v) for v in value]

    parents = {key(k) for k in ('采购类型','采购类别','采购分类','采购支出','Tipo de compra','Categoría de compra','Categoria de compra','Gastos de Compra','采购类型Tipo de compra','采购类别Categoría de compra','采购类别Categoria de compra','采购分类Categoría de compra','采购分类Categoria de compra','采购支出Gastos de Compra')}
    children = {key(k) for k in ('服务类采购','Adquisiciones de servicios','Compra De Servicios','服务类采购 Adquisiciones de servicios','服务类采购Compra De Servicios')}
    service = {key(v) for v in ('服务类采购','Compra De Servicios','服务类采购Compra De Servicios','服务商采购','Compra de proveedores','服务商采购Compra de proveedores','服务商采购Compra de servicios')}
    logistics = {key(v) for v in ('物流及运输服务','Servicios de logística y transporte','Servicios de logistica y transporte','物流及运输服务Servicios de logística y transporte','物流及运输服务Servicios de logistica y transporte')}
    normalized = [(key(label), path(value)) for label, value in fields.items() if isinstance(label, str) and key(label) in parents | children]
    if any(label in children and value and len(value) == 1 and value[0] in logistics for label, value in normalized):
        return True
    complete = False
    for label, value in normalized:
        if label not in parents:
            continue
        if not value or value[0] not in service or (len(value) == 2 and value[1] not in logistics):
            return False
        complete |= len(value) == 2
    return complete


def components(payload):
    values = decoded(payload.get('formComponentValues') or payload.get('form_component_values') or [])
    return values if isinstance(values, list) else []


def fields_of(row):
    row = decoded(row)
    if isinstance(row, list):
        return {str(c.get('name') or c.get('label') or c.get('id') or ''): decoded(c.get('value')) for c in row if isinstance(c, dict)}
    if isinstance(row, dict):
        if isinstance(row.get('rowValue'), list):
            return fields_of(row['rowValue'])
        return row
    return {}


FIELD_TRANSLATIONS = {
    '币种': ('Moneda',), '本次申请金额': ('Monto solicitado','Importe solicitado'),
    '申请金额': ('Monto solicitado','Importe solicitado'), '支付金额': ('Monto pagado','Importe de pago'),
    '总金额': ('Monto Total','Importe total'), '合计金额': ('Monto Total','Importe total'),
    '物料编码': ('Codigo','Código de material','SKU'), '编码': ('Codigo',),
    '物品名称': ('Nombre','Nombre del producto'), '货物名称': ('Nombre','Nombre de mercancía'),
    '名称': ('Nombre',), '规格': ('Especificacion','Modelo'), '数量': ('Cantidad',),
    '单位': ('Unidad',), '费用名称': ('Concepto',), '费用项目': ('Concepto',),
    '金额': ('Monto','Importe'),
}


def pick(fields, *aliases):
    for alias in aliases:
        labels = {norm(alias)}
        for translation in FIELD_TRANSLATIONS.get(alias, ()):
            labels.add(norm(alias + translation))
        for key, value in fields.items():
            if norm(key) in labels and value not in (None, ''):
                return value
    return None


def merchandise_price(fields, *, source_table='', source_position=None):
    # Only explicitly identified merchandise prices count. Generic billing rates
    # in logistics tables are never an independent purchase price.
    # Called only for positively classified cargo tables, never packing/billing.
    aliases = ('商品单价', '货物单价', '采购单价', '单价', 'Precio', 'Precio unitario',
               'Unit price', 'Precio unitario del producto', 'Merchandise unit price')
    labels = {norm(alias) for alias in aliases} | {norm(cn+es) for cn in ('单价','商品单价','采购单价')
              for es in ('Precio','Precio unitario','Precio unitario del producto')}
    entries = [(key, value) for key, value in fields.items()
               if norm(key) in labels
               and value not in (None, '')]
    if not entries:
        return {'present': False}
    price = number(entries[0][1])
    return {'present': True, 'price': price,
            'currency': currency(pick(fields, '商品币种', '采购币种', '币种', 'Moneda')),
            'price_uom': pick(fields, '商品计价单位', '单价单位', '采购单位', '单位', 'Unidad') or '',
            'ambiguous': len(entries) != 1 or price is None,
            'evidence': {'source_table': source_table, 'source_position': source_position,
                         'fields': [{'label': key, 'raw_value': value} for key, value in entries]}}


def parse_source(row, *, logistics_codes):
    payload = decoded(row.get('raw_payload')) or {}
    comps = list(components(payload) or components(row)) + list(row.get('settlement_attachment_components') or [])
    fields = {str(c.get('name') or c.get('label') or ''): decoded(c.get('value')) for c in comps}
    corp = str(row.get('corp_id') or payload.get('corpId') or '')
    instance = str(row.get('process_instance_id') or payload.get('processInstanceId') or '')
    if not corp or not instance:
        raise ValueError(_('来源缺少企业或审批实例 ID'))
    process = row.get('process_code') or payload.get('processCode')
    from .freight_lines import financial_candidate, identifiers_in
    classified = is_logistics_expense(fields) or financial_candidate(row, fields) or row.get('financial_scope') is True
    kind = 'logistics' if process in logistics_codes else 'expense' if classified else 'unclassified'
    status = str(row.get('status') or payload.get('status') or '').upper()
    result = str(row.get('result') or payload.get('result') or '').lower()
    invalid = bool(row.get('deleted_at')) or status in {'TERMINATED', 'CANCELED', 'CANCELLED', 'DELETED', 'REJECTED', 'WITHDRAWN', 'WITHDRAW', 'REVOKED'} or result in {'refuse', 'reject', 'disagree'}
    approved = not invalid and status == 'COMPLETED' and result in {'agree', 'approved', 'pass'}
    text = '\n'.join(str(v) for v in fields.values() if not isinstance(v, (dict, list)))
    identifiers = identifiers_in(text) | identifiers_in(payload.get('comments') or []) | identifiers_in(payload.get('operationRecords') or [])
    for token in re.findall(r'(?<![A-Z0-9])(?:[A-Z]{4}\d{7}|MXT\d{4,})(?![A-Z0-9])', text.upper()):
        identifiers.add(('container' if re.fullmatch(r'[A-Z]{4}\d{7}', token) else 'waybill', token))
    for key, value in fields.items():
        label = norm(key)
        if isinstance(value, (dict, list)):
            continue
        kind_id = 'waybill' if any(x in label for x in ('运单', '提单', 'waybill', 'billoflading')) else 'container' if '柜号' in label or 'container' in label else 'approval' if '物流审批' in label else ''
        if kind_id:
            for token in re.findall(r'[A-Z0-9][A-Z0-9-]{4,}', str(value).upper()):
                identifiers.add((kind_id, token))
    approval_no = str(row.get('business_id') or payload.get('businessId') or '')
    if kind == 'logistics' and approval_no:
        identifiers.add(('approval', approval_no.upper()))
    related = set()
    def walk(value):
        value = decoded(value)
        if isinstance(value, dict):
            for k, v in value.items():
                if k in {'procInstId', 'processInstanceId', 'process_instance_id'} and isinstance(v, str):
                    related.add(v)
                else:
                    walk(v)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    for c in comps:
        if 'Relate' in str(c.get('componentType') or c.get('component_type')) or '关联' in str(c.get('name')) or 'asociar' in norm(c.get('name')):
            walk(c.get('value')); walk(c.get('extValue'))
    cur = currency(pick(fields, '币种', 'Moneda'))
    amount = number(pick(fields, '本次申请金额', '申请金额', '支付金额', '总金额', 'Monto Total', '合计金额', '合计总额（元）', '合计总额', '金额'))
    fees, goods, issues = [], [], []
    goods_table_validity = []
    fee_issues = list(row.get('settlement_fee_issues') or [])
    billing = []
    for c in comps:
        table = decoded(c.get('value'))
        if str(c.get('componentType') or c.get('component_type')) != 'TableField':
            continue
        name = str(c.get('name') or '')
        goods_table = any(x in norm(name) for x in ('货物明细', '货物信息', '运输货物', 'listademercancias', 'goodsdetails'))
        fee_table = any(x in norm(name) for x in ('费用明细', '付款明细', '物流费用', 'desglosedegastos', '支出明细'))
        if not (goods_table or fee_table):
            billing.append({'source_table': name, 'raw': table})
            continue
        if not isinstance(table, list):
            issues.append(f'{name}明细不完整')
            if fee_table:
                fee_issues.append(f'{name}未完整解析')
            if goods_table:
                goods_table_validity.append(False)
            continue
        valid = True
        seen = {}
        for index, raw in enumerate(table):
            f = fields_of(raw)
            item = {'source_table': name, 'source_position': index + 1, 'raw': f}
            if goods_table:
                item.update(material_code=pick(f, '物料编码', '编码', 'Codigo', 'SKU') or '', product_name=pick(f, '物品名称', '货物名称', '名称', 'Nombre') or '', spec_model=pick(f, '规格', 'Especificacion') or '', quantity=number(pick(f, '数量', 'Cantidad')), unit=pick(f, '单位', 'Unidad') or '')
                item['merchandise_price'] = merchandise_price(f, source_table=name, source_position=index+1)
                from .document_writer import PHYSICAL_ALIASES
                item['physical'] = {field:number(pick(f,*aliases)) for field,aliases in PHYSICAL_ALIASES.items()
                                    if field != 'actual_shipped_qty' and pick(f,*aliases) is not None}
                valid &= bool((item['material_code'] or item['product_name']) and item['quantity'] is not None and item['unit'])
                signature = digest(name, item['material_code'], item['product_name'], item['spec_model'], item['unit'])
            else:
                item.update(label=pick(f, '费用名称', '费用项目', '名称', 'Concepto') or '', amount=number(pick(f, '金额', '总金额', 'Monto', 'Importe')), currency=currency(pick(f, '币种', 'Moneda') or cur))
                valid &= bool(item['amount'] is not None and item['currency'])
                signature = digest(name, item['label'], item['currency'])
            native_id = raw.get('rowId') if isinstance(raw, dict) else None
            item['line_key'] = digest(name, str(native_id)) if native_id else signature
            seen[item['line_key']] = seen.get(item['line_key'], 0) + 1
            item['native_id'] = bool(native_id)
            (goods if goods_table else fees).append(item)
        if any(n > 1 for n in seen.values()):
            issues.append('明细行身份重复，需逐行确认')
            valid = False
        if goods_table:
            goods_table_validity.append(valid and bool(table))
        if not valid or not table:
            issues.append(f'{name}明细不完整')
            if fee_table:
                fee_issues.append(f'{name}未完整解析或行身份不唯一')
    if fees and (any(f['amount'] is None for f in fees) or (amount is not None and sum(Decimal(f['amount'] or '0') for f in fees) != Decimal(amount))):
        issues.append('费用明细与总额未核对一致')
    if any(Decimal(f['amount'] or '0') < 0 for f in fees) or (amount is not None and Decimal(amount) < 0):
        issues.append('负数或冲抵费用待核对')
    scope_text = norm(text + ' ' + ' '.join(str(f.get('label') or '') for f in fees) + ' ' + dumps(billing))
    other_scope_labels = ('双清','包税','清关','税费','关税','内陆','境内运输','陆运','ddp','despacho','aduana','impuesto','arancel','inland','fletelocal')
    coverage = 'unknown' if any(norm(label) in scope_text for label in other_scope_labels) else 'freight'
    normalized = {'id': digest(corp, instance), 'corp': corp, 'instance': instance, 'process_code': process, 'approval_no': approval_no, 'kind': kind, 'status': status, 'approval_result': result, 'deleted_at': row.get('deleted_at'), 'approved': approved, 'invalid': invalid, 'source_updated_at': timestamp(row.get('updated_at')), 'identifiers': sorted(identifiers), 'related': sorted(related), 'amount': amount, 'currency': cur, 'fees': fees, 'goods': goods, 'billing': billing, 'goods_complete': bool(goods_table_validity) and all(goods_table_validity), 'issues': issues, 'fee_issues': fee_issues, 'coverage': coverage, 'fields': fields, 'raw': payload, 'archive_revision': row.get('archive_revision'), 'documents': row.get('settlement_documents') or [], 'attachments': row.get('attachments') or [], 'parser_version': PARSER_VERSION}
    normalized['identifiers']=sorted(set(map(tuple,normalized['identifiers'])) | {('material',code) for g in goods for code in re.findall(r'[A-Z]{1,8}\d[A-Z0-9_-]*',str(g.get('material_code') or '').upper())})
    normalized['title'] = row.get('title') or payload.get('title') or row.get('process_name') or row.get('template_name') or ''
    attachments = sorted((attachment_identity(a) for a in normalized['attachments']), key=dumps)
    scalar_fields = {key: value for key, value in fields.items() if not isinstance(value, (dict, list))}
    normalized['match_hash'] = digest(kind, corp, normalized['identifiers'], normalized['related'])
    normalized['cost_hash'] = digest(approved, invalid, normalized['title'], approval_no, scalar_fields, stable_lines(fees), stable_lines(goods), fee_issues, billing, attachments, normalized['goods_complete'], PARSER_VERSION)
    normalized['packing_hash'] = digest(payload.get('operationRecords'), payload.get('comments'), attachments)
    normalized['fingerprint'] = digest(kind, status, result, invalid, payload, attachments, normalized['documents'], normalized['title'], approval_no, process, PARSER_VERSION)
    return normalized
