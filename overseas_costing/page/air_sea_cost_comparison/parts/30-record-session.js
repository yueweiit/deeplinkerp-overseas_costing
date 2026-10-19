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
