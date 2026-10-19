(function (globalThis) {
"use strict";

// Source: 00-data.js
function ocTranslate(text, args = []) {
  if (typeof globalThis.__ === "function") return globalThis.__(text, args);
  return String(text).replace(/\{(\d+)\}/g, (_, index) => args[index] ?? "");
}

const headers = [
  ocTranslate("对应钉钉采购订单号"), ocTranslate("品目编码Item code"), ocTranslate("品牌"), ocTranslate("申报名称"), ocTranslate("申报单位"), ocTranslate("供应商supplier"),
  ocTranslate("中文品名 Chinese Name"), ocTranslate("是否开票/CI"), ocTranslate("海关编码"), ocTranslate("英文品名"), ocTranslate("西语品名"), ocTranslate("规格型号品牌"),
  ocTranslate("长m"), ocTranslate("宽m"), ocTranslate("高m"), ocTranslate("净重NW/件kg"), ocTranslate("毛重GW/件kg"), ocTranslate("单件CBM"), ocTranslate("个数(每件)"), ocTranslate("件数"),
  ocTranslate("总个数"), ocTranslate("包装"), ocTranslate("总净重"), ocTranslate("总毛重Gross weight"), ocTranslate("总体积total capacity"), ocTranslate("单价unit price"), ocTranslate("总价（RMB)"),
  ocTranslate("计划出货日期"), ocTranslate("备注Remarks"), ocTranslate("项目归属")
];
const displayColumns = [0, 1, 6, 11, 20, 23, 24, 25, 26, 2, 3, 4, 5, 7, 8, 9, 10, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 27, 28, 29];
const numericColumns = new Set([20, 23, 24, 25, 26]);
const headerAliases = [
  ["对应钉钉采购订单号", "对应钉钉采购订单", "钉钉采购订单号", "采购订单号", "采购订单", "订单号", "dingtalk purchase order no", "dingtalk po", "purchase order no", "purchase order", "order no", "po no", "po"],
  ["品目编码Item code", "品目编码", "品项编码", "物料编码", "item code", "itemcode", "item no", "material code", "料号", "货号"],
  ["品牌", "brand"],
  ["申报名称", "报关名称", "申报品名", "申报货物名称", "declared name", "declaration name"],
  ["申报单位", "单位", "申报计量单位", "declared unit", "unit"],
  ["供应商supplier", "供应商", "供货商", "supplier", "vendor"],
  ["中文品名 Chinese Name", "中文品名", "中文名称", "品名中文", "chinese name", "chinese product name"],
  ["是否开票/CI", "是否开票", "开票", "发票", "ci", "invoice"],
  ["海关编码", "HS编码", "HS code", "海关号", "hscode", "tariff code"],
  ["英文品名", "英文名称", "品名英文", "english name"],
  ["西语品名", "西班牙语品名", "西语名称", "spanish name"],
  ["规格型号品牌", "规格型号", "规格", "型号", "specification", "model"],
  ["长m", "长度", "长", "length", "length m"],
  ["宽m", "宽度", "宽", "width", "width m"],
  ["高m", "高度", "高", "height", "height m"],
  ["净重NW/件kg", "净重/件", "净重NW件kg", "NW/件", "NW per piece", "net weight per piece", "nw per piece"],
  ["毛重GW/件kg", "毛重/件", "毛重GW件kg", "GW/件", "GW per piece", "gross weight per piece", "gw per piece"],
  ["单件CBM", "单件体积", "单件容积", "CBM/件", "unit CBM", "unit volume", "cbm per piece"],
  ["个数(每件)", "每件个数", "个数/件", "个数每件", "pieces per package", "qty per piece"],
  ["件数", "箱数", "包装件数", "packages", "cartons"],
  ["总个数", "总数量", "发货数量", "数量", "总件数", "total quantity", "total qty", "quantity", "qty", "shipment quantity", "shipping quantity"],
  ["包装", "包装方式", "包装材料", "packaging"],
  ["总净重", "净重", "净重合计", "总重量（净重）", "total net weight", "total netweight", "net weight total", "total nw"],
  ["总毛重Gross weight", "总毛重", "毛重", "毛重合计", "总重量", "重量合计", "重量kg", "重量", "毛重kg", "毛重(kg)", "总毛重kg", "总毛重(kg)", "weight kg", "weight (kg)", "weight", "gross wt", "Gross weight", "grossweight", "total gross weight", "total gross wt", "total grossweight", "gross weight total", "gross wt kg", "total gw", "gw"],
  ["总体积total capacity", "总体积", "体积", "总容积", "总体积m3", "总体积(CBM)", "体积(CBM)", "总立方数", "体积合计", "CBM总量", "total CBM", "total cbm", "CBM", "m3", "m³", "cubic meter", "cubic meters", "total capacity", "total volume", "totalvolume", "volume total", "volume cbm", "volume"],
  ["单价unit price", "单价", "申报价单价", "unit price", "unitprice", "unit cost", "price"],
  ["总价（RMB)", "总价", "总金额", "申报货值", "货值", "RMB总价", "total price", "totalprice", "total amount", "amount"],
  ["计划出货日期", "计划发货日期", "出货日期", "发货日期", "预计出货日期", "planned shipping date"],
  ["备注Remarks", "备注", "说明", "remarks", "remark", "note"],
  ["项目归属", "所属项目", "归属项目", "项目归属", "project ownership", "project"]
];
const traditionalToSimplified = {
  "訂": "订", "購": "购", "單": "单", "號": "号", "編": "编", "碼": "码", "品": "品", "目": "目",
  "申": "申", "報": "报", "稱": "称", "單": "单", "位": "位", "供": "供", "應": "应", "商": "商",
  "開": "开", "票": "票", "關": "关", "稅": "税", "英": "英", "語": "语", "規": "规", "格": "格",
  "型": "型", "長": "长", "寬": "宽", "高": "高", "淨": "净", "重": "重", "總": "总", "體": "体",
  "積": "积", "價": "价", "計": "计", "畫": "划", "貨": "货", "期": "期", "備": "备", "註": "注",
  "專": "专", "案": "案", "歸": "归", "屬": "属", "發": "发", "運": "运", "數": "数", "量": "量",
  "個": "个", "每": "每", "件": "件", "裝": "装", "箱": "箱", "總": "总"
};
const sampleRow = [
  "202510231037000496405", "FL002788", "", "", "PZ", "", "", "是", "", "", "", "",
  "", "", "", "0.0101", "0.01022", "0.0000214", "1", "50400", "50400", "纸箱", "509.6", "515.2",
  "1.0805676", "1.0449", "366237.45", "2026-02-06", "", "OEM IML"
];
const defaults = {
  oceanRate: 7.01, airRateFx: 6.9236, tariffRate: 0, vatRate: 16, airKgRate: 40,
  airFixedFee: 2000, clearanceMxn: 19500, mxnToCny: 2.6, deliveryFee: 600, oceanPortFee: 3482.575,
  oceanContainerFee: "", oceanContainerWeight: "", oceanContainerVolume: ""
};
const currencyDefaults = {
  declaredCurrency: "USD", oceanPortFeeCurrency: "CNY", airKgRateCurrency: "CNY",
  airFixedFeeCurrency: "CNY", clearanceCurrency: "MXN", deliveryFeeCurrency: "CNY",
  oceanContainerFeeCurrency: "CNY"
};

// Source: 10-calculator.js
const parameterLabels = {oceanRate:'海运美元汇率', airRateFx:'空运美元汇率', tariffRate:'关税率', vatRate:'增值税率', airKgRate:'空运公斤单价', airFixedFee:'空运固定费', clearanceMxn:'清关杂费', mxnToCny:'比索汇率', deliveryFee:'目的地拖车费', oceanPortFee:'海运本地费用'};
const blank = value => value === null || value === undefined || String(value).trim() === '';
const clone = value => JSON.parse(JSON.stringify(value));
const emptyRow = () => Array(30).fill('');
const normalizeRow = row => Array.from({length:30},(_,i)=>row && row[i] != null ? String(row[i]) : '');
const normalizeUnit = value => !String(value || '').trim() || /^(PZ|PCS|PC|PIECES|PIECE|个|件)$/i.test(String(value).trim()) ? '件' : String(value).trim();
const money = value => value != null && Number.isFinite(Number(value)) ? Number(value).toLocaleString(globalThis.frappe?.boot?.lang || 'zh-CN',{style:'currency',currency:'CNY',minimumFractionDigits:2,maximumFractionDigits:2}) : ocTranslate('待补充');
const fixed = (value,digits) => value != null && Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : ocTranslate('待补充');
const quantityText = value => fixed(value,6).replace(/\.?0+$/, '');
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g,character=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));

// Anchored parsing preserves exponents and rejects partial/malformed numbers.
function parseNumber(raw) {
  if (blank(raw) || typeof raw === 'boolean') return null;
  const input=String(raw).trim().replace(/，/g,',').replace(/\u00a0/g,' ');
  if(input.length>128)return null;
  const match=input.match(/^(?:(?:USD|CNY|RMB|MXN|US\$|CN¥|MX\$|[¥￥$])\s*)?([+-]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)(?:\s*(?:USD|CNY|RMB|MXN|kg|kgs|KG|m3|m³|CBM|件|个|PZ|PCS|%))?$/i);
  if (!match) return null;
  const numeric=match[1].replace(/,/g,'');
  const [coefficient,exponent]=numeric.split(/[eE]/);
  if(coefficient.replace(/\D/g,'').length>30 || exponent!==undefined && Math.abs(Number(exponent))>30)return null;
  const value=Number(numeric);
  return Number.isFinite(value) && Math.abs(value)<=1e15 ? value : null;
}
function toCny(value,currency,usd,mxn) { return currency==='USD' ? value*usd : currency==='MXN' ? value/mxn : value; }
function calculate(payload = {}) {
  const issues=[];
  const issue=message=>{if(!issues.includes(message))issues.push(message);};
  const rawParams={...defaults,...(payload.parameters||{})};
  const currencies={...currencyDefaults,...(payload.currencies||{})};
  const parameters=Object.fromEntries(Object.entries(rawParams).map(([id,value])=>[id,parseNumber(value)]));
  const check=(value,label,required=false)=>{
    if(blank(value)){if(required)issue(ocTranslate("{0}待补充", [ocTranslate(label)]));return null;}
    const n=parseNumber(value);if(n===null||n<0){issue(ocTranslate("{0}须为非负有效数字", [ocTranslate(label)]));return null;}return n;
  };
  ['tariffRate','vatRate','airKgRate','airFixedFee','clearanceMxn','deliveryFee','oceanPortFee'].forEach(id=>check(rawParams[id],parameterLabels[id],true));
  const activeCurrencies=Object.keys(currencyDefaults).filter(id=>id!=='oceanContainerFeeCurrency');
  activeCurrencies.forEach(id=>{if(!['CNY','USD','MXN'].includes(currencies[id]))issue(ocTranslate('费用币种须为 CNY、USD 或 MXN'));});
  const oceanUses=['declaredCurrency','oceanPortFeeCurrency','clearanceCurrency','deliveryFeeCurrency'];
  const airUses=['declaredCurrency','airKgRateCurrency','airFixedFeeCurrency','clearanceCurrency','deliveryFeeCurrency'];
  [[oceanUses.some(id=>currencies[id]==='USD'),'oceanRate'],[airUses.some(id=>currencies[id]==='USD'),'airRateFx'],[activeCurrencies.some(id=>currencies[id]==='MXN'),'mxnToCny']].forEach(([used,id])=>{
    if(used && !(parameters[id]>0))issue(ocTranslate("{0}须为大于 0 的有效数字", [ocTranslate(parameterLabels[id])]));
  });
  const rows=(Array.isArray(payload.rows)?payload.rows:[]).map(normalizeRow).filter(row=>row.some(value=>!blank(value)));
  const overrides=payload.totals_override||{};
  const grossOverride=check(overrides.grossWeight,'整票总毛重');
  const volumeOverride=check(overrides.volume,'整票总体积');
  let quantity=0,grossWeight=0,volume=0,declaredValue=0;
  let quantityComplete=true,grossComplete=true,volumeComplete=true,declaredComplete=true;
  const units=new Set();
  rows.forEach((row,index)=>{
    const prefix=ocTranslate("第 {0} 行", [index+1]);
    const values={};
    [12,13,14,15,16,17,18,19,20,22,23,24,25,26].forEach(column=>{values[column]=check(row[column],`${prefix}${headers[column]}`,column===20);});
    const qty=values[20];if(qty===null)quantityComplete=false;else quantity+=qty;
    if(qty>0)units.add(normalizeUnit(row[4]));
    const gross=!blank(row[23])?values[23]:values[16]!==null&&qty!==null?values[16]*qty:null;
    const vol=!blank(row[24])?values[24]:values[17]!==null&&qty!==null?values[17]*qty:null;
    const declared=!blank(row[25])?values[25]!==null&&qty!==null?values[25]*qty:null:values[26];
    if(qty!==0){
      if(gross===null){grossComplete=false;if(grossOverride===null)issue(ocTranslate("{0}总毛重待补充", [prefix]));}else grossWeight+=gross;
      if(vol===null)volumeComplete=false;else volume+=vol;
      if(declared===null){declaredComplete=false;issue(ocTranslate("{0}申报单价或总价待补充", [prefix]));}else declaredValue+=declared;
    }
  });
  if(!(quantity>0))issue(ocTranslate('核算总数量须大于 0'));
  if(grossOverride!==null)grossWeight=grossOverride;
  else if(!grossComplete)grossWeight=null;
  if(!(grossWeight>0))issue(ocTranslate('整票总毛重须大于 0'));
  if(volumeOverride!==null)volume=volumeOverride;
  else if(!volumeComplete||!rows.length)volume=null;
  if(!quantityComplete)quantity=null;
  if(!declaredComplete||!rows.length)declaredValue=null;
  const mixedUnits=units.size>1;
  const unit=mixedUnits?'件':([...units][0]||'件');
  const declaredUnitPrice=quantity>0&&declaredValue!==null?declaredValue/quantity:null;
  const v={quantity,grossWeight,volume,declaredValue,declaredUnitPrice,...parameters,...currencies};
  const fields=['oceanPortFee','airKgRate','airFixedFee','oceanDeclared','airDeclared','oceanCif','airTransport','airCif','oceanTariff','airTariff','oceanVat','airVat','oceanClearance','airClearance','oceanDelivery','airDelivery','oceanDta','airDta','oceanImport','airImport','oceanTotal','airTotal','oceanAvg','airAvg'];
  const r={v,status:issues.length?'Draft':'Ready',issues,unit,mixedUnits,...Object.fromEntries(fields.map(key=>[key,null]))};
  if(issues.length)return r;
  r.oceanPortFee=toCny(v.oceanPortFee,v.oceanPortFeeCurrency,v.oceanRate,v.mxnToCny);
  r.airKgRate=toCny(v.airKgRate,v.airKgRateCurrency,v.airRateFx,v.mxnToCny);
  r.airFixedFee=toCny(v.airFixedFee,v.airFixedFeeCurrency,v.airRateFx,v.mxnToCny);
  r.airTransport=r.airKgRate*v.grossWeight+r.airFixedFee;
  for(const mode of ['ocean','air']){
    const fx=mode==='ocean'?v.oceanRate:v.airRateFx;
    r[mode+'Declared']=toCny(v.declaredValue,v.declaredCurrency,fx,v.mxnToCny);
    const transport=mode==='ocean'?r.oceanPortFee:r.airTransport;
    const cif=r[mode+'Cif']=transport+r[mode+'Declared'];
    r[mode+'Tariff']=cif*v.tariffRate/100;
    r[mode+'Vat']=(cif+r[mode+'Tariff'])*v.vatRate/100;
    r[mode+'Clearance']=toCny(v.clearanceMxn,v.clearanceCurrency,fx,v.mxnToCny);
    r[mode+'Delivery']=toCny(v.deliveryFee,v.deliveryFeeCurrency,fx,v.mxnToCny);
    r[mode+'Dta']=cif*.0008;
    r[mode+'Import']=['Tariff','Vat','Clearance','Delivery','Dta'].reduce((sum,key)=>sum+r[mode+key],0);
    r[mode+'Total']=transport+r[mode+'Import'];
    r[mode+'Avg']=mixedUnits?null:r[mode+'Total']/v.quantity;
  }
  if(fields.some(key=>r[key]!==null&&!Number.isFinite(r[key]))){
    r.status='Draft';r.issues.push(ocTranslate('数值超出计算范围，请检查输入'));fields.forEach(key=>r[key]=null);
  }
  return r;
}

// Source: 20-paste.js
function pastedHtmlRows(html) {
  if (!html || typeof DOMParser === "undefined") return [];
  const documentFragment = new DOMParser().parseFromString(html, "text/html");
  const table = documentFragment.querySelector("table");
  if (!table) return [];
  return [...table.rows]
    .map((tableRow) => [...tableRow.cells].map((cell) => {
      const clone = cell.cloneNode(true);
      clone.querySelectorAll("br").forEach((breakNode) => breakNode.replaceWith("\n"));
      return clone.textContent.replace(/\u00a0/g, " ").trim();
    }))
    .filter((row) => row.some(Boolean));
}

function pastedRows(text, html = "") {
  const htmlRows = pastedHtmlRows(html);
  if (htmlRows.length) return htmlRows;

  const rows = [];
  let row = [];
  let cell = "";
  let quoted = false;
  let quoteClosed = false;
  const input = String(text || "").replace(/^\uFEFF/, "");

  for (let index = 0; index < input.length; index += 1) {
    const character = input[index];
    const nextCharacter = input[index + 1];

    if (quoted) {
      if (character === '"' && nextCharacter === '"') {
        cell += '"';
        index += 1;
      } else if (character === '"') {
        quoted = false;
        quoteClosed = true;
      } else {
        cell += character;
      }
      continue;
    }

    if (character === '"' && !quoteClosed && cell.trim() === "") {
      quoted = true;
      cell = "";
      continue;
    }

    if (character === "\t") {
      row.push(cell.trim());
      cell = "";
      quoteClosed = false;
      continue;
    }

    if (character === "\r" || character === "\n" || character === "\u2028" || character === "\u2029") {
      if (character === "\r" && nextCharacter === "\n") index += 1;
      row.push(cell.trim());
      if (row.some(Boolean)) rows.push(row);
      row = [];
      cell = "";
      quoteClosed = false;
      continue;
    }

    cell += character;
  }

  if (cell || row.length) {
    row.push(cell.trim());
    if (row.some(Boolean)) rows.push(row);
  }
  return rows;
}

function normalized(value) {
  const halfWidth = String(value || "").replace(/[\uFF01-\uFF5E]/g, (character) => String.fromCharCode(character.charCodeAt(0) - 0xfee0));
  return [...halfWidth.toLowerCase()]
    .map((character) => traditionalToSimplified[character] || character)
    .join("")
    .replace(/m³/g, "m3")
    .replace(/[^a-z0-9\u3400-\u9fff]+/g, "");
}

function headerMatchScore(value, index) {
  const candidate = normalized(value);
  if (!candidate) return 0;
  const hasTotalMarker = /总|合计|total|totals|sum/.test(candidate);
  const hasPerPieceMarker = /每件|单件|件|perpiece|perunit|unit|package|carton/.test(candidate);
  const isPerPieceWeightField = index === 15 || index === 16;
  const isTotalWeightField = index === 22 || index === 23;
  const isUnitVolumeField = index === 17;
  const isTotalVolumeField = index === 24;
  return Math.max(...headerAliases[index].map((alias) => {
    const normalizedAlias = normalized(alias);
    if (!normalizedAlias) return 0;
    let score = 0;
    if (candidate === normalizedAlias) score = 100 + normalizedAlias.length / 100;
    else if (candidate.includes(normalizedAlias)) score = 80 + normalizedAlias.length / 100;
    else if (normalizedAlias.includes(candidate) && candidate.length >= 2) score = 60 + candidate.length / 100;
    if (!score) return 0;
    if (isPerPieceWeightField) score += hasPerPieceMarker ? 18 : -18;
    if (isTotalWeightField) score += hasTotalMarker ? 18 : (hasPerPieceMarker ? -24 : 0);
    if (isUnitVolumeField) score += hasPerPieceMarker ? 18 : -18;
    if (isTotalVolumeField) score += hasTotalMarker ? 18 : (hasPerPieceMarker ? -24 : 0);
    return score;
  }));
}

function findHeaderMap(row) {
  const pairs = [];
  row.forEach((cell, sourceIndex) => {
    headers.forEach((_, targetIndex) => {
      const score = headerMatchScore(cell, targetIndex);
      if (score > 0) pairs.push({ sourceIndex, targetIndex, score });
    });
  });
  pairs.sort((left, right) => right.score - left.score || left.targetIndex - right.targetIndex || left.sourceIndex - right.sourceIndex);
  const usedSources = new Set();
  const usedTargets = new Set();
  const byTarget = Array(headers.length).fill(-1);
  pairs.forEach(({ sourceIndex, targetIndex, score }) => {
    if (score < 60 || usedSources.has(sourceIndex) || usedTargets.has(targetIndex)) return;
    usedSources.add(sourceIndex);
    usedTargets.add(targetIndex);
    byTarget[targetIndex] = sourceIndex;
  });
  return usedTargets.size >= 2 ? { byTarget, matchedTargets: [...usedTargets] } : null;
}

function findHeaderInfo(rows) {
  const maxHeaderRows = Math.min(rows.length, 6);
  for (let rowIndex = 0; rowIndex < maxHeaderRows; rowIndex += 1) {
    const map = findHeaderMap(rows[rowIndex]);
    if (map) return { map, rowIndex };
  }
  return null;
}

function parsePaste(text,html='',startColumn=0) {
  const raw=pastedRows(text,html);const info=findHeaderInfo(raw);
  const data=info?raw.slice(info.rowIndex+1):raw;
  const original=!info&&startColumn===0&&data.every(row=>row.length>=30);
  const startIndex=Math.max(0,displayColumns.indexOf(startColumn));
  const columns=info?info.map.matchedTargets:original?Array.from({length:30},(_,i)=>i):displayColumns.slice(startIndex,startIndex+Math.max(0,...data.map(row=>row.length)));
  const entries=data.map(row=>{
    const full=emptyRow();
    // Each row keeps its own footprint: omitted cells must not erase existing data.
    const rowColumns=columns.filter((column,index)=>(info?info.map.byTarget[column]:index)<row.length);
    rowColumns.forEach(column=>{const sourceIndex=info?info.map.byTarget[column]:columns.indexOf(column);full[column]=row[sourceIndex]||'';});
    return {row:full,columns:rowColumns};
  }).filter(entry=>entry.row.some(value=>!blank(value)));
  return {rows:entries.map(entry=>entry.row),rowColumns:entries.map(entry=>entry.columns),columns,headerRow:info?info.rowIndex+1:null,matched:columns.length,missing:info?[20,23,25].filter(column=>!columns.includes(column)&&!(column===23&&columns.includes(16))&&!(column===25&&columns.includes(26))):[]};
}

function applyPaste(currentRows,text,html='',startRow=0,startColumn=0) {
  const parsed=parsePaste(text,html,startColumn);const rows=currentRows.map(normalizeRow);
  parsed.rows.forEach((row,offset)=>{
    while(rows.length<=startRow+offset)rows.push(emptyRow());
    parsed.rowColumns[offset].forEach(column=>rows[startRow+offset][column]=row[column]);
  });
  if(!rows.length||rows[rows.length-1].some(value=>!blank(value)))rows.push(emptyRow());
  return {...parsed,rows,count:parsed.rows.length};
}

// Source: 30-record-session.js
function newPayload() { return {rows:[emptyRow()],parameters:clone(defaults),currencies:clone(currencyDefaults),totals_override:{grossWeight:null,volume:null},source:null}; }
function uuid() {
  if(globalThis.crypto && globalThis.crypto.randomUUID)return globalThis.crypto.randomUUID();
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g,c=>{const r=Math.random()*16|0;return(c==='x'?r:(r&3|8)).toString(16);});
}
// Compare the submitted and acknowledged data, ignoring harmless server normalization.
function canonicalPayload(payload) {
  const values=(input,reference)=>Object.fromEntries(Object.entries(reference).map(([key,value])=>[key,String(Object.hasOwn(input||{},key)?input[key]??'':value)]));
  const normalized={
    rows:(payload.rows||[]).map(normalizeRow).filter(row=>row.some(value=>!blank(value))),
    parameters:values(payload.parameters,defaults),currencies:values(payload.currencies,currencyDefaults),
    totals_override:Object.fromEntries(['grossWeight','volume'].map(key=>[key,blank(payload.totals_override?.[key])?null:String(payload.totals_override[key])])),
    source:payload.source||null
  };
  const stable=value=>Array.isArray(value)?value.map(stable):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,stable(value[key])])):value;
  return JSON.stringify(stable(normalized));
}
class RecordSession {
  constructor(){this.payload=newPayload();this.title='';this.name=null;this.modified=null;this.dirty=false;this.saving=false;this.revision=0;this.generation=0;this.pendingSave=null;this.result=null;}
  touch(){this.dirty=true;this.revision+=1;this.result=null;}
  apply(record,dirty=false){
    this.generation+=1;this.revision+=1;this.pendingSave=null;
    this.payload=clone(record.payload||newPayload());
    this.payload.rows=(this.payload.rows||[]).map(normalizeRow);
    this.payload.parameters={...defaults,...this.payload.parameters};this.payload.currencies={...currencyDefaults,...this.payload.currencies};
    this.payload.source=this.payload.source||null;this.payload.totals_override=this.payload.totals_override||{grossWeight:null,volume:null};
    if(!this.payload.rows.length||this.payload.rows.at(-1).some(value=>!blank(value)))this.payload.rows.push(emptyRow());
    this.title=record.title||'';this.name=record.name||null;this.modified=record.modified||null;this.result=record.result||null;this.dirty=dirty;
  }
  snapshot(){const payload=clone(this.payload);payload.rows=payload.rows.filter(row=>row.some(value=>!blank(value)));return payload;}
  async save(call){
    if(this.saving)return null;
    if(!this.title.trim())throw new Error(ocTranslate("请填写记录名称。"));
    const payload_json=JSON.stringify(this.snapshot());const title=this.title.trim();
    const signature=JSON.stringify([title,payload_json,this.name,this.modified]);
    if(!this.pendingSave||this.pendingSave.signature!==signature)this.pendingSave={signature,request_id:uuid()};
    const request_id=this.pendingSave.request_id;const revision=this.revision,generation=this.generation;
    this.saving=true;
    try{
      const response=await call('save_record',{title,payload_json,name:this.name,modified:this.modified,request_id});
      if(!response||!response.ok||!response.record)throw new Error(ocTranslate("保存失败，请重试。"));
      if(this.generation!==generation)return null;
      if(response.record.payload && (canonicalPayload(response.record.payload)!==canonicalPayload(JSON.parse(payload_json)) || response.record.title!=null && response.record.title.trim()!==title)){
        this.result=null;this.dirty=true;
        throw new Error(ocTranslate("保存返回的数据与本次提交不一致，当前编辑已保留。请重新读取团队记录核对后再保存。"));
      }
      this.name=response.record.name;this.modified=response.record.modified;this.pendingSave=null;
      if(this.revision===revision){this.result=response.record.result||calculate(this.payload);this.dirty=false;}
      return response;
    } finally {this.saving=false;}
  }
}

const PAGE_TEMPLATE = "<main class=\"shell\">\n\n    <section class=\"section\" aria-label=\"{%= __('装箱单明细与基础数据录入') %}\">\n      <div class=\"section-body\">\n        <div class=\"packing-toolbar\">\n          <div>\n            <h3>{%= __(\"装箱单明细\") %}</h3>\n            <p>{%= __(\"表头顺序与“空运成本”一致；点击起始单元格后可直接粘贴 Excel 行或多行。\") %}</p>\n          </div>\n          <div class=\"packing-actions\"><button class=\"btn ghost\" data-field=\"importBatch\" type=\"button\">{%= __(\"从批次导入\") %}</button><button class=\"btn ghost\" data-field=\"newRecord\" type=\"button\">{%= __(\"新建\") %}</button>\n            <button class=\"btn ghost\" data-field=\"loadSample\" type=\"button\">{%= __(\"载入样例\") %}</button>\n            <button class=\"btn primary\" data-field=\"recalculate\" type=\"button\">{%= __(\"重新计算成本\") %}</button>\n            <button class=\"btn\" data-field=\"resetAll\" type=\"button\">{%= __(\"重置参数\") %}</button>\n            <button class=\"btn ghost\" data-field=\"saveRecord\" type=\"button\">{%= __(\"保存到团队\") %}</button>\n            <button class=\"btn ghost\" data-field=\"loadRecord\" type=\"button\">{%= __(\"团队记录\") %}</button>\n          </div>\n        </div>\n        <div class=\"grid-status\" data-field=\"gridStatus\">{%= __(\"默认保留 1 行空白，直接编辑或粘贴数据。\") %}</div>\n        <div class=\"record-name-row\"><label>{%= __(\"记录名称\") %}<input data-field=\"recordName\" type=\"text\" maxlength=\"140\" placeholder=\"{%= __('填写记录名称，可保存未完成的草稿') %}\"></label><span class=\"pill blue\" data-field=\"saveState\">{%= __(\"未保存\") %}</span></div>\n        <div class=\"source-note\" data-field=\"sourceNote\" hidden></div>\n        <section class=\"record-panel\" data-field=\"browserPanel\" hidden aria-label=\"{%= __('团队记录与批次选择') %}\">\n          <div class=\"record-panel-head\"><h3 data-field=\"browserTitle\">{%= __(\"团队记录\") %}</h3><button class=\"btn\" data-action=\"close-browser\" type=\"button\">{%= __(\"关闭\") %}</button></div>\n          <div class=\"record-name-row\"><input data-field=\"searchKeyword\" placeholder=\"{%= __('搜索批次、订单号或记录名称') %}\" aria-label=\"{%= __('搜索关键词') %}\"><button class=\"btn\" data-action=\"search\" type=\"button\">{%= __(\"搜索\") %}</button></div>\n          <div data-field=\"browserStatus\" role=\"status\"></div><div class=\"record-list\" data-field=\"browserList\"></div>\n          <div class=\"pager\"><button class=\"btn\" data-action=\"previous\" type=\"button\">{%= __(\"上一页\") %}</button><span data-field=\"pageInfo\"></span><button class=\"btn\" data-action=\"next\" type=\"button\">{%= __(\"下一页\") %}</button></div>\n        </section>\n        <section class=\"record-panel\" data-field=\"previewPanel\" hidden aria-label=\"{%= __('批次导入预览') %}\"></section>\n        <div class=\"empty-data-hint\" data-field=\"emptyDataHint\">{%= __(\"请先录入或粘贴装箱单数据\") %}</div>\n        <div class=\"packing-entry\">\n          <div class=\"packing-table-scroll\">\n            <table class=\"packing-table\" aria-label=\"{%= __('装箱单明细录入表') %}\">\n              <colgroup data-field=\"packingColgroup\"></colgroup>\n              <thead><tr data-field=\"packingHeaders\"></tr></thead>\n              <tbody data-field=\"packingRows\"></tbody>\n            </table>\n          </div>\n        </div>\n        <div class=\"override-panel\"><label class=\"override-toggle\"><input type=\"checkbox\" data-field=\"overrideEnabled\">{%= __(\"使用整票毛重 / 体积\") %}</label><div data-field=\"overrideFields\" hidden><div class=\"override-grid\"><label>{%= __(\"整票总毛重（kg）\") %}<input data-field=\"grossWeightOverride\" inputmode=\"decimal\" placeholder=\"{%= __('留空按明细汇总') %}\"></label><label>{%= __(\"整票总体积（m³）\") %}<input data-field=\"volumeOverride\" inputmode=\"decimal\" placeholder=\"{%= __('留空按明细汇总') %}\"></label></div><small>{%= __(\"仅覆盖当前测算的整票汇总，不向每行重复分配；留空时由明细合计。体积不参与当前运费公式。\") %}</small></div></div>\n        <div class=\"summary-head\">{%= __(\"解析汇总\") %}</div>\n        <div class=\"summary-strip\" aria-label=\"{%= __('装箱单核心汇总') %}\">\n          <div class=\"summary-item\"><div class=\"summary-label\">{%= __(\"核算数量\") %}</div><div class=\"summary-value\" data-field=\"summaryQuantity\">0 <small>{%= __(\"件\") %}</small></div></div>\n          <div class=\"summary-item\"><div class=\"summary-label\">{%= __(\"总毛重\") %}</div><div class=\"summary-value\" data-field=\"summaryGross\">0.000 <small>kg</small></div></div>\n          <div class=\"summary-item\"><div class=\"summary-label\">{%= __(\"总体积\") %}</div><div class=\"summary-value\" data-field=\"summaryVolume\">0.000000 <small>m³</small></div></div>\n          <div class=\"summary-item\"><div class=\"summary-label\">{%= __(\"申报单价\") %}</div><div class=\"summary-value-row\"><div class=\"summary-value\" data-field=\"summaryUnitPrice\">0.000000 <small>USD</small></div><select class=\"summary-currency-select\" data-field=\"declaredCurrency\" aria-label=\"{%= __('申报单价币种') %}\"><option value=\"CNY\">CNY</option><option value=\"USD\" selected>USD</option><option value=\"MXN\">MXN</option></select></div></div>\n        </div>\n        <div class=\"mapping-hint\" data-field=\"mappingHint\" hidden></div>\n        <div class=\"field-groups\">\n          <details class=\"parameter-group\">\n            <summary>{%= __(\"本批次核算参数\") %} <small>{%= __(\"当前批次独立保存，修改后实时计算\") %}</small></summary>\n            <div class=\"parameter-sections\">\n              <div class=\"parameter-section\">\n                <div class=\"parameter-section-title\">{%= __(\"海运专属参数\") %}</div>\n                <div class=\"group-fields\">\n                  <div class=\"field\"><label data-for=\"oceanPortFee\">{%= __(\"海运本地费用\") %}</label><div class=\"currency-control\"><input data-field=\"oceanPortFee\" data-number min=\"0\" step=\"0.001\" value=\"3482.575\"><select class=\"currency-select\" data-field=\"oceanPortFeeCurrency\" aria-label=\"{%= __('海运本地费用币种') %}\"><option value=\"CNY\" selected>CNY</option><option value=\"USD\">USD</option><option value=\"MXN\">MXN</option></select></div></div>\n                  <div class=\"field\"><label data-for=\"oceanRate\">{%= __(\"海运美元兑人民币汇率\") %}</label><div class=\"exchange-wrap\"><div class=\"input-wrap\"><input class=\"exchange-input\" data-field=\"oceanRate\" data-number min=\"0\" step=\"0.0001\" value=\"7.01\"></div><span class=\"exchange-display\" data-field=\"oceanRateDisplay\">1 USD = 7.01 CNY</span></div></div>\n                  <div class=\"field reference-field\" hidden><label data-for=\"oceanContainerFee\">{%= __(\"整柜海运费（参考）\") %}</label><div class=\"currency-control\"><input data-field=\"oceanContainerFee\" data-number min=\"0\" step=\"0.01\" placeholder=\"{%= __('可选') %}\"><select class=\"currency-select\" data-field=\"oceanContainerFeeCurrency\" aria-label=\"{%= __('整柜海运费参考币种') %}\"><option value=\"CNY\" selected>CNY</option><option value=\"USD\">USD</option><option value=\"MXN\">MXN</option></select></div></div>\n                  <div class=\"field reference-field\" hidden><label data-for=\"oceanContainerWeight\">{%= __(\"整柜总重量（参考）\") %}</label><div class=\"input-wrap\"><input data-field=\"oceanContainerWeight\" data-number min=\"0\" step=\"0.001\" placeholder=\"{%= __('可选') %}\"><span class=\"unit\">kg</span></div></div>\n                  <div class=\"field reference-field\" hidden><label data-for=\"oceanContainerVolume\">{%= __(\"整柜总体积（参考）\") %}</label><div class=\"input-wrap\"><input data-field=\"oceanContainerVolume\" data-number min=\"0\" step=\"0.000001\" placeholder=\"{%= __('可选') %}\"><span class=\"unit\">m³</span></div></div>\n                </div>\n                <div class=\"reference-note\" hidden>{%= __(\"整柜参考参数用于后续占比分摊，暂不改变当前整批成本。\") %}</div>\n              </div>\n              <div class=\"parameter-section\">\n                <div class=\"parameter-section-title\">{%= __(\"空运专属参数\") %}</div>\n                <div class=\"group-fields\">\n                  <div class=\"field\"><label data-for=\"airKgRate\">{%= __(\"空运公斤单价\") %}</label><div class=\"currency-control\"><input data-field=\"airKgRate\" data-number min=\"0\" step=\"0.01\" value=\"40\"><select class=\"currency-select\" data-field=\"airKgRateCurrency\" aria-label=\"{%= __('空运公斤单价币种') %}\"><option value=\"CNY\" selected>CNY</option><option value=\"USD\">USD</option><option value=\"MXN\">MXN</option></select></div></div>\n                  <div class=\"field\"><label data-for=\"airFixedFee\">{%= __(\"空运固定费\") %}</label><div class=\"currency-control\"><input data-field=\"airFixedFee\" data-number min=\"0\" step=\"0.01\" value=\"2000\"><select class=\"currency-select\" data-field=\"airFixedFeeCurrency\" aria-label=\"{%= __('空运固定费币种') %}\"><option value=\"CNY\" selected>CNY</option><option value=\"USD\">USD</option><option value=\"MXN\">MXN</option></select></div></div>\n                  <div class=\"field\"><label data-for=\"airRateFx\">{%= __(\"空运美元兑人民币汇率\") %}</label><div class=\"exchange-wrap\"><div class=\"input-wrap\"><input class=\"exchange-input\" data-field=\"airRateFx\" data-number min=\"0\" step=\"0.0001\" value=\"6.9236\"></div><span class=\"exchange-display\" data-field=\"airRateDisplay\">1 USD = 6.9236 CNY</span></div></div>\n                </div>\n              </div>\n              <div class=\"parameter-section\">\n                <div class=\"parameter-section-title\">{%= __(\"公共税费参数\") %}</div>\n                <div class=\"group-fields\">\n                  <div class=\"field\"><label data-for=\"tariffRate\">{%= __(\"关税率\") %}</label><div class=\"input-wrap\"><input data-field=\"tariffRate\" data-number min=\"0\" step=\"0.0001\" value=\"0\"><span class=\"unit\">%</span></div></div>\n                  <div class=\"field\"><label data-for=\"vatRate\">{%= __(\"增值税率\") %}</label><div class=\"input-wrap\"><input data-field=\"vatRate\" data-number min=\"0\" step=\"0.01\" value=\"16\"><span class=\"unit\">%</span></div></div>\n                  <div class=\"field\"><label data-for=\"clearanceMxn\">{%= __(\"清关杂费\") %}</label><div class=\"currency-control\"><input data-field=\"clearanceMxn\" data-number min=\"0\" step=\"0.01\" value=\"19500\"><select class=\"currency-select\" data-field=\"clearanceCurrency\" aria-label=\"{%= __('清关杂费币种') %}\"><option value=\"CNY\">CNY</option><option value=\"USD\">USD</option><option value=\"MXN\" selected>MXN</option></select></div></div>\n                  <div class=\"field\"><label data-for=\"mxnToCny\">{%= __(\"比索兑人民币汇率\") %}</label><div class=\"exchange-wrap\"><div class=\"input-wrap\"><input class=\"exchange-input\" data-field=\"mxnToCny\" data-number min=\"0\" step=\"0.0001\" value=\"2.6\"></div><span class=\"exchange-display\" data-field=\"mxnRateDisplay\">1 CNY = 2.60 MXN</span></div></div>\n                  <div class=\"field\"><label data-for=\"deliveryFee\">{%= __(\"目的地拖车费\") %}</label><div class=\"currency-control\"><input data-field=\"deliveryFee\" data-number min=\"0\" step=\"0.01\" value=\"600\"><select class=\"currency-select\" data-field=\"deliveryFeeCurrency\" aria-label=\"{%= __('目的地拖车费币种') %}\"><option value=\"CNY\" selected>CNY</option><option value=\"USD\">USD</option><option value=\"MXN\">MXN</option></select></div></div>\n                </div>\n              </div>\n            </div>\n          </details>\n        </div>\n      </div>\n    </section>\n\n    <section class=\"section\" aria-label=\"{%= __('运输方式成本明细') %}\">\n      <div class=\"section-head\">\n        <div>\n          <h2 data-field=\"cost-title\">{%= __(\"运输方式成本明细\") %}</h2>\n          <p>{%= __(\"按当前参数实时计算；金额均换算为人民币。\") %}</p>\n        </div>\n        <span class=\"section-kicker\">{%= __(\"金额单位：CNY\") %}</span>\n      </div>\n      <div class=\"section-body\">\n        <div class=\"calculation-issues\" data-field=\"issues\" role=\"status\"></div><div class=\"compare-grid\">\n          <article class=\"mode-panel\">\n            <div class=\"mode-head sea\">\n              <div><div class=\"mode-title\" style=\"color: var(--sea)\"><span class=\"mode-dot\"></span>{%= __(\"海运成本\") %}</div><div class=\"mode-meta\">{%= __(\"港杂费用 + 进口环节费用\") %}</div></div>\n              <span class=\"pill sea\" data-field=\"oceanBadge\">{%= __(\"待补充\") %}</span>\n            </div>\n            <div class=\"cost-list\">\n              <div class=\"cost-row\"><div class=\"cost-name\">{%= __(\"海运费\") %}</div><div class=\"cost-value-stack\"><div class=\"cost-value\" data-field=\"oceanTransport\">{%= __(\"待补充\") %}</div><div class=\"cost-diff neutral\"></div></div><div class=\"cost-formula\"><span class=\"formula-info\" tabindex=\"0\" data-tip=\"直接取海运本地费用输入值。\">i</span></div></div>\n              <div class=\"cost-row\"><div class=\"cost-name\">{%= __(\"申报货值\") %}</div><div class=\"cost-value-stack\"><div class=\"cost-value\" data-field=\"oceanDeclared\">{%= __(\"待补充\") %}</div><div class=\"cost-diff neutral\"></div></div><div class=\"cost-formula\"><span class=\"formula-info\" tabindex=\"0\" data-tip=\"核算数量 × 申报单价 × 海运美元汇率。\">i</span></div></div>\n              <div class=\"cost-row\"><div class=\"cost-name\">{%= __(\"CIF价值\") %}</div><div class=\"cost-value-stack\"><div class=\"cost-value\" data-field=\"oceanCif\">{%= __(\"待补充\") %}</div></div><div class=\"cost-formula\"><span class=\"formula-info\" tabindex=\"0\" data-tip=\"CIF = 海运费 + 申报货值。\">i</span></div></div>\n              <div class=\"cost-row\"><div class=\"cost-name\">{%= __(\"关税率\") %}</div><div class=\"cost-value-stack\"><div class=\"cost-value\" data-field=\"oceanTariffRate\">{%= __(\"待补充\") %}</div></div><div class=\"cost-formula\">{%= __(\"按参数配置\") %}</div></div>\n              <div class=\"cost-row\"><div class=\"cost-name\">{%= __(\"关税费用\") %}</div><div class=\"cost-value-stack\"><div class=\"cost-value\" data-field=\"oceanTariff\">{%= __(\"待补充\") %}</div></div><div class=\"cost-formula\"><span class=\"formula-info\" tabindex=\"0\" data-tip=\"CIF × 关税率。\">i</span></div></div>\n              <div class=\"cost-row\"><div class=\"cost-name\">{%= __(\"增值税率\") %}</div><div class=\"cost-value-stack\"><div class=\"cost-value\" data-field=\"oceanVatRate\">{%= __(\"待补充\") %}</div></div><div class=\"cost-formula\">{%= __(\"按参数配置\") %}</div></div>\n              <div class=\"cost-row\"><div class=\"cost-name\">{%= __(\"增值税费用\") %}</div><div class=\"cost-value-stack\"><div class=\"cost-value\" data-field=\"oceanVat\">{%= __(\"待补充\") %}</div></div><div class=\"cost-formula\"><span class=\"formula-info\" tabindex=\"0\" data-tip=\"(CIF + 关税费用) × 增值税率。\">i</span></div></div>\n              <div class=\"cost-row\"><div class=\"cost-name\">{%= __(\"清关杂费\") %}</div><div class=\"cost-value-stack\"><div class=\"cost-value\" data-field=\"oceanClearance\">{%= __(\"待补充\") %}</div></div><div class=\"cost-formula\"><span class=\"formula-info\" tabindex=\"0\" data-tip=\"清关杂费（MXN） ÷ 比索汇率。\">i</span></div></div>\n              <div class=\"cost-row\"><div class=\"cost-name\">{%= __(\"DTA费用\") %}</div><div class=\"cost-value-stack\"><div class=\"cost-value\" data-field=\"oceanDta\">{%= __(\"待补充\") %}</div></div><div class=\"cost-formula\"><span class=\"formula-info\" tabindex=\"0\" data-tip=\"按当前表格口径，CIF × 0.08%。\">i</span></div></div>\n              <div class=\"cost-row\"><div class=\"cost-name\">{%= __(\"拖车费\") %}</div><div class=\"cost-value-stack\"><div class=\"cost-value\" data-field=\"oceanDelivery\">{%= __(\"待补充\") %}</div></div><div class=\"cost-formula\"><span class=\"formula-info\" tabindex=\"0\" data-tip=\"取目的地拖车费参数。\">i</span></div></div>\n              <div class=\"cost-row total\"><div class=\"cost-name\">{%= __(\"运输及进口费用合计\") %}</div><div class=\"cost-value-stack\"><div class=\"cost-value\" data-field=\"oceanTotal\">{%= __(\"待补充\") %}</div><div class=\"cost-diff neutral\"></div></div><div class=\"cost-formula\">{%= __(\"不含申报货值\") %}</div></div>\n              <div class=\"cost-row time-row\"><div class=\"cost-name\">{%= __(\"预计时效\") %}</div><div class=\"cost-value-stack\"><div class=\"cost-value\">{%= __(\"30–40 天（参考）\") %}</div></div><div class=\"cost-formula\">{%= __(\"参考时效\") %}</div></div>\n            </div>\n            <div class=\"avg-block\"><span class=\"avg-label\">{%= __(\"平均单位成本\") %}</span><strong class=\"avg-value\" data-field=\"oceanAvg\">{%= __(\"待补充\") %}</strong></div>\n            <div class=\"mode-footnote\">{%= __(\"平均单位成本 = 运输及进口费用合计 ÷ 核算数量。\") %}</div>\n          </article>\n\n          <article class=\"mode-panel\">\n            <div class=\"mode-head air\">\n              <div><div class=\"mode-title\" style=\"color: var(--air)\"><span class=\"mode-dot\"></span>{%= __(\"空运成本\") %}</div><div class=\"mode-meta\">{%= __(\"按总毛重计算空运费\") %}</div></div>\n              <span class=\"pill air\" data-field=\"airBadge\">{%= __(\"待补充\") %}</span>\n            </div>\n            <div class=\"cost-list\">\n              <div class=\"cost-row\"><div class=\"cost-name\">{%= __(\"空运费\") %}</div><div class=\"cost-value-stack\"><div class=\"cost-value\" data-field=\"airTransport\">{%= __(\"待补充\") %}</div><div class=\"cost-diff\" data-field=\"diffTransport\"></div></div><div class=\"cost-formula\"><span class=\"formula-info\" tabindex=\"0\" data-tip=\"空运公斤单价 × 总毛重 + 空运固定费。当前体积未进入公式。\">i</span></div></div>\n              <div class=\"cost-row\"><div class=\"cost-name\">{%= __(\"申报货值\") %}</div><div class=\"cost-value-stack\"><div class=\"cost-value\" data-field=\"airDeclared\">{%= __(\"待补充\") %}</div><div class=\"cost-diff\" data-field=\"diffDeclared\"></div></div><div class=\"cost-formula\"><span class=\"formula-info\" tabindex=\"0\" data-tip=\"核算数量 × 申报单价 × 空运美元汇率。\">i</span></div></div>\n              <div class=\"cost-row\"><div class=\"cost-name\">{%= __(\"CIF价值\") %}</div><div class=\"cost-value-stack\"><div class=\"cost-value\" data-field=\"airCif\">{%= __(\"待补充\") %}</div><div class=\"cost-diff\" data-field=\"diffCif\"></div></div><div class=\"cost-formula\"><span class=\"formula-info\" tabindex=\"0\" data-tip=\"CIF = 空运费 + 申报货值。\">i</span></div></div>\n              <div class=\"cost-row\"><div class=\"cost-name\">{%= __(\"关税率\") %}</div><div class=\"cost-value-stack\"><div class=\"cost-value\" data-field=\"airTariffRate\">{%= __(\"待补充\") %}</div></div><div class=\"cost-formula\">{%= __(\"按参数配置\") %}</div></div>\n              <div class=\"cost-row\"><div class=\"cost-name\">{%= __(\"关税费用\") %}</div><div class=\"cost-value-stack\"><div class=\"cost-value\" data-field=\"airTariff\">{%= __(\"待补充\") %}</div><div class=\"cost-diff\" data-field=\"diffTariff\"></div></div><div class=\"cost-formula\"><span class=\"formula-info\" tabindex=\"0\" data-tip=\"CIF × 关税率。\">i</span></div></div>\n              <div class=\"cost-row\"><div class=\"cost-name\">{%= __(\"增值税率\") %}</div><div class=\"cost-value-stack\"><div class=\"cost-value\" data-field=\"airVatRate\">{%= __(\"待补充\") %}</div></div><div class=\"cost-formula\">{%= __(\"按参数配置\") %}</div></div>\n              <div class=\"cost-row\"><div class=\"cost-name\">{%= __(\"增值税费用\") %}</div><div class=\"cost-value-stack\"><div class=\"cost-value\" data-field=\"airVat\">{%= __(\"待补充\") %}</div><div class=\"cost-diff\" data-field=\"diffVat\"></div></div><div class=\"cost-formula\"><span class=\"formula-info\" tabindex=\"0\" data-tip=\"(CIF + 关税费用) × 增值税率。\">i</span></div></div>\n              <div class=\"cost-row\"><div class=\"cost-name\">{%= __(\"清关杂费\") %}</div><div class=\"cost-value-stack\"><div class=\"cost-value\" data-field=\"airClearance\">{%= __(\"待补充\") %}</div><div class=\"cost-diff neutral\" data-field=\"diffClearance\"></div></div><div class=\"cost-formula\"><span class=\"formula-info\" tabindex=\"0\" data-tip=\"清关杂费（MXN） ÷ 比索汇率。\">i</span></div></div>\n              <div class=\"cost-row\"><div class=\"cost-name\">{%= __(\"DTA费用\") %}</div><div class=\"cost-value-stack\"><div class=\"cost-value\" data-field=\"airDta\">{%= __(\"待补充\") %}</div><div class=\"cost-diff\" data-field=\"diffDta\"></div></div><div class=\"cost-formula\"><span class=\"formula-info\" tabindex=\"0\" data-tip=\"按当前表格口径，CIF × 0.08%。\">i</span></div></div>\n              <div class=\"cost-row\"><div class=\"cost-name\">{%= __(\"拖车费\") %}</div><div class=\"cost-value-stack\"><div class=\"cost-value\" data-field=\"airDelivery\">{%= __(\"待补充\") %}</div><div class=\"cost-diff neutral\" data-field=\"diffDelivery\"></div></div><div class=\"cost-formula\"><span class=\"formula-info\" tabindex=\"0\" data-tip=\"取目的地拖车费参数。\">i</span></div></div>\n              <div class=\"cost-row total\"><div class=\"cost-name\">{%= __(\"运输及进口费用合计\") %}</div><div class=\"cost-value-stack\"><div class=\"cost-value\" data-field=\"airTotal\">{%= __(\"待补充\") %}</div><div class=\"cost-diff\" data-field=\"diffTotal\"></div></div><div class=\"cost-formula\">{%= __(\"不含申报货值\") %}</div></div>\n              <div class=\"cost-row time-row\"><div class=\"cost-name\">{%= __(\"预计时效\") %}</div><div class=\"cost-value-stack\"><div class=\"cost-value\">{%= __(\"待补充\") %}</div></div><div class=\"cost-formula\">{%= __(\"需确认\") %}</div></div>\n            </div>\n            <div class=\"avg-block\"><span class=\"avg-label\">{%= __(\"平均单位成本\") %}</span><strong class=\"avg-value\" data-field=\"airAvg\">{%= __(\"待补充\") %}</strong></div>\n            <div class=\"mode-footnote\" data-field=\"airFootnote\">{%= __(\"空运公斤单价 × 总毛重 + 空运固定费。体积暂未进入计算。\") %}</div>\n          </article>\n        </div>\n      </div>\n    </section>\n  </main>\n";

// Source: 50-controller.js
class AirSeaComparisonPage {
  constructor(wrapper) {
    this.wrapper=wrapper;this.session=new RecordSession();this.browser={mode:'records',page:1,total:0,items:[],token:0,busy:false};this.previewToken=0;this.readToken=0;this.active=false;this.allowRouteChange=false;
    this.page=frappe.ui.make_app_page({parent:wrapper,title:ocTranslate("空运海运成本对比"),single_column:true});
    this.root=document.createElement('div');this.root.className='air-sea-cost-comparison';this.root.innerHTML=frappe.render_template(PAGE_TEMPLATE, {});
    this.page.main.append(this.root);
    const prefix='air-sea-'+uuid();
    this.root.querySelectorAll('[data-for]').forEach(label=>{const field=this.$(label.dataset.for);if(field){field.id=prefix+'-'+label.dataset.for;label.htmlFor=field.id;}});
    this.root.addEventListener('input',event=>this.onInput(event));
    this.root.addEventListener('change',event=>this.onChange(event));
    this.root.addEventListener('click',event=>this.onClick(event));
    this.root.addEventListener('paste',event=>this.onPaste(event));
    this.root.addEventListener('keydown',event=>this.onKeydown(event));
    this.beforeUnload=event=>{if(this.active&&(this.session.dirty||this.session.saving)){event.preventDefault();event.returnValue='';}};
    this.navigationClick=event=>{
      if(!this.active||event.defaultPrevented||event.button!==0||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;
      const link=event.target.closest&&event.target.closest('a[href]');
      if(!link||link.target==='_blank'||link.hasAttribute('download'))return;
      const url=new URL(link.href,window.location.href);
      if(url.href===window.location.href||link.getAttribute('href')==='#')return;
      if(!this.canReplace(ocTranslate("离开当前页面"))){event.preventDefault();event.stopImmediatePropagation();}
      else this.allowRouteChange=true;
    };
    this.routeChange=()=>{
      const onPage=frappe.get_route&&frappe.get_route()[0]==='air-sea-cost-comparison';
      if(onPage){this.show();return;}
      if(!this.active)return;
      if(!this.allowRouteChange&&!this.canReplace(ocTranslate("离开当前页面"))){this.active=false;frappe.set_route('air-sea-cost-comparison');return;}
      this.allowRouteChange=false;this.hide();
    };
    window.addEventListener('beforeunload',this.beforeUnload);
    document.addEventListener('click',this.navigationClick,true);
    if(frappe.router&&frappe.router.on)frappe.router.on('change',this.routeChange);
    this.resize=()=>this.alignRows();window.addEventListener('resize',this.resize);
    this.fillInputs();this.renderTable();this.render();this.show();
  }
  $(name){return this.root.querySelector(`[data-field="${name}"]`);}
  text(name,value){const node=this.$(name);if(node)node.textContent=value;}
  show(){this.active=true;this.allowRouteChange=false;this.alignRows();}
  hide(){this.active=false;this.browser.token+=1;this.previewToken+=1;this.readToken+=1;this.browser.busy=false;this.browserButtons();}
  destroy(){this.hide();window.removeEventListener('beforeunload',this.beforeUnload);document.removeEventListener('click',this.navigationClick,true);window.removeEventListener('resize',this.resize);if(frappe.router&&frappe.router.off)frappe.router.off('change',this.routeChange);}
  async call(method,args={}){
    const response=await frappe.call({method:'overseas_costing.api.air_sea_comparison.'+method,args});
    const result=response&&response.message;
    if(!result||!result.ok)throw new Error((result&&result.message)||ocTranslate("请求未完成，请重试。"));
    return result;
  }
  notice(message,error=false){this.text("gridStatus",message);this.$('gridStatus').className='grid-status '+(error?'error':'success');}
  error(error){this.notice(error&&error.message||ocTranslate("请求失败，当前编辑已保留，请重试。"),true);}
  canReplace(action){
    if(this.session.saving){this.notice(ocTranslate("正在保存，请等待保存完成。"),true);return false;}
    return !this.session.dirty||window.confirm(ocTranslate("当前有未保存的修改。{0}会放弃这些修改，是否继续？", [action]));
  }
  fillInputs(){
    const s=this.session,p=s.payload;
    Object.entries({...p.parameters,...p.currencies}).forEach(([id,value])=>{if(this.$(id))this.$(id).value=value??'';});
    this.$('recordName').value=s.title;
    const override=p.totals_override||{};
    this.$('grossWeightOverride').value=override.grossWeight??'';this.$('volumeOverride').value=override.volume??'';
    this.$('overrideEnabled').checked=!!p.source||!blank(override.grossWeight)||!blank(override.volume);
    this.$('overrideFields').hidden=!this.$('overrideEnabled').checked;
    const source=p.source;this.$('sourceNote').hidden=!source;this.$('sourceNote').classList.remove('source-changed');
    this.text("sourceNote",source?ocTranslate("来源批次：{0}。本页编辑仅用于独立测算，保留导入时的来源快照。", [source.batch_no||source.batch||source.batch_name||ocTranslate("已导入批次")]):'');
  }
  headerLabel(column,currency=this.session.payload.currencies.declaredCurrency){return column===25?ocTranslate("申报单价（{0}）", [currency]):column===26?ocTranslate("申报总价（{0}）", [currency]):headers[column];}
  renderHeaders(){this.$('packingHeaders').innerHTML=displayColumns.map(column=>`<th scope="col" class="col-${column}${numericColumns.has(column)?' numeric-col':''}">${escapeHtml(this.headerLabel(column))}</th>`).join('')+`<th scope="col">${ocTranslate("操作")}</th>`;}
  rowHtml(row,index){return `<tr>${displayColumns.map(column=>`<td class="col-${column}${numericColumns.has(column)?' numeric-col':''}"><textarea class="grid-input" data-row="${index}" data-column="${column}" aria-label="${escapeHtml(ocTranslate("第 {0} 行 {1}", [index+1, headers[column]]))}" rows="1">${escapeHtml(row[column])}</textarea></td>`).join('')}<td><button class="remove-row" data-action="remove-row" data-row="${index}" type="button" aria-label="${escapeHtml(ocTranslate("删除第 {0} 行", [index+1]))}">×</button></td></tr>`;}
  renderTable(){
    this.renderHeaders();
    this.$('packingColgroup').innerHTML=displayColumns.map(column=>`<col style="width:${column===0?185:[1,6,11].includes(column)?132:100}px">`).join('')+'<col style="width:48px">';
    this.$('packingRows').innerHTML=this.session.payload.rows.map((row,index)=>this.rowHtml(row,index)).join('');
    this.root.querySelectorAll('.grid-input').forEach(input=>this.sizeInput(input));
  }
  sizeInput(input){input.style.height='auto';input.style.height=Math.max(34,input.scrollHeight)+'px';}
  ensureBlankRow(){const rows=this.session.payload.rows;if(!rows.length||rows.at(-1).some(value=>!blank(value))){rows.push(emptyRow());this.$('packingRows').insertAdjacentHTML('beforeend',this.rowHtml(rows.at(-1),rows.length-1));}}
  onInput(event){
    const input=event.target,s=this.session,p=s.payload,id=input.dataset.field;
    if(input.matches('.grid-input')){const row=Number(input.dataset.row),column=Number(input.dataset.column);p.rows[row][column]=input.value;this.sizeInput(input);this.ensureBlankRow();}
    else if(id==='recordName')s.title=input.value;
    else if(Object.hasOwn(defaults,id))p.parameters[id]=input.value;
    else if(id==='grossWeightOverride'||id==='volumeOverride'){p.totals_override=p.totals_override||{};p.totals_override[id==='grossWeightOverride'?'grossWeight':'volume']=input.value.trim()||null;}
    else return;
    s.touch();this.render();
  }
  onChange(event){
    const id=event.target.dataset.field;
    if(Object.hasOwn(currencyDefaults,id)){this.session.payload.currencies[id]=event.target.value;if(id==='declaredCurrency')this.renderHeaders();this.session.touch();this.render();}
    if(id==='overrideEnabled'){
      this.$('overrideFields').hidden=!event.target.checked;
      if(!event.target.checked){this.session.payload.totals_override={grossWeight:null,volume:null};this.$('grossWeightOverride').value='';this.$('volumeOverride').value='';this.session.touch();this.render();}
    }
  }
  async onClick(event){
    const button=event.target.closest('button');if(!button||!this.root.contains(button))return;
    const id=button.dataset.field,action=button.dataset.action;
    try{
      if(id==='recalculate'){this.session.result=null;this.render();this.notice(ocTranslate("已按当前参数重新计算。"));}
      else if(id==='saveRecord')await this.save();
      else if(id==='loadRecord')await this.openBrowser('records');
      else if(id==='importBatch')await this.openBrowser('batches');
      else if(id==='newRecord'||id==='loadSample'){
        if(!this.canReplace(id==='newRecord'?ocTranslate("新建记录"):ocTranslate("载入样例")))return;
        const payload=newPayload();if(id==='loadSample')payload.rows=[sampleRow.slice(),emptyRow()];
        this.apply({payload,title:id==='loadSample'?ocTranslate("样例测算"):''},id==='loadSample');this.notice(id==='newRecord'?ocTranslate("已新建空白记录。"):ocTranslate("已载入样例，可继续编辑并保存。"));
      }else if(id==='resetAll'){
        if(!this.canReplace(ocTranslate("重置核算参数")))return;
        this.session.payload.parameters=clone(defaults);this.session.payload.currencies=clone(currencyDefaults);this.session.touch();this.fillInputs();this.render();this.notice(ocTranslate("已恢复默认核算参数。"));
      }else if(action==='close-browser'){this.browser.token+=1;this.browser.busy=false;this.$('browserPanel').hidden=true;}
      else if(action==='search'){this.browser.page=1;await this.fetchBrowser();}
      else if(action==='previous'&&this.browser.page>1){this.browser.page-=1;await this.fetchBrowser();}
      else if(action==='next'&&this.browser.page*20<this.browser.total){this.browser.page+=1;await this.fetchBrowser();}
      else if(action==='load-record')await this.loadRecord(button.dataset.name);
      else if(action==='delete-record')await this.deleteRecord(button.dataset.name,button);
      else if(action==='preview-batch')await this.previewBatch(button.dataset.name);
      else if(action==='cancel-preview'){this.previewToken+=1;this.preview=null;this.$('previewPanel').hidden=true;}
      else if(action==='confirm-preview'&&this.preview){
        if(!this.canReplace(ocTranslate("载入批次数据")))return;
        this.apply({payload:this.preview.payload,title:ocTranslate("{0} 空运海运对比", [this.preview.payload.source?.batch_no||ocTranslate("批次")])},true);this.notice(ocTranslate("已载入批次快照，所有明细及整票汇总均可编辑。"));
      }else if(action==='remove-row'){
        const row=Number(button.dataset.row);if(!this.session.payload.rows[row])return;
        this.session.payload.rows.splice(row,1);if(!this.session.payload.rows.length)this.session.payload.rows.push(emptyRow());this.session.touch();this.renderTable();this.ensureBlankRow();this.render();
      }
    }catch(error){this.error(error);}
  }
  onPaste(event){
    const input=event.target;if(!input.matches('.grid-input'))return;
    const text=event.clipboardData?.getData('text/plain')||'',html=event.clipboardData?.getData('text/html')||'';
    if(!html&&!/[\t\r\n]/.test(text))return;
    event.preventDefault();
    const result=applyPaste(this.session.payload.rows,text,html,Number(input.dataset.row),Number(input.dataset.column));
    if(result.rows.length>5001){this.notice(ocTranslate("装箱明细最多支持 5000 行，请分批测算。"),true);return;}
    this.session.payload.rows=result.rows;this.session.touch();this.renderTable();this.render();
    this.$('mappingHint').hidden=!result.missing.length;this.text("mappingHint",result.missing.map(column=>ocTranslate("未识别到{0}，请补充", [headers[column]])).join('；'));
    this.notice(ocTranslate("{0}已粘贴 {1} 行。", [result.headerRow?ocTranslate("已识别第 {0} 行表头，映射 {1} 个字段；", [result.headerRow, result.matched]):'', result.count]));
  }
  onKeydown(event){
    if(event.target.dataset.field==='searchKeyword'&&event.key==='Enter'){event.preventDefault();this.browser.page=1;this.fetchBrowser().catch(error=>this.error(error));return;}
    if(!event.target.matches('.grid-input')||event.key!=='Enter'||event.shiftKey)return;
    event.preventDefault();const row=Number(event.target.dataset.row),column=Number(event.target.dataset.column),index=displayColumns.indexOf(column);
    const next=index+1<displayColumns.length?[row,displayColumns[index+1]]:[row+1,displayColumns[0]];
    this.root.querySelector(`.grid-input[data-row="${next[0]}"][data-column="${next[1]}"]`)?.focus();
  }
  apply(record,dirty=false){
    this.readToken+=1;this.previewToken+=1;this.preview=null;this.session.apply(record,dirty);this.$('previewPanel').hidden=true;this.$('mappingHint').hidden=true;this.fillInputs();this.renderTable();this.render();
  }
  async save(){
    if(this.session.saving)return;
    if(!this.session.title.trim()){this.$('recordName').focus();this.notice(ocTranslate("请填写记录名称。"),true);return;}
    const promise=this.session.save((method,args)=>this.call(method,args));this.renderSaveState();
    try{const response=await promise;if(response){this.render();this.notice(this.session.dirty?ocTranslate("已保存提交时的数据；之后的修改尚未保存。"):ocTranslate("记录已保存{0}。", [this.session.result?.status==='Draft'?ocTranslate("为草稿"):'']));if(response.source_changed)this.sourceChanged();if(!this.$('browserPanel').hidden&&this.browser.mode==='records')await this.fetchBrowser();}}
    finally{this.renderSaveState();}
  }
  sourceChanged(){this.$('sourceNote').hidden=false;this.$('sourceNote').classList.add('source-changed');this.text("sourceNote",ocTranslate("来源批次已变更。本记录仍使用保存时的快照；如需更新，请从批次重新导入并确认。"));}
  renderSaveState(){const s=this.session;this.$('saveRecord').disabled=s.saving;this.text("saveState",s.saving?ocTranslate("正在保存…"):s.dirty?ocTranslate("有未保存修改"):s.name?ocTranslate("已保存"):ocTranslate("未保存"));}
  render(){
    const r=this.session.result||calculate(this.session.payload),v=r.v||{};this.renderSaveState();
    this.text("summaryQuantity",ocTranslate("{0} {1}", [quantityText(v.quantity), r.mixedUnits?ocTranslate("（单位不一致）"):ocTranslate(r.unit||"件")]));
    this.text("summaryGross",`${fixed(v.grossWeight,3)} kg`);this.text("summaryVolume",`${fixed(v.volume,6)} m³`);
    this.text("summaryUnitPrice",ocTranslate("{0} {1}", [r.mixedUnits?ocTranslate("单位不一致"):fixed(v.declaredUnitPrice,6), v.declaredCurrency||this.session.payload.currencies.declaredCurrency]));
    this.$('emptyDataHint').hidden=Number(v.quantity)>0;
    this.$('issues').hidden=r.status==='Ready'&&!r.mixedUnits;
    this.text("issues",r.status==='Draft'?ocTranslate("待补充 · {0}{1}。可以保存为草稿。", [(r.issues||[]).slice(0,8).join('；'), (r.issues||[]).length>8?ocTranslate("；其余问题请检查明细"):'']):r.mixedUnits?ocTranslate("单位不一致，暂不计算平均单位成本；费用合计仍可对比。"):'');
    for(const mode of ['ocean','air']){
      for(const field of ['Transport','Declared','Cif','Tariff','Vat','Clearance','Dta','Delivery','Total'])this.text(mode+field,money(r[mode==='ocean'&&field==='Transport'?'oceanPortFee':mode+field]));
      this.text(mode+'TariffRate',v.tariffRate==null?ocTranslate("待补充"):`${v.tariffRate}%`);this.text(mode+'VatRate',v.vatRate==null?ocTranslate("待补充"):`${v.vatRate}%`);
      this.text(mode+'Avg',r.mixedUnits?ocTranslate("单位不一致，暂不计算"):r[mode+'Avg']==null?ocTranslate("待补充"):`¥${fixed(r[mode+'Avg'],6)} / ${ocTranslate(r.unit||"件")}`);
    }
    for(const [name,air,sea] of [['Transport','airTransport','oceanPortFee'],...['Declared','Cif','Tariff','Vat','Clearance','Dta','Delivery','Total'].map(field=>[field,'air'+field,'ocean'+field])]){
      const node=this.$('diff'+name);if(!node)continue;
      node.className='cost-diff neutral';
      if(r[air]==null||r[sea]==null){node.textContent='';continue;}
      const difference=Number(r[air])-Number(r[sea]);node.textContent=Math.abs(difference)<.005?ocTranslate("与海运相同"):ocTranslate("比海运{0} {1}", [difference>0?ocTranslate("贵"):ocTranslate("低"), money(Math.abs(difference))]);node.classList.add(difference>0?'higher':'lower');
    }
    const comparable=r.status==='Ready';const delta=comparable?Number(r.airTotal)-Number(r.oceanTotal):0;
    this.text("oceanBadge",!comparable?ocTranslate("待补充"):Math.abs(delta)<.005?ocTranslate("费用相同"):delta>0?ocTranslate("费用较低"):ocTranslate("费用较高"));
    this.text("airBadge",!comparable?ocTranslate("待补充"):Math.abs(delta)<.005?ocTranslate("费用相同"):delta<0?ocTranslate("费用较低"):ocTranslate("费用较高"));
    this.text("oceanRateDisplay",v.oceanRate>0?`1 USD = ${v.oceanRate} CNY`:ocTranslate("未设置有效汇率"));
    this.text("airRateDisplay",v.airRateFx>0?`1 USD = ${v.airRateFx} CNY`:ocTranslate("未设置有效汇率"));
    this.text("mxnRateDisplay",v.mxnToCny>0?`1 CNY = ${v.mxnToCny} MXN`:ocTranslate("未设置有效汇率"));
    this.text("airFootnote",ocTranslate("空运费 = {0} {1}/kg × 总毛重 + {2} {3}，按对应汇率换算人民币。体积暂未进入计算。", [v.airKgRate??ocTranslate("待补充"), v.airKgRateCurrency||'CNY', v.airFixedFee??ocTranslate("待补充"), v.airFixedFeeCurrency||'CNY']));
    this.formulas(v);this.alignRows();
  }
  formulas(v){
    const convert=(currency,mode)=>currency==='USD'?ocTranslate("（USD）× {0}美元汇率", [mode==='ocean'?ocTranslate("海运"):ocTranslate("空运")]):currency==='MXN'?ocTranslate("（MXN）÷ 比索汇率"):ocTranslate("（CNY，直接使用）");
    const formula=(field,text)=>{const node=this.$(field)?.closest('.cost-row')?.querySelector('.formula-info');if(node){node.dataset.tip=text;node.setAttribute('aria-label',text);}};
    formula('oceanTransport',ocTranslate("海运本地费用{0}。", [convert(v.oceanPortFeeCurrency,'ocean')]));
    formula('airTransport',ocTranslate("空运公斤单价{0} × 总毛重 + 空运固定费{1}。体积未进入当前公式。", [convert(v.airKgRateCurrency,'air'), convert(v.airFixedFeeCurrency,'air')]));
    for(const mode of ['ocean','air']){
      formula(mode+'Declared',ocTranslate("逐行汇总申报货值{0}。有单价时按数量 × 单价，否则使用该行总价。", [convert(v.declaredCurrency,mode)]));
      formula(mode+'Clearance',ocTranslate("清关杂费{0}。", [convert(v.clearanceCurrency,mode)]));
      formula(mode+'Delivery',ocTranslate("目的地拖车费{0}。", [convert(v.deliveryFeeCurrency,mode)]));
    }
  }
  alignRows(){
    if(!this.active)return;
    const panels=this.root.querySelectorAll('.mode-panel');if(panels.length!==2)return;
    const left=panels[0].querySelectorAll('.cost-row'),right=panels[1].querySelectorAll('.cost-row');
    [...left,...right].forEach(row=>row.style.height='auto');
    if(panels[0].getBoundingClientRect().top!==panels[1].getBoundingClientRect().top)return;
    left.forEach((row,index)=>{if(right[index]){const height=Math.max(row.offsetHeight,right[index].offsetHeight);row.style.height=right[index].style.height=height+'px';}});
  }
  async openBrowser(mode){this.browser.mode=mode;this.browser.page=1;this.$('browserPanel').hidden=false;this.$('searchKeyword').value='';this.text("browserTitle",mode==='batches'?ocTranslate("选择装箱批次"):ocTranslate("团队测算记录"));await this.fetchBrowser();}
  browserButtons(){this.root.querySelectorAll('[data-action="previous"],[data-action="next"]').forEach(button=>{button.disabled=this.browser.busy||(button.dataset.action==='previous'?this.browser.page<=1:this.browser.page*20>=this.browser.total);});}
  async fetchBrowser(){
    const b=this.browser,token=++b.token,mode=b.mode;b.busy=true;this.browserButtons();this.text("browserStatus",ocTranslate("正在读取…"));
    try{
      const result=await this.call(mode==='batches'?'search_batches':'list_records',{keyword:this.$('searchKeyword').value.trim(),page:b.page,page_length:20});
      if(token!==b.token)return;b.items=result.items||[];b.total=Number(result.total)||0;b.page=Number(result.page)||b.page;
      this.text("browserStatus",b.total?ocTranslate("共 {0} 条", [b.total]):ocTranslate("暂无匹配结果"));this.text("pageInfo",ocTranslate("第 {0} / {1} 页", [b.page, Math.max(1,Math.ceil(b.total/20))]));
      this.$('browserList').innerHTML=b.items.map(item=>`<div class="record-item"><div class="record-item-info"><div class="record-item-name">${escapeHtml(mode==='batches'?(item.batch_no||item.name):(item.title||item.name))}</div><div class="record-item-meta">${mode==='batches'?escapeHtml([item.source_approval_no,item.transport_mode].filter(Boolean).join(' · ')):escapeHtml(ocTranslate("{0} · {1} · {2}", [item.status==='Ready'?ocTranslate("已完成"):ocTranslate("草稿"), item.modified||'', item.modified_by||item.owner||'']))}</div>${mode==='records'?`<div class="record-item-meta">${ocTranslate("海运 {0} · 空运 {1}", [money(item.sea_total), money(item.air_total)])}</div>`:''}</div><div class="record-item-actions"><button class="btn ghost" data-action="${mode==='batches'?'preview-batch':'load-record'}" data-name="${escapeHtml(item.name)}" type="button">${mode==='batches'?ocTranslate("预览"):ocTranslate("读取")}</button>${mode==='records'?`<button class="btn" data-action="delete-record" data-name="${escapeHtml(item.name)}" type="button">${ocTranslate("删除")}</button>`:''}</div></div>`).join('');
    }catch(error){if(token===b.token){this.text("browserStatus",ocTranslate("读取失败，已有数据和当前编辑均保留。"));throw error;}}
    finally{if(token===b.token){b.busy=false;this.browserButtons();}}
  }
  async loadRecord(name){
    if(!this.canReplace(ocTranslate("读取另一条记录")))return;
    const token=++this.readToken,revision=this.session.revision;this.notice(ocTranslate("正在读取记录…"));
    const response=await this.call('get_record',{name});if(token!==this.readToken||!this.active)return;
    if(revision!==this.session.revision&&!this.canReplace(ocTranslate("读取另一条记录")))return;
    this.apply(response.record);this.notice(ocTranslate("已读取团队记录。"));if(response.source_changed)this.sourceChanged();
  }
  async deleteRecord(name,button){
    if(button.disabled)return;const item=this.browser.items.find(item=>item.name===name);if(!item)return;
    if(this.session.saving){this.notice(ocTranslate("正在保存，请等待保存完成。"),true);return;}
    if(!window.confirm(ocTranslate("确定删除团队记录“{0}”吗？", [item.title||item.name])))return;
    button.disabled=true;
    try{await this.call('delete_record',{name,modified:item.modified});if(this.session.name===name){this.session.name=null;this.session.modified=null;this.session.touch();this.renderSaveState();}this.notice(ocTranslate("记录已删除。"));if(this.browser.items.length===1&&this.browser.page>1)this.browser.page-=1;await this.fetchBrowser();}
    finally{button.disabled=false;}
  }
  async previewBatch(name){
    const token=++this.previewToken;this.notice(ocTranslate("正在读取完整批次预览…"));
    const response=await this.call('preview_batch',{batch_name:name});if(token!==this.previewToken||!this.active)return;
    this.preview=response;const rows=(response.payload.rows||[]).filter(row=>row.some(value=>!blank(value)));const r=calculate(response.payload),v=r.v;
    const panel=this.$('previewPanel');panel.hidden=false;
    panel.innerHTML=`<div class="record-panel-head"><h3>${ocTranslate("导入预览 · {0} 行完整明细", [rows.length])}</h3><button class="btn" data-action="cancel-preview" type="button">${ocTranslate("取消")}</button></div><p>${ocTranslate("总数量 {0} {1} · 整票总毛重 {2} kg · 整票总体积 {3} m³", [escapeHtml(quantityText(v.quantity)), escapeHtml(r.mixedUnits?ocTranslate("（单位不一致）"):r.unit), escapeHtml(fixed(v.grossWeight,3)), escapeHtml(fixed(v.volume,6))])}</p><div class="calculation-issues">${(response.warnings||[]).map(warning=>escapeHtml(typeof warning==='string'?warning:warning.message||JSON.stringify(warning))).join('<br>')||ocTranslate("请核对全部明细与整票汇总后载入。")}</div><div class="preview-table-scroll"><table class="preview-table"><thead><tr>${headers.map((header,column)=>`<th>${escapeHtml(this.headerLabel(column,response.payload.currencies?.declaredCurrency||currencyDefaults.declaredCurrency))}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${normalizeRow(row).map(value=>`<td>${escapeHtml(value)}</td>`).join('')}</tr>`).join('')}</tbody></table></div><div class="preview-actions"><button class="btn primary" data-action="confirm-preview" type="button">${ocTranslate("确认载入这 {0} 行", [rows.length])}</button></div>`;
    panel.scrollIntoView({block:'nearest'});this.notice(ocTranslate("批次预览已就绪；确认后替换当前测算。"));
  }
}
globalThis.OverseasAirSeaCalculator={calculate,defaults,currencyDefaults,parseNumber,headers,displayColumns,pastedRows,parsePaste,applyPaste,RecordSession,AirSeaComparisonPage};
if(typeof module!=='undefined'&&module.exports)module.exports=globalThis.OverseasAirSeaCalculator;
if(typeof frappe!=='undefined'&&frappe.pages){
  frappe.pages['air-sea-cost-comparison']=frappe.pages['air-sea-cost-comparison']||{};
  frappe.pages['air-sea-cost-comparison'].on_page_load=wrapper=>{if(!wrapper.airSeaComparison)wrapper.airSeaComparison=new AirSeaComparisonPage(wrapper);};
  frappe.pages['air-sea-cost-comparison'].on_page_show=wrapper=>wrapper.airSeaComparison?.show();
  frappe.pages['air-sea-cost-comparison'].on_page_unload=wrapper=>{wrapper.airSeaComparison?.destroy();wrapper.airSeaComparison=null;};
}

})(typeof globalThis !== "undefined" ? globalThis : window);
