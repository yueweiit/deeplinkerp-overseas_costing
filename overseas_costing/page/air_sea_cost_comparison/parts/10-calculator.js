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
