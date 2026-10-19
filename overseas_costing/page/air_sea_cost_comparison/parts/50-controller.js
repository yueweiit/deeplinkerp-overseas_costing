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
