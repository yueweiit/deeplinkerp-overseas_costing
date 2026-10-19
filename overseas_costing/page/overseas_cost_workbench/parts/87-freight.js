  freightEvidence(evidence = {}) {
    return `${evidence.file_name || "审批正文"} · ${evidence.sheet || "明细"}${evidence.row != null ? ocTranslate(" · 第 {0} 行", [evidence.row]) : ""}`;
  }

  freightMoney(amount, currency) {
    return `${this.escape(amount ?? ocTranslate("待核对"))} ${this.escape(currency || '')}`;
  }

  freightSourceCell(row = {}, source = {}) {
    return `<span class="ocw-freight-source-no">${this.escape(source.approval_no || row.approval_no || ocTranslate("结算原单"))}</span><small>${this.escape(this.freightEvidence(row.evidence || {}))}</small>`;
  }

  freightActionButton(action, label, attributes = '', primary = false, disabled = false) {
    return `<button type="button" class="${primary ? 'ocw-primary-btn' : 'ocw-outline-btn'}" data-settlement-action="${action}" ${attributes} ${disabled ? 'disabled' : ''}>${ocTranslate(label)}</button>`;
  }

  paymentReadOnly(data = {}) {
    const confirmed = String(data.confirm_status || data.version_status || '').toLowerCase();
    const writeback = String(data.writeback_status || '').toLowerCase();
    return !!(data.historical || data.readonly || data.locked
      || this.detailState?.readonly === true
      || ['confirmed', 'archived', 'final'].includes(confirmed)
      || ['success', 'completed', 'posted'].includes(writeback));
  }

  async ensurePaymentWriteSession(state) {
    if (this.paymentReadOnly(state.data || {})) throw new Error(ocTranslate("当前版本只读，不能修改实际支付资料"));
    if (this.detailState?.batchName !== state.batchName || typeof this.ensureEditSession !== 'function') {
      throw new Error(ocTranslate("未取得当前批次编辑上下文，请返回批次详情后重试"));
    }
    const draft = state.freightDraft;
    if (!(await this.ensureEditSession())) {
      state.freightDraft = draft;
      throw new Error(ocTranslate("未取得当前批次编辑权，输入已保留，请稍后重试"));
    }
    if (!this.detailState.editToken || !this.detailState.expectedModified) {
      state.freightDraft = draft;
      throw new Error(ocTranslate("编辑权资料不完整，输入已保留，请重新获取编辑权"));
    }
    return true;
  }

  renderFreightContent(data, state = {}) {
    const tab = state.freightTab || 'freight';
    const unified = Array.isArray(data.payment_claims) || Array.isArray(data.payment_candidates) || !!data.payment_matching;
    const tabs = unified ? [['freight', '实际支付流程'], ['packing', '装箱变更'], ['audit', '操作记录']]
      : [['freight', '运费'], ['packing', '装箱来源'], ['audit', '操作记录']];
    const view = state.freightView;
    const paymentViewBlocked = this.paymentReadOnly(data) && view?.kind?.startsWith('payment-');
    let body;
    if (view?.kind === 'evidence') body = this.renderFreightInlineEvidence(state);
    else if (tab === 'packing') body = this.renderFreightPackingSources(data, state);
    else if (tab === 'audit') body = this.renderFreightAudit(data);
    else if (view?.kind === 'search') body = this.renderFreightSearch(state);
    else if (view?.kind?.startsWith('payment-') && !paymentViewBlocked) body = this.renderPaymentEditor(data, state);
    else if (view) body = this.renderFreightEditor(data, state);
    else body = this.renderFreightFees(data);
    return `<div class="ocw-freight-workspace"><div class="ocw-freight-heading"><div><small>${ocTranslate("本票国际物流")}</small><strong>${this.escape(data.logistics?.approval_no || state.batchName || ocTranslate("来源待整理"))}</strong></div>${this.freightActionButton('refresh', '刷新', '', false, !!state.freightWriting)}</div>
      <nav class="ocw-freight-tabs" role="tablist" aria-label="${ocTranslate('实际支付流程与装箱变更')}">${tabs.map(([key, label]) => this.freightActionButton('freight-tab', label, `data-freight-tab="${key}" role="tab" aria-selected="${key === tab}"`, false, !!state.freightWriting)).join('')}</nav>
      ${data.historical ? `<p class="ocw-settlement-hint">${ocTranslate("历史版本仅供追溯，展示采用时的资料与证据。")}</p>` : ''}
      ${state.freightMessage ? `<p class="ocw-settlement-notice" role="status">${this.escape(state.freightMessage)}</p>` : ''}
      ${state.freightError ? `<p class="ocw-settlement-notice is-error" role="alert">${this.escape(state.freightError)}</p>` : ''}
      <div class="ocw-freight-panel" role="tabpanel">${body}</div></div>`;
  }

  renderFreightFees(data) {
    if (Array.isArray(data.payment_claims) || Array.isArray(data.payment_candidates) || data.payment_matching) return this.renderPaymentFees(data);
    const claims = data.freight?.claims || [];
    const header = `<thead><tr><th>${ocTranslate("费用名称")}</th><th>${ocTranslate("运单号")}</th><th>${ocTranslate("原始金额")}</th><th>${ocTranslate("当前采用金额")}</th><th>${ocTranslate("来源")}</th><th>${ocTranslate("状态")}</th><th>${ocTranslate("操作")}</th></tr></thead>`;
    return `<section><h4 class="ocw-freight-section-title">${ocTranslate("已审批运费")}</h4><div class="ocw-settlement-table-wrap"><table class="ocw-settlement-table ocw-freight-fee-table">${header}<tbody>${claims.map(row => `<tr><td>${this.escape(row.label || ocTranslate("运输费"))}</td><td>${this.escape(row.waybill || ocTranslate("原单明确关联"))}</td><td class="ocw-freight-money">${this.freightMoney(row.original_amount ?? (row.manual_corrected ? null : row.amount), row.currency)}</td><td class="ocw-freight-money"><strong>${this.freightMoney(row.applied_amount ?? row.amount, row.currency)}</strong></td><td>${this.freightSourceCell(row)}</td><td><span class="ocw-freight-status">${row.manual_corrected ? ocTranslate("人工更正") : ocTranslate("已采用")}</span></td><td><div class="ocw-freight-row-actions">${row.line_id ? this.freightActionButton('freight-evidence', '查看证据', `data-line="${this.escape(row.line_id)}"`) : ''}${!data.historical ? ['amount', 'replace', 'revoke'].map((action, index) => this.freightActionButton(`freight-${action}`, ['改金额', '换来源', '撤销采用'][index], `data-claim="${this.escape(row.id)}"`)).join('') : ''}</div></td></tr>`).join('') || `<tr><td colspan="7">${ocTranslate("尚未采用本票实际运费")}</td></tr>`}</tbody></table></div>
      <p class="ocw-settlement-hint">${ocTranslate("这里显示审批通过的账单费用；银行支付状态需凭证核实。")}</p>${(data.freight?.issues || []).map(issue => `<p class="ocw-settlement-notice">${this.escape(issue)}</p>`).join('')}</section>
      <p class="ocw-settlement-hint">${ocTranslate("装箱：{0}", [this.escape(data.packing?.message || ocTranslate("保留当前资料，变更单独核对"))])}</p>
      ${data.historical ? '' : `${this.renderBatchSettlementMatching(data)}<div class="ocw-settlement-toolbar"><h4 class="ocw-freight-section-title">${ocTranslate("本票费用候选")}</h4>${this.freightActionButton('search', '搜索本票支付证据')}</div>
      ${(data.candidates || []).map(candidate => `<section class="ocw-settlement-source"><div class="ocw-freight-heading"><div><strong>${this.escape(candidate.expense?.title || ocTranslate("支付流程"))}</strong><small>${this.escape(candidate.expense?.approval_no || candidate.expense?.instance || '')}</small></div>${this.freightActionButton('freight-source', '打开支付原单', `data-id="${this.escape(candidate.id)}"`)}</div>
        <p class="ocw-settlement-hint">${ocTranslate("整单合计，仅供核对：{0}", [this.freightMoney(candidate.expense?.amount, candidate.expense?.currency)])}</p><p>${this.escape(candidate.reason || ocTranslate("请核对本票费用明细"))}${candidate.expense?.approved === false ? ocTranslate(" · 审批未通过，仅供查看") : ''}</p>
        <div class="ocw-settlement-table-wrap"><table class="ocw-settlement-table">${header}<tbody>${(candidate.lines || []).map(row => `<tr><td>${this.escape(row.label || ocTranslate("运输费"))}</td><td>${this.escape(row.waybill || ocTranslate("正文明确关联"))}</td><td class="ocw-freight-money">${this.freightMoney(row.amount, row.currency)}</td><td>${row.adopted ? ocTranslate("已采用，见上表") : '—'}</td><td>${this.freightSourceCell(row, candidate.expense)}</td><td>${row.adopted ? ocTranslate("已采用") : row.available ? ocTranslate("可采用") : ocTranslate("待核对")}</td><td>${this.freightActionButton('freight-evidence', '查看证据', `data-line="${this.escape(row.id)}"`)}</td></tr>`).join('') || `<tr><td colspan="7">${ocTranslate("已找到相关单据，本票金额待核对；不会按整单总额或平均数采用。")}</td></tr>`}</tbody></table></div>
        ${(candidate.issues || []).map(issue => `<p class="ocw-settlement-notice">${this.escape(issue)}</p>`).join('')}<div class="ocw-settlement-toolbar">${(candidate.lines || []).length ? this.freightActionButton('freight-review', '核对本票费用', `data-id="${this.escape(candidate.id)}"`, true) : ''}${candidate.packing_available ? this.freightActionButton('packing-review', '查看装箱来源', `data-id="${this.escape(candidate.id)}"`) : ''}${this.freightActionButton('freight-reject', '不是本票', `data-id="${this.escape(candidate.id)}"`)}</div></section>`).join('')}`}`;
  }

  paymentFeeOptions(data = {}) {
    const mode = String(data.transport_mode || '').toUpperCase();
    const freight = mode === 'AIR' ? ['international_air_freight', '国际空运费'] : mode === 'EXPRESS'
      ? ['international_express_fee', '国际快递费'] : ['international_sea_freight', '国际海运费'];
    return [freight, ['customs_clearance_fee', '清关费'], ['import_tax', '进口税费'], ['destination_delivery', '目的地配送']];
  }

  paymentFeeLabel(key, data = {}) {
    return Object.fromEntries(this.paymentFeeOptions(data))[key] || key || ocTranslate("待分类费用");
  }

  paymentMethodLabel(candidate = {}) {
    return candidate.method === 'deepseek' ? ocTranslate("AI匹配（DeepSeek）") : candidate.method === 'manual' || candidate.method === 'reopened'
      ? ocTranslate("人工匹配") : ocTranslate("规则匹配");
  }

  paymentConfidence(candidate = {}) {
    const value = Number(candidate.confidence);
    return Number.isFinite(value) ? `${Math.round(value * 100)}%` : '—';
  }

  renderPaymentMatching(data, state = {}) {
    const rules = data.matching || {};
    const ai = data.payment_matching || {};
    const aiRunning = ['queued', 'running'].includes(ai.status);
    const showForm = state.paymentAiOpen === true;
    const range = ai.id ? ocTranslate("本轮候选范围 {0}–{1}", [this.escape((ai.offset || 0) + 1), this.escape((ai.offset || 0) + (ai.limit || ai.total || 0))]) : '本轮候选范围待选择';
    return `<section class="ocw-payment-matching"><div class="ocw-payment-match-actions"><div><strong>${ocTranslate("本票支付来源匹配")}</strong><small>${ocTranslate("打开只读取本地缓存；DeepSeek 仅在明确点击后调用。")}</small></div><div class="ocw-freight-row-actions">
      ${this.freightActionButton('payment-rule-match', '规则匹配', '', false, !!state.freightLoading)}
      ${this.freightActionButton('payment-ai-open', 'AI匹配（DeepSeek）', '', true, aiRunning || !!state.freightLoading)}</div></div>
      <div class="ocw-payment-match-status"><span>${ocTranslate("规则：{0} · 推荐 {1}", [this.escape(rules.status || ocTranslate("未运行")), this.escape(rules.recommended ?? 0)])}</span><span>${ocTranslate("AI：{0} · 模型 {1}", [this.escape(ai.status || ocTranslate("未运行")), this.escape(ai.model || ocTranslate("尚未调用"))])}</span><span>${range}</span></div>
      ${ai.error ? `<p class="ocw-settlement-notice is-error">${this.escape(ai.error)}</p>` : ''}
      ${showForm ? this.renderPaymentAiForm(data, state) : ''}
      ${ai.has_more ? `<div class="ocw-payment-expand">${state.paymentAiExpandPending ? `<span>${ocTranslate("将分析下一批最多 50 个同企业已审批候选，会产生新的 DeepSeek 调用。")}</span>` : ''}${this.freightActionButton(state.paymentAiExpandPending ? 'payment-ai-more-confirm' : 'payment-ai-more', state.paymentAiExpandPending ? '确认扩大候选范围' : '扩大候选范围', '', false, aiRunning)}</div>` : ''}</section>`;
  }

  renderPaymentAiForm(data, state = {}) {
    const draft = state.freightDraft || {};
    return `<div class="ocw-payment-ai-form"><p class="ocw-settlement-hint">${ocTranslate("可选提示只用于缩小同企业本地候选，AI 只提供建议，不会写入费用或装箱数据。")}</p><div class="ocw-payment-hint-grid">
      ${[['waybill','运单号'],['supplier','供应商'],['project','项目'],['date','日期'],['description','说明']].map(([key,label]) => `<label>${ocTranslate(label)}<input data-freight-field="${key}" value="${this.escape(draft[key] || '')}"></label>`).join('')}
      <label>${ocTranslate("本轮最多候选")}<input type="number" min="1" max="50" data-freight-field="ai_limit" value="${this.escape(draft.ai_limit || data.payment_matching?.limit || 30)}"></label></div>
      <div class="ocw-freight-row-actions">${this.freightActionButton('payment-ai-run', '开始 AI 匹配', '', true, !!state.freightLoading)}</div></div>`;
  }

  renderPaymentClaims(data) {
    const claims = data.payment_claims || [];
    const pending = data.payment_evidence_pending || [];
    const readOnly = this.paymentReadOnly(data);
    return `<section><h4 class="ocw-freight-section-title">${ocTranslate("已认领实际费用")}</h4><div class="ocw-settlement-table-wrap"><table class="ocw-settlement-table ocw-payment-claim-table"><thead><tr><th>${ocTranslate("费用分类")}</th><th>${ocTranslate("金额")}</th><th>${ocTranslate("来源审批")}</th><th>${ocTranslate("状态")}</th><th>${ocTranslate("操作")}</th></tr></thead><tbody>${claims.map(row => {
      const evidencePending = pending.some(item => item.claim_id === row.id || item.logical_fee_key === row.logical_fee_key);
      const actions = readOnly ? [] : row.available_actions || [];
      return `<tr><td>${this.escape(this.paymentFeeLabel(row.logical_fee_key, data))}</td><td class="ocw-freight-money"><strong>${this.freightMoney(row.amount, row.currency)}</strong><small>${this.escape(row.amount_status || 'ACTUAL')}</small></td><td>${this.escape(row.source_approval_no || row.source_title || ocTranslate("付款流程"))}</td><td><span class="ocw-freight-status ${row.stale ? 'is-stale' : ''}">${row.stale ? ocTranslate("来源已变更／待复核") : row.active ? ocTranslate("已认领") : this.escape(row.status || ocTranslate("已撤销"))}</span>${evidencePending ? `<small class="ocw-freight-missing">${ocTranslate("附件待归档／可重试")}</small>` : ''}</td><td><div class="ocw-freight-row-actions">${actions.map(action => this.freightActionButton('payment-amend', ({amount:'改金额',currency:'改币种',status:'改状态',reclassify:'重分类',revoke:'撤销认领'})[action] || action, `data-claim="${this.escape(row.id)}" data-payment-action="${this.escape(action)}"`, false, !!data.historical)).join('')}</div></td></tr>`;
    }).join('') || `<tr><td colspan="5">${ocTranslate("尚未认领本票实际支付费用")}</td></tr>`}</tbody></table></div></section>`;
  }

  renderPaymentAttachmentList(attachments = [], selectedKeys = [], data = {}, readOnly = false) {
    const origin = value => value === 'Comment' ? '评论附件' : '表单附件';
    const editable = selectedKeys.length > 0 && !readOnly;
    const validKeys = new Set(selectedKeys);
    return `<div class="ocw-payment-attachments"><strong>${ocTranslate("费用凭证建议")}</strong>${attachments.map(row => {
      const currentKeys = (row.logical_fee_keys || row.suggested_logical_fee_keys || selectedKeys).filter(key => !editable || validKeys.has(key));
      const options = editable ? `<select multiple aria-label="${ocTranslate('附件对应费用项')}" data-payment-attachment-keys="${this.escape(row.document_id)}">${this.paymentFeeOptions(data).filter(([key]) => validKeys.has(key)).map(([key, label]) => `<option value="${key}" ${currentKeys.includes(key) ? 'selected' : ''}>${ocTranslate(label)}</option>`).join('')}</select>` : '';
      return `<label class="ocw-payment-attachment ${row.availability !== 'available' ? 'is-pending' : ''}"><input type="checkbox" data-payment-attachment="${this.escape(row.document_id)}" ${row.selected ?? row.default_selected ? 'checked' : ''} ${row.required || readOnly ? 'disabled' : ''}><span><b>${this.escape(row.file_name || ocTranslate("附件"))}</b><small>${origin(row.origin)} · ${this.escape(row.attachment_type || ocTranslate("其他"))} · ${this.escape(row.status || ocTranslate("待归档"))}</small>${row.blocker ? `<small>${this.escape(row.blocker)}</small>` : ''}${options}</span></label>`;
    }).join('') || `<p class="ocw-settlement-hint">${ocTranslate("当前付款来源没有可建议关联的附件。")}</p>`}</div>`;
  }

  renderPaymentCandidate(candidate, data, state = {}) {
    const source = candidate.expense || {};
    const model = candidate.model || (candidate.method === 'deepseek' ? data.payment_matching?.model : '');
    const total = candidate.approval_total ?? source.amount;
    const currency = candidate.approval_currency || source.currency;
    return `<article class="ocw-payment-candidate"><div class="ocw-freight-heading"><div><small>${this.escape(source.process_code || ocTranslate("支付流程"))}</small><strong>${this.escape(source.title || ocTranslate("支付流程"))}</strong><span>${this.escape(source.approval_no || source.instance || '')}</span></div>${source.open_url ? this.freightActionButton('freight-source', '打开支付原单', `data-id="${this.escape(candidate.id)}"`) : ''}</div>
      <div class="ocw-payment-candidate-grid"><div><small>${ocTranslate("审批总额")}</small><strong>${this.freightMoney(total, currency)}</strong></div><div><small>${ocTranslate("来源额度")}</small><strong>${ocTranslate("已认领 {0} · 剩余 {1}", [this.escape(candidate.active_claimed_amount ?? candidate.claimed ?? 0), this.escape(candidate.remaining_amount ?? candidate.remaining ?? ocTranslate("待复核"))])}</strong></div><div><small>${ocTranslate("匹配方式／置信度")}</small><strong>${this.paymentMethodLabel(candidate)} · ${this.paymentConfidence(candidate)}</strong></div><div><small>${ocTranslate("模型／来源版本")}</small><strong>${this.escape(model || ocTranslate("不使用模型"))} · ${this.escape(candidate.source_revision || source.snapshot || ocTranslate("待核对"))}</strong></div></div>
      <p>${this.escape(candidate.reason || ocTranslate("请核对本票与支付流程的对应关系"))}</p>${this.renderPaymentAttachmentList(candidate.attachments || [])}
      ${this.paymentReadOnly(data) ? '' : `<div class="ocw-freight-row-actions">${this.freightActionButton('payment-review', '预览并认领费用', `data-id="${this.escape(candidate.id)}"`, true, source.approved === false)}${this.freightActionButton('payment-reject', '不是本票', `data-id="${this.escape(candidate.id)}"`)}</div>`}</article>`;
  }

  renderPaymentFees(data, state = this.batchSettlementState || {}) {
    const candidates = data.payment_candidates || data.candidates || [];
    const rejected = data.payment_rejected_candidates || [];
    const readOnly = this.paymentReadOnly(data);
    const sourceHistory = (data.payment_source_history || []).map(row => `<article class="ocw-settlement-source"><strong>${this.escape(row.title || row.workflow_template || ocTranslate("支付流程"))}</strong><p>${this.escape(row.approval_no || ocTranslate("审批号待核对"))}</p><small>${this.escape(row.workflow_template || ocTranslate("已确认支付来源"))} · ${this.escape(row.confirmed_at || '')}</small></article>`).join('');
    return `${sourceHistory ? `<section><h4 class="ocw-freight-section-title">${ocTranslate("已确认支付来源")}</h4>${sourceHistory}</section>` : ''}${this.renderPaymentClaims(data)}${readOnly ? '' : this.renderPaymentMatching(data, state)}
      <section><div class="ocw-settlement-toolbar"><h4 class="ocw-freight-section-title">${ocTranslate("待确认支付候选")}</h4></div>${candidates.map(row => this.renderPaymentCandidate(row, data, state)).join('') || `<p class="ocw-settlement-hint">${ocTranslate("尚未找到可靠候选。可先运行规则匹配，再按需使用 AI。")}</p>`}</section>
      ${rejected.length ? `<details class="ocw-payment-rejected"><summary>${ocTranslate("已否决候选 {0}", [rejected.length])}</summary>${rejected.map(row => `<p>${this.escape(row.expense?.approval_no || row.expense?.title || ocTranslate("支付流程"))} · ${this.escape(row.rejection_reason || ocTranslate("已否决"))} ${readOnly ? '' : this.freightActionButton('payment-reopen', '重新纳入', `data-id="${this.escape(row.id)}"`)}</p>`).join('')}</details>` : ''}
      ${(data.payment_blocking_reasons || []).map(message => `<p class="ocw-settlement-notice is-error">${this.escape(message)}</p>`).join('')}`;
  }

  freightSelectedLines(state) {
    const draft = state.freightDraft || {};
    const candidate = (state.data.candidates || []).find(row => row.id === draft.candidate_id);
    return { candidate, lines: (candidate?.lines || []).filter(row => (draft.line_ids || []).includes(row.id)) };
  }

  freightNeedsNegative(state) {
    if (state.freightView?.kind === 'payment-amend' && state.freightView?.action === 'amount') return Number(state.freightDraft?.amount) < 0;
    return state.freightView?.kind === 'amount' ? Number(state.freightDraft?.amount) < 0 : this.freightSelectedLines(state).lines.some(row => Number(row.amount) < 0);
  }

  renderFreightChangeSummary(state) {
    const view = state.freightView || {};
    const claim = (state.data.freight?.claims || []).find(row => row.id === view.claim_id);
    const { lines } = this.freightSelectedLines(state);
    const totals = {};
    for (const line of lines) totals[line.currency || ''] = (totals[line.currency || ''] || 0) + Number(line.amount);
    if (view.kind === 'amount' && claim) totals[claim.currency] = Number(state.freightDraft?.amount);
    const after = Object.entries(totals).map(([currency, amount]) => this.freightMoney(Number(amount.toFixed(6)), currency)).join(' + ') || (view.kind === 'revoke' ? '撤销该费用' : '请选择明细');
    let difference = '';
    if (claim && Object.keys(totals).length === 1 && Object.hasOwn(totals, claim.currency)) difference = this.freightMoney(Number((totals[claim.currency] - Number(claim.applied_amount ?? claim.amount)).toFixed(6)), claim.currency);
    return `<div class="ocw-freight-change-summary">${claim ? `<div><small>${ocTranslate("更正前")}</small><strong>${this.freightMoney(claim.applied_amount ?? claim.amount, claim.currency)}</strong><small>${this.escape(claim.approval_no || '')}</small></div>` : ''}<div><small>${claim ? ocTranslate("更正后") : ocTranslate("所选费用合计")}</small><strong>${after}</strong></div>${difference ? `<div><small>${ocTranslate("差额")}</small><strong>${difference}</strong></div>` : ''}</div>`;
  }

  paymentInitialRows(candidate, data) {
    const allowed = this.paymentFeeOptions(data);
    const firstKey = allowed[0][0];
    const lines = candidate.lines || [];
    if (!lines.length) return [{ source_line_id: 'approval_total', logical_fee_key: firstKey,
      amount: String(candidate.remaining_amount ?? candidate.remaining ?? ''), currency: candidate.approval_currency || candidate.expense?.currency || 'RMB',
      amount_status: 'ACTUAL', replace_claim_ids: [] }];
    return lines.filter(row => !row.adopted).map(row => ({ source_line_id: row.id,
      logical_fee_key: row.logical_fee_key || firstKey, amount: String(row.amount ?? ''), currency: row.currency || candidate.approval_currency || 'RMB',
      amount_status: 'ACTUAL', replace_claim_ids: [], _structured: true, _scope: row.scope || 'review', _label: row.label || '' }));
  }

  paymentPublicRows(draft = {}) {
    return (draft.payment_rows || []).map(row => ({ source_line_id: row.source_line_id,
      logical_fee_key: row.logical_fee_key, amount: String(row.amount ?? '').trim(), currency: String(row.currency || '').toUpperCase(),
      amount_status: String(row.amount_status || 'ACTUAL').toUpperCase(), replace_claim_ids: row.replace_claim_ids || [] }));
  }

  validatePaymentDraft(state) {
    const rows = this.paymentPublicRows(state.freightDraft || {});
    if (!rows.length) throw new Error(ocTranslate("请至少保留一条本票费用"));
    const seen = new Set(); let total = 0;
    for (const row of rows) {
      const amount = Number(row.amount);
      if (!row.logical_fee_key || !Number.isFinite(amount)) throw new Error(ocTranslate("请填写有效的费用分类和金额"));
      const key = `${row.source_line_id}:${row.logical_fee_key}`;
      if (seen.has(key)) throw new Error(ocTranslate("同一来源明细不能重复使用同一费用分类"));
      seen.add(key); total += Math.abs(amount);
    }
    const candidate = (state.data.payment_candidates || state.data.candidates || []).find(row => row.id === state.freightDraft.candidate_id);
    const remaining = Number(candidate?.remaining_amount ?? candidate?.remaining);
    if (Number.isFinite(remaining) && total > remaining + 0.000001) throw new Error(ocTranslate("本票认领金额超过来源剩余可认领金额 {0}", [remaining]));
    if (rows.some(row => row.replace_claim_ids.length) && !String(state.freightDraft?.reason || '').trim()) throw new Error(ocTranslate("替换已有认领必须填写原因"));
    if (rows.some(row => Number(row.amount) < 0) && state.freightDraft.negative_confirmed !== true) throw new Error(ocTranslate("请先确认负数冲抵／折扣"));
    return rows;
  }

  renderPaymentRow(row, index, data) {
    const fixed = row._structured && row._scope !== 'review';
    const options = this.paymentFeeOptions(data).map(([key, label]) => `<option value="${key}" ${row.logical_fee_key === key ? 'selected' : ''}>${ocTranslate(label)}</option>`).join('');
    const firstForKey = (data.payment_rows || []).findIndex(item => item.logical_fee_key === row.logical_fee_key) === index;
    const replaceable = firstForKey ? (data.payment_claims || []).filter(claim => claim.active === true && claim.logical_fee_key === row.logical_fee_key) : [];
    const replacementChoices = replaceable.length ? `<div class="ocw-payment-replacements"><strong>${ocTranslate("替换现有认领")}</strong>${replaceable.map(claim => `<label><input type="checkbox" data-payment-replace-row="${index}" data-payment-replace-claim="${this.escape(claim.id)}" ${(row.replace_claim_ids || []).includes(claim.id) ? 'checked' : ''}>${ocTranslate("替换此认领：{0} {1} · {2}", [this.escape(this.paymentFeeLabel(claim.logical_fee_key, data)), this.freightMoney(claim.amount, claim.currency), this.escape(claim.source_approval_no || claim.source_title || ocTranslate("已有来源"))])}</label>`).join('')}<small>${ocTranslate("只有明确勾选的现有认领会被替换；必须填写下方说明。")}</small></div>` : '';
    return `<tr data-payment-row="${index}"><td>${this.escape(row._label || (row.source_line_id === 'approval_total' ? ocTranslate("审批总额／人工拆分") : row.source_line_id))}</td><td><select data-payment-row-field="logical_fee_key" data-payment-row-index="${index}" ${fixed ? 'disabled' : ''}>${options}</select>${fixed ? `<small>${ocTranslate("来源明细已明确分类")}</small>` : ''}</td>
      <td><input type="number" step="any" data-payment-row-field="amount" data-payment-row-index="${index}" value="${this.escape(row.amount ?? '')}" ${row._structured ? 'readonly' : ''}></td><td><select data-payment-row-field="currency" data-payment-row-index="${index}" ${row._structured ? 'disabled' : ''}>${['RMB','USD','MXN'].map(value => `<option ${row.currency === value ? 'selected' : ''}>${value}</option>`).join('')}</select></td>
      <td><select data-payment-row-field="amount_status" data-payment-row-index="${index}">${[['ACTUAL','实际'],['ESTIMATED','暂估'],['NOT_INCURRED','未发生'],['INCLUDED','已包含']].map(([value,label]) => `<option value="${value}" ${row.amount_status === value ? 'selected' : ''}>${ocTranslate(label)}</option>`).join('')}</select></td><td>${row.source_line_id === 'approval_total' ? this.freightActionButton('payment-split-remove', '删除拆分', `data-row-index="${index}"`, false, (data.payment_rows || []).length === 1) : ocTranslate("结构化明细金额只读")}${replacementChoices}</td></tr>`;
  }

  renderPaymentAdoptionEditor(data, state) {
    const draft = state.freightDraft || {};
    const candidate = (data.payment_candidates || data.candidates || []).find(row => row.id === draft.candidate_id) || {};
    const rows = draft.payment_rows || [];
    const remaining = candidate.remaining_amount ?? candidate.remaining;
    return `<h4 class="ocw-freight-section-title">${ocTranslate("预览并认领本票费用")}</h4><p>${this.escape(candidate.expense?.title || ocTranslate("支付流程"))} · ${this.escape(candidate.expense?.approval_no || '')}</p>
      <p class="ocw-settlement-hint">${ocTranslate("审批总额 {0} · 已认领 {1} · 剩余 {2}。结构化明细不可改金额；只有审批总额时可人工拆分。", [this.freightMoney(candidate.approval_total ?? candidate.expense?.amount, candidate.approval_currency || candidate.expense?.currency), this.escape(candidate.active_claimed_amount ?? candidate.claimed ?? 0), this.escape(remaining ?? ocTranslate("待复核"))])}</p>
      <div class="ocw-settlement-table-wrap"><table class="ocw-settlement-table ocw-payment-adoption-table"><thead><tr><th>${ocTranslate("来源明细")}</th><th>${ocTranslate("费用分类")}</th><th>${ocTranslate("本票认领金额")}</th><th>${ocTranslate("币种")}</th><th>${ocTranslate("状态")}</th><th>${ocTranslate("操作")}</th></tr></thead><tbody>${rows.map((row,index) => this.renderPaymentRow(row,index,{...data,payment_rows:rows})).join('')}</tbody></table></div>
      ${rows.some(row => row.source_line_id === 'approval_total') ? this.freightActionButton('payment-split-add', '新增费用拆分') : ''}
      ${this.renderPaymentAttachmentList(draft.attachments || candidate.attachments || [], rows.map(row => row.logical_fee_key), data)}
      <label class="ocw-freight-negative"><input type="checkbox" data-freight-field="negative_confirmed" ${draft.negative_confirmed ? 'checked' : ''}>${ocTranslate("已核对负数冲抵／折扣")}</label>
      <label class="ocw-freight-field">${ocTranslate("替换或特殊处理说明")}<textarea rows="2" data-freight-field="reason">${this.escape(draft.reason || '')}</textarea></label>`;
  }

  renderPaymentPreview(state) {
    const preview = state.paymentPreview || {};
    return `<h4 class="ocw-freight-section-title">${ocTranslate("确认费用差异预览")}</h4><p class="ocw-settlement-hint">${ocTranslate("预览仅保存服务端令牌，未修改本票费用。确认时将重新校验来源、额度、附件、版本和编辑租约。")}</p><div class="ocw-freight-change-summary">${(preview.impacted_fees || []).map(row => `<div><small>${this.escape(this.paymentFeeLabel(row.logical_fee_key, state.data))}</small><strong>${this.freightMoney(row.amount, row.currency)}</strong><small>${this.escape(row.amount_status || 'ACTUAL')}</small></div>`).join('')}</div>
      <p>${ocTranslate("来源剩余：{0}", [this.freightMoney(preview.remaining, preview.source_currency)])}</p>${(preview.warnings || preview.payment_blocking_reasons || []).map(message => `<p class="ocw-settlement-notice is-error">${this.escape(message)}</p>`).join('')}${this.renderPaymentAttachmentList(preview.attachments || [], [], state.data, true)}<p class="ocw-settlement-hint">${ocTranslate("修改附件选择需返回候选重新预览。")}</p>`;
  }

  renderPaymentAmendEditor(data, state) {
    const draft = state.freightDraft || {}; const claim = (data.payment_claims || []).find(row => row.id === state.freightView?.claim_id) || {};
    const action = state.freightView?.action; const labels = {amount:ocTranslate("修改认领金额"),currency:ocTranslate("修改币种"),status:ocTranslate("修改金额状态"),reclassify:ocTranslate("重新分类"),revoke:ocTranslate("撤销认领")};
    return `<h4 class="ocw-freight-section-title">${labels[action] || ocTranslate("费用更正")}</h4><p>${this.escape(this.paymentFeeLabel(claim.logical_fee_key, data))} · ${this.freightMoney(claim.amount, claim.currency)} · ${this.escape(claim.source_approval_no || '')}</p>
      ${action === 'amount' ? `<label class="ocw-freight-field">${ocTranslate("金额")}<input type="number" step="any" data-freight-field="amount" value="${this.escape(draft.amount ?? claim.amount ?? '')}"></label>` : ''}
      ${action === 'amount' ? `<label class="ocw-freight-negative"><input type="checkbox" data-freight-field="negative_confirmed" ${draft.negative_confirmed ? 'checked' : ''}>${ocTranslate("若金额为负数，确认这是冲抵／折扣")}</label>` : ''}
      ${action === 'currency' ? `<label class="ocw-freight-field">${ocTranslate("币种")}<select data-freight-field="currency">${['RMB','USD','MXN'].map(value => `<option ${draft.currency === value ? 'selected' : ''}>${value}</option>`).join('')}</select></label>` : ''}
      ${['currency','status'].includes(action) ? `<label class="ocw-freight-field">${ocTranslate("金额状态")}<select data-freight-field="amount_status">${['ACTUAL','ESTIMATED','NOT_INCURRED','INCLUDED'].map(value => `<option ${draft.amount_status === value ? 'selected' : ''}>${value}</option>`).join('')}</select></label>` : ''}
      ${action === 'reclassify' ? `<label class="ocw-freight-field">${ocTranslate("费用分类")}<select data-freight-field="logical_fee_key">${this.paymentFeeOptions(data).map(([key,label]) => `<option value="${key}" ${draft.logical_fee_key === key ? 'selected' : ''}>${ocTranslate(label)}</option>`).join('')}</select></label>` : ''}
      ${action === 'revoke' ? `<p class="ocw-settlement-notice">${ocTranslate("撤销后释放来源可认领余额，并保留完整审计记录。")}</p>` : ''}
      <label class="ocw-freight-field">${ocTranslate("更正原因（必填）")}<textarea rows="3" data-freight-field="reason">${this.escape(draft.reason || '')}</textarea></label>`;
  }

  renderPaymentEditor(data, state) {
    if (state.freightView?.kind === 'payment-preview') return this.renderPaymentPreview(state);
    if (state.freightView?.kind === 'payment-amend') return this.renderPaymentAmendEditor(data, state);
    if (state.freightView?.kind === 'payment-decision') return `<h4 class="ocw-freight-section-title">${state.freightView.action === 'reopen' ? ocTranslate("重新纳入付款候选") : ocTranslate("否决付款候选")}</h4><p>${this.escape(state.freightView.label || ocTranslate("付款候选"))}</p><label class="ocw-freight-field">${ocTranslate("核对依据（必填）")}<textarea rows="3" data-freight-field="reason">${this.escape(state.freightDraft?.reason || '')}</textarea></label>`;
    return this.renderPaymentAdoptionEditor(data, state);
  }

  renderFreightEditor(data, state) {
    const view = state.freightView || {};
    const draft = state.freightDraft || {};
    const claim = (data.freight?.claims || []).find(row => row.id === view.claim_id);
    const titles = { amount: '修改当前采用金额', replace: '更换费用来源', revoke: '撤销费用采用', adopt: '核对本票费用', reject: '标记不是本票', checks: '核对保留的重量与体积' };
    const { candidate } = this.freightSelectedLines(state);
    const choices = ['adopt', 'replace'].includes(view.kind);
    return `<h4 class="ocw-freight-section-title">${titles[view.kind] || ocTranslate("本票费用核对")}</h4>${claim ? `<p>${ocTranslate("{0} · 运单 {1}", [this.escape(claim.label || ocTranslate("运输费")), this.escape(claim.waybill || ocTranslate("原单明确关联"))])}</p><p class="ocw-settlement-hint">${ocTranslate("原始证据金额：{0}", [this.freightMoney(claim.original_amount ?? claim.amount, claim.currency)])}<br>${ocTranslate("来源审批：{0}", [this.escape(claim.approval_no || '')])}</p>` : ''}
      ${view.kind === 'amount' ? `<label class="ocw-freight-field">${ocTranslate("当前采用金额（{0}）", [this.escape(claim?.currency || '')])}<input type="number" step="any" data-freight-field="amount" value="${this.escape(draft.amount ?? '')}"></label><p class="ocw-settlement-hint">${ocTranslate("保留原始证据金额，人工更正值用于本票核算。")}</p>` : ''}
      ${choices ? `${view.kind === 'replace' ? `<label class="ocw-freight-field">${ocTranslate("选择新的费用来源")}<select data-freight-field="candidate_id"><option value="">${ocTranslate("请选择来源")}</option>${(data.candidates || []).filter(row => row.expense?.approved !== false).map(row => `<option value="${this.escape(row.id)}" ${draft.candidate_id === row.id ? 'selected' : ''}>${this.escape(row.expense?.approval_no || row.expense?.title || ocTranslate("支付流程"))}</option>`).join('')}</select></label>` : `<p>${ocTranslate("支付审批：{0}", [this.escape(candidate?.expense?.approval_no || '')])}</p>`}
      <p class="ocw-settlement-hint">${ocTranslate("勾选本票明细，提交后采用所选金额。")}</p><div class="ocw-settlement-table-wrap"><table class="ocw-settlement-table"><thead><tr><th>${ocTranslate("采用")}</th><th>${ocTranslate("费用名称")}</th><th>${ocTranslate("运单号")}</th><th>${ocTranslate("金额")}</th><th>${ocTranslate("来源")}</th><th>${ocTranslate("状态")}</th><th>${ocTranslate("证据")}</th></tr></thead><tbody>${(candidate?.lines || []).map(row => `<tr><td><input type="checkbox" aria-label="${this.escape(ocTranslate("采用{0}", [row.label || '费用']))}" data-freight-line="${this.escape(row.id)}" ${(draft.line_ids || []).includes(row.id) ? 'checked' : ''} ${row.available && !row.adopted && candidate.expense?.approved !== false ? '' : 'disabled'}></td><td>${this.escape(row.label || ocTranslate("运输费"))}</td><td>${this.escape(row.waybill || ocTranslate("正文明确关联"))}</td><td class="ocw-freight-money">${this.freightMoney(row.amount, row.currency)}</td><td>${this.freightSourceCell(row, candidate?.expense)}</td><td>${row.adopted ? ocTranslate("已采用") : row.available ? ocTranslate("可采用") : ocTranslate("不可采用")}</td><td>${state.inline ? this.escape(this.freightEvidence(row.evidence)) : this.freightActionButton('freight-evidence', '查看证据', `data-line="${this.escape(row.id)}"`)}</td></tr>`).join('') || `<tr><td colspan="7">${ocTranslate("请选择有可采用费用行的来源")}</td></tr>`}</tbody></table></div>` : ''}
      ${view.kind === 'checks' ? `<p>${ocTranslate("请核实保留值仍适用于本票。需要修改时先在物料表补充。")}</p>${(data.packing_checks || []).map(row => `<label class="ocw-settlement-search-row"><input type="checkbox" data-freight-check="${this.escape(row.name)}" ${(draft.check_ids || []).includes(row.name) ? 'checked' : ''}><span>${this.escape(row.material_code)} ${this.escape(row.product_name)}<small>${ocTranslate("毛重 {0} kg · 体积 {1} m³", [this.escape(row.gross_weight_kg ?? ocTranslate("待补")), this.escape(row.volume_m3 ?? ocTranslate("待补"))])}</small></span></label>`).join('')}` : ''}
      ${['amount', 'replace', 'revoke', 'adopt'].includes(view.kind) ? `<div data-freight-summary>${this.renderFreightChangeSummary(state)}</div>` : ''}
      ${view.kind === 'revoke' ? `<p class="ocw-settlement-notice">${ocTranslate("保存后，本票不再采用这条费用。原始来源与更正记录仍可追溯。")}</p>` : ''}
      ${view.kind === 'reject' ? `<p>${ocTranslate("请说明该支付来源与本票不对应的依据。")}</p>` : ''}
      ${['amount', 'replace', 'adopt'].includes(view.kind) ? `<label class="ocw-freight-negative" data-freight-negative ${this.freightNeedsNegative(state) ? '' : 'hidden'}><input type="checkbox" data-freight-field="negative_confirmed" ${draft.negative_confirmed ? 'checked' : ''}>${ocTranslate("已核对负数冲抵／折扣")}</label>` : ''}
      ${view.kind !== 'adopt' ? `<label class="ocw-freight-field">${view.kind === 'checks' || view.kind === 'reject' ? ocTranslate("核对依据") : ocTranslate("更正原因")}<textarea rows="3" data-freight-field="reason" placeholder="${ocTranslate('请填写本次核对的依据')}">${this.escape(draft.reason || '')}</textarea></label>` : ''}`;
  }

  renderFreightPackingSources(data, state) {
    const sourceGroups = [['approval_form', '正文'], ['approval_attachment', '附件'], ['approval_comment_attachment', '评论附件'], ['approval_comment', '评论']];
    const preview = state.packingPreview;
    const sources = state.packingSources || [];
    const missing = value => value === null || value === undefined || value === '' ? `<span class="ocw-freight-missing">${ocTranslate("待补")}</span>` : this.escape(value);
    return `<div class="ocw-settlement-toolbar"><h4 class="ocw-freight-section-title">${ocTranslate("选择整票装箱来源")}</h4>${this.freightActionButton('packing-sources-refresh', '刷新来源', '', false, !!state.freightLoading)}</div>
      <p class="ocw-settlement-hint">${this.escape(data.packing?.message || ocTranslate("选择一个具体来源，预览后替换本票装箱资料。"))}</p>
      ${state.freightLoading ? `<p role="status">${ocTranslate("正在读取来源与预览…")}</p>` : ''}
      <div class="ocw-settlement-table-wrap"><table class="ocw-settlement-table ocw-freight-packing-sources"><thead><tr><th>${ocTranslate("类型")}</th><th>${ocTranslate("来源名称")}</th><th>${ocTranslate("审批号")}</th><th>${ocTranslate("人员／时间")}</th><th>${ocTranslate("明细")}</th><th>${ocTranslate("状态")}</th><th>${ocTranslate("操作")}</th></tr></thead><tbody>${sourceGroups.map(([kind, label]) => { const rows = sources.filter(row => row.source_kind === kind); return rows.length ? rows.map((row, index) => `<tr class="${state.packingSelected === row.id ? 'is-selected' : ''}"><td>${index ? '' : label}</td><td>${this.escape(row.source_label || label)}</td><td>${this.escape(row.approval_no || '')}</td><td>${this.escape(row.actor_name || ocTranslate("未提供"))}<small>${this.escape(row.occurred_at || '')}</small></td><td>${ocTranslate("{0} 行", [this.escape(row.row_count ?? 0)])}</td><td>${row.can_adopt ? row.complete ? ocTranslate("可采用") : ocTranslate("资料待补") : ocTranslate("仅供查看")}${(row.issues || []).map(issue => `<small>${this.escape(issue)}</small>`).join('')}</td><td>${this.freightActionButton('packing-source-preview', '预览来源', `data-id="${this.escape(row.id)}"`, false, !!state.freightLoading)}</td></tr>`).join('') : `<tr><td>${ocTranslate(label)}</td><td colspan="6" class="ocw-settlement-hint">${state.packingSources ? ocTranslate("暂无可用来源") : ocTranslate("来源尚未读取")}</td></tr>`; }).join('')}</tbody></table></div>
      ${preview ? `<section class="ocw-settlement-source" data-freight-packing-preview><div class="ocw-freight-heading"><div><strong>${ocTranslate("来源预览")}</strong><small>${this.escape(preview.selection?.descriptor?.source_label || preview.selection?.source_label || sources.find(row => row.id === state.packingSelected)?.source_label || '')}</small></div></div>
      <div class="ocw-freight-change-summary"><div><small>${ocTranslate("本次采用")}</small><strong>${ocTranslate("{0} 行", [this.escape((preview.goods || []).length)])}</strong></div><div><small>${ocTranslate("相对当前资料")}</small><strong>${ocTranslate("新增 {0} · 移除 {1}", [this.escape(preview.new_count ?? 0), this.escape(preview.removed_count ?? 0)])}</strong></div></div>
      <p class="ocw-settlement-notice">${ocTranslate("采用后将完整替换本票装箱资料，旧行会从新版本移除。{0}{1}", [preview.complete ? '' : '此来源资料不完整，缺失值保持待补。', (preview.missing_fields || []).length ? ocTranslate(" 待补：{0}", [this.escape(preview.missing_fields.join('、'))]) : ''])}</p>
      <div class="ocw-settlement-table-wrap"><table class="ocw-settlement-table ocw-freight-packing-goods"><thead><tr><th>${ocTranslate("SKU／物料编码")}</th><th>${ocTranslate("品名")}</th><th>${ocTranslate("规格")}</th><th>${ocTranslate("数量")}</th><th>${ocTranslate("单位")}</th><th>${ocTranslate("毛重 kg")}</th><th>${ocTranslate("净重 kg")}</th><th>${ocTranslate("体积 m³")}</th><th>${ocTranslate("包装")}</th></tr></thead><tbody>${(preview.goods || []).map(row => `<tr><td>${missing(row.material_code)}</td><td>${missing(row.product_name)}</td><td>${missing(row.spec_model)}</td><td>${missing(row.quantity)}</td><td>${missing(row.unit)}</td><td>${missing(row.physical?.gross_weight_kg ?? row.gross_weight_kg)}</td><td>${missing(row.physical?.net_weight_kg ?? row.net_weight_kg)}</td><td>${missing(row.physical?.volume_m3 ?? row.volume_m3)}</td><td>${missing(row.packaging && typeof row.packaging === 'object' ? Object.entries(row.packaging).map(([key, value]) => `${({package_count:'件数',package_type:'包装',carton_count:'箱数',length_cm:'长 cm',width_cm:'宽 cm',height_cm:'高 cm'})[key] || key}：${value}`).join('；') : row.packaging)}</td></tr>`).join('') || `<tr><td colspan="9">${ocTranslate("未取得可采用的装箱明细")}</td></tr>`}</tbody></table></div>
      ${!this.canAdoptFreightPacking(state) ? `<p class="ocw-settlement-notice">${ocTranslate("此预览不可采用，请核对审批状态、来源和解析结果。")}</p>` : ''}
      <details open><summary>${ocTranslate("原始来源与证据")}</summary>${this.renderFreightScopedEvidence(preview.selection || {})}</details></section>` : `<p class="ocw-settlement-hint">${ocTranslate("点击具体来源预览整票明细，预览不会修改当前资料。")}</p>`}
      ${!data.historical && data.packing_checks?.length ? this.freightActionButton('packing-checks', '核对保留的重量与体积') : ''}`;
  }

  renderFreightScopedEvidence(selection) {
    const descriptor = selection.descriptor || selection;
    const goods = selection.goods || [];
    return `<p>${ocTranslate("审批号：{0}", [this.escape(descriptor.approval_no || '')])}</p><p>${this.escape(this.freightEvidence(descriptor.evidence || {}))}</p>${selection.text ? `<pre class="ocw-freight-evidence-text">${this.escape(selection.text)}</pre>` : ''}${goods.map(row => `<p>${this.escape(row.material_code || row.product_name || ocTranslate("明细"))}<small>${this.escape(this.freightEvidence(row.evidence || {}))}</small></p>`).join('')}${!selection.text && !goods.length ? `<p class="ocw-settlement-hint">${ocTranslate("此来源未提供可展示的原始文本，请查看来源文件。")}</p>` : ''}`;
  }

  canAdoptFreightPacking(state) {
    const preview = state.packingPreview;
    return !state.data?.historical && preview?.selection?.can_adopt === true && !preview.invalid && preview.approved !== false && (preview.goods || []).length > 0;
  }

  renderFreightInlineEvidence(state) {
    const row = state.freightEvidenceRow;
    if (!row) return `<p role="status">${ocTranslate("正在读取本票账单行证据…")}</p>`;
    return `<h4 class="ocw-freight-section-title">${ocTranslate("本票账单行证据")}</h4><div class="ocw-freight-change-summary"><div><small>${ocTranslate("支付审批")}</small><strong>${this.escape(row.source?.approval_no || '')}</strong></div><div><small>${ocTranslate("本票运单")}</small><strong>${this.escape(row.waybill || row.approval_no || ocTranslate("原单明确关联"))}</strong></div><div><small>${this.escape(row.label || ocTranslate("费用金额"))}</small><strong>${this.freightMoney(row.amount, row.currency)}</strong></div></div><p>${this.escape(this.freightEvidence(row.evidence || {}))}</p><p>${ocTranslate("账单计费重量：{0}（不直接覆盖装箱毛重）", [this.escape(row.billing_weight ?? ocTranslate("未提供"))])}</p><pre class="ocw-freight-evidence-text">${this.escape(row.cargo_text || ocTranslate("本行未提供物料明细"))}</pre><small>${ocTranslate("本地归档证据；只展示本票行。")}</small>`;
  }

  renderFreightAudit(data) {
    const labels = { material_ai_payment_match_confirmed:ocTranslate("AI 填充确认支付来源"), freight_amount:ocTranslate("更正费用金额"), freight_replace:ocTranslate("更换费用来源"), freight_revoke:ocTranslate("撤销费用采用"), freight_lines_adopted:ocTranslate("采用运费"), packing_source_replaced:ocTranslate("替换装箱来源"), packing_changes_adopted:ocTranslate("采用装箱资料"), freight_packing_verified:ocTranslate("核对装箱资料"), freight_source_pending:ocTranslate("来源已更新，等待核对"), freight_application_recovered:ocTranslate("恢复费用采用"), freight_amount_corrected:ocTranslate("更正费用金额"), amount:ocTranslate("更正费用金额"), freight_claim_amount:ocTranslate("更正费用金额"), freight_claim_replaced:ocTranslate("更换费用来源"), replace:ocTranslate("更换费用来源"), freight_claim_revoked:ocTranslate("撤销费用采用"), revoke:ocTranslate("撤销费用采用"), freight_confirmed:ocTranslate("采用运费"), confirm:ocTranslate("采用运费"), freight_rejected:ocTranslate("标记不是本票"), rejected:ocTranslate("标记不是本票"), packing_source_confirmed:ocTranslate("替换装箱来源"), packing_adopted:ocTranslate("采用装箱资料"), freight_packing_confirmed:ocTranslate("采用装箱资料"), packing_checks_resolved:ocTranslate("核对装箱资料"), amount_corrected:ocTranslate("更正费用金额"), source_replaced:ocTranslate("更换费用来源"), claim_revoked:ocTranslate("撤销费用采用") };
    const values = claims => (Array.isArray(claims) ? claims : []).map(c => `${c.applied_amount ?? c.amount ?? '待核对'} ${c.currency || ''} · ${c.approval_no || ''}`).join('；') || '无采用费用';
    const changes = row => row.action === 'material_ai_payment_match_confirmed'
      ? ocTranslate("支付来源：{0}", [row.source_approval_no || row.source_title || '已确认'])
      : row.action === 'packing_source_replaced'
      ? ocTranslate("来源：{0}；{1} → {2}", [row.source || '资料', row.old_version || '', row.version || ''])
      : (row.before || row.after) ? `${values(row.before)} → ${values(row.after)}` : '';
    return `<h4 class="ocw-freight-section-title">${ocTranslate("操作记录")}</h4><div class="ocw-settlement-table-wrap"><table class="ocw-settlement-table"><thead><tr><th>${ocTranslate("时间")}</th><th>${ocTranslate("操作")}</th><th>${ocTranslate("操作人")}</th><th>${ocTranslate("变更前后／来源")}</th><th>${ocTranslate("依据")}</th></tr></thead><tbody>${(data.audit || []).map(row => `<tr><td>${this.escape(row.created_at || '')}</td><td>${this.escape(labels[row.action] || ocTranslate("来源与采用记录更新"))}</td><td>${this.escape(row.actor || '')}</td><td>${this.escape(changes(row))}</td><td>${this.escape(row.reason || '—')}</td></tr>`).join('') || `<tr><td colspan="5">${ocTranslate("暂无操作记录")}</td></tr>`}</tbody></table></div>`;
  }

  renderFreightSearch(state) {
    const search = state.freightSearch || {};
    const draft = state.freightDraft || {};
    return `<h4 class="ocw-freight-section-title">${ocTranslate("搜索本票支付证据")}</h4><label class="ocw-freight-field">${ocTranslate("审批号、单据号或原单关键词")}<input data-freight-field="query" value="${this.escape(draft.query || '')}"></label><label class="ocw-freight-field">${ocTranslate("人工关联依据")}<textarea data-freight-field="reason" rows="2">${this.escape(draft.reason || '')}</textarea></label><p class="ocw-settlement-hint">${ocTranslate("请填写该支付来源与本票国际物流的对应关系，选择后在当前窗口核对费用。")}</p>${this.freightActionButton('freight-search-run', '搜索', '', true, !!state.freightLoading)}${state.freightLoading ? `<p role="status">${ocTranslate("正在搜索…")}</p>` : ''}
      ${(search.items || []).map(row => `<div class="ocw-settlement-source"><strong>${this.escape(row.title || ocTranslate("支付流程"))}</strong><p>${this.escape(row.approval_no || row.instance || '')}</p><p>${this.escape(this.settlementAmount(row))}</p>${row.invalid ? `<small>${ocTranslate("来源已失效")}</small>` : this.freightActionButton('freight-search-choose', '选择并复核', `data-id="${this.escape(row.id)}"`)}</div>`).join('') || (search.loaded ? `<p>${ocTranslate("没有符合关键词的支付来源，请调整关键词。")}</p>` : '')}<div class="ocw-settlement-toolbar">${search.pages?.length ? this.freightActionButton('freight-search-previous', '上一页') : ''}${search.has_more ? this.freightActionButton('freight-search-next', '下一页') : ''}</div>`;
  }

  renderFreightWorkspace(state) {
    if (!state.open) return;
    const $wrapper = state.dialog.$wrapper;
    $wrapper?.addClass?.('ocw-freight-modal');
    if (state.packingVersion && state.packingVersion !== state.versionName) { state.packingSources = null; state.packingPreview = null; state.packingSelected = null; }
    this.settlementBody(state, this.renderFreightContent(state.data || {}, state));
    const view = state.freightView;
    const disabled = !!(state.freightWriting || state.freightLoading);
    const paymentWritable = !this.paymentReadOnly(state.data || {});
    let actions = '';
    if (view) actions += this.freightActionButton('freight-back', view.kind === 'evidence' ? '返回核对' : view.kind === 'payment-preview' ? '返回修改附件／费用' : '返回列表', '', false, disabled);
    if (view?.kind === 'evidence' && state.freightEvidenceRow?.source?.open_url) actions += this.freightActionButton('freight-evidence-source', '打开支付原单');
    if (paymentWritable && view?.kind === 'payment-adopt') actions += this.freightActionButton('payment-preview', '生成费用差异预览', '', true, disabled);
    if (paymentWritable && view?.kind === 'payment-preview') actions += this.freightActionButton('payment-confirm', '确认认领费用', '', true, disabled || !!(state.paymentPreview?.payment_blocking_reasons || []).length);
    if (paymentWritable && view?.kind === 'payment-amend') actions += this.freightActionButton('payment-amend-save', '保存费用更正', '', true, disabled);
    if (paymentWritable && view?.kind === 'payment-decision') actions += this.freightActionButton('payment-decision-save', '保存核对记录', '', true, disabled);
    if (!state.data?.historical && view && ['amount','replace','revoke','adopt','reject','checks'].includes(view.kind)) actions += this.freightActionButton('freight-save', ({amount:'保存金额更正',replace:'确认更换来源',revoke:'确认撤销采用',adopt:'确认采用所选费用',reject:'保存否决记录',checks:'确认所选装箱资料适用'})[view.kind], '', true, disabled);
    if (state.freightTab === 'packing' && !view && this.canAdoptFreightPacking(state)) actions += this.freightActionButton('packing-source-confirm', '采用此来源并替换本票装箱资料', '', true, disabled);
    const footer = state.data?.historical ? '历史版本 · 只读' : '费用与装箱资料分别核对、分别采用';
    this.settlementActions(state, `<div class="ocw-freight-footer"><span>${state.freightWriting ? ocTranslate("正在处理，请稍候…") : footer}</span><div class="ocw-freight-row-actions">${actions}</div></div>`);
    this.bindFreightInputs(state);
  }

  captureFreightDraft(state) {
    const $wrapper = state.dialog.$wrapper;
    const draft = state.freightDraft ||= {};
    $wrapper.find('[data-freight-field]').each((_, element) => { const key = element.getAttribute('data-freight-field'); draft[key] = element.type === 'checkbox' ? element.checked : element.value; });
    $wrapper.find('[data-payment-row-field]').each((_, element) => { const index = Number(element.getAttribute('data-payment-row-index')); const key = element.getAttribute('data-payment-row-field'); if (draft.payment_rows?.[index]) draft.payment_rows[index][key] = element.value; });
    if ($wrapper.find('[data-payment-replace-claim]').length) {
      const publicClaims = new Map((state.data?.payment_claims || []).filter(row => row.active === true).map(row => [String(row.id), row]));
      for (const row of draft.payment_rows || []) row.replace_claim_ids = [];
      $wrapper.find('[data-payment-replace-claim]:checked').each((_, element) => {
        const index = Number(element.getAttribute('data-payment-replace-row'));
        const claimId = String(element.getAttribute('data-payment-replace-claim') || '');
        const row = draft.payment_rows?.[index]; const claim = publicClaims.get(claimId);
        if (row && claim?.logical_fee_key === row.logical_fee_key) row.replace_claim_ids.push(claimId);
      });
    }
    if ($wrapper.find('[data-payment-attachment]').length) {
      const candidate = (state.data?.payment_candidates || state.data?.candidates || []).find(row => row.id === draft.candidate_id) || {};
      const source = draft.attachments || candidate.attachments || [];
      const controls = {};
      $wrapper.find('[data-payment-attachment]').each((_, element) => {
        const id = element.getAttribute('data-payment-attachment');
        controls[id] = { ...(controls[id] || {}), selected: !!element.checked };
      });
      $wrapper.find('[data-payment-attachment-keys]').each((_, element) => {
        const id = element.getAttribute('data-payment-attachment-keys');
        controls[id] = { ...(controls[id] || {}), logical_fee_keys: Array.from(element.selectedOptions || []).map(option => option.value) };
      });
      draft.attachments = source.map(row => {
        const choice = controls[String(row.document_id)] || {};
        const selected = row.required || !!choice.selected;
        const defaults = (row.suggested_logical_fee_keys || []).filter(key => (draft.payment_rows || []).some(item => item.logical_fee_key === key));
        const logicalFeeKeys = choice.logical_fee_keys || defaults;
        return { ...row, selected, logical_fee_keys: logicalFeeKeys.length ? logicalFeeKeys : [...new Set((draft.payment_rows || []).map(item => item.logical_fee_key).filter(Boolean))] };
      });
    }
    if ($wrapper.find('[data-freight-line]').length) draft.line_ids = $wrapper.find('[data-freight-line]:checked').map((_, element) => element.getAttribute('data-freight-line')).get();
    if ($wrapper.find('[data-freight-check]').length) draft.check_ids = $wrapper.find('[data-freight-check]:checked').map((_, element) => element.getAttribute('data-freight-check')).get();
  }

  bindFreightInputs(state) {
    const $wrapper = state.dialog.$wrapper;
    $wrapper.off('input.ocwFreight change.ocwFreight').on('input.ocwFreight change.ocwFreight', '[data-freight-field],[data-freight-line],[data-freight-check],[data-payment-row-field],[data-payment-attachment],[data-payment-attachment-keys],[data-payment-replace-claim]', event => {
      if (state.busy || state.freightWriting) return;
      this.captureFreightDraft(state);
      const key = event.currentTarget.getAttribute('data-freight-field');
      const paymentRowKey = event.currentTarget.getAttribute('data-payment-row-field');
      if (key === 'candidate_id') { state.freightDraft.line_ids = []; state.freightDraft.negative_confirmed = false; this.renderFreightWorkspace(state); }
      else if (paymentRowKey === 'logical_fee_key') {
        const validKeys = [...new Set((state.freightDraft.payment_rows || []).map(row => row.logical_fee_key).filter(Boolean))];
        state.freightDraft.attachments = (state.freightDraft.attachments || []).map(row => {
          const keys = (row.logical_fee_keys || []).filter(value => validKeys.includes(value));
          return { ...row, logical_fee_keys: row.selected && !keys.length ? validKeys : keys };
        });
        this.renderFreightWorkspace(state);
      }
      else {
        if (!this.freightNeedsNegative(state)) { state.freightDraft.negative_confirmed = false; $wrapper.find('[data-freight-field="negative_confirmed"]').prop('checked', false); }
        $wrapper.find('[data-freight-negative]').prop('hidden', !this.freightNeedsNegative(state));
        $wrapper.find('[data-freight-summary]').html(this.renderFreightChangeSummary(state));
      }
    });
  }

  async readFreightWorkspace(state, action, args, receive) {
    const request = state.freightRequest = (state.freightRequest || 0) + 1;
    const version = state.versionName;
    state.freightLoading = true; state.freightError = ''; this.renderFreightWorkspace(state);
    const current = () => this.isBatchSettlementCurrent(state) && request === state.freightRequest && version === state.versionName;
    try {
      const result = await this.settlementApi(action, { batch_name: state.batchName, version_name: version, ...args });
      if (!current()) return;
      if (!result?.ok) throw new Error(result?.message || '读取来源失败，请重试');
      receive(result);
    } catch (error) { if (current()) state.freightError = error.message || '读取失败，请重试'; }
    finally { if (current()) { state.freightLoading = false; this.renderFreightWorkspace(state);
      if (action === "preview_shipment_packing_source" && state.packingPreview) state.dialog.$wrapper?.find?.("[data-freight-packing-preview]")?.[0]?.scrollIntoView?.({block:"start",behavior:"smooth"});
    } }
  }

  async writeFreightWorkspace(state, action, args, afterWrite) {
    if (state.freightWriting || state.busy || !state.open) return;
    if (!this.isBatchSettlementCurrent(state)) throw new Error(ocTranslate("当前批次或版本已变化，请重新打开本票资料。"));
    state.freightWriting = true; state.freightError = ''; this.renderFreightWorkspace(state);
    const version = state.versionName;
    try {
      const result = await this.settlementWrite(state, () => this.settlementApi(action, args));
      if (!result?.ok) throw new Error(result?.message || '操作未保存，请核对后重试');
      if (!this.isBatchSettlementCurrent(state) || version !== state.versionName) return;
      if (this.detailState?.batchName === state.batchName) this.acceptBatchWriteRevision?.(result.batch_modified);
      state.freightView = null; state.freightDraft = {}; state.packingSources = null; state.packingPreview = null; state.packingSelected = null;
      state.freightMessage = result.message || (result.status === 'queued' ? '已保存确认，当前编辑结束后处理。' : '已保存，请核对当前资料并重新试算。');
      try { await this.refreshSettlementBatch(state.batchName, result.version); }
      catch (error) { state.freightError = ocTranslate("已保存，新版本资料读取失败：{0}", [error.message || "请刷新重试"]); }
      if (state.open) await afterWrite(result);
    } finally { state.freightWriting = false; if (state.open) this.renderFreightWorkspace(state); }
  }

  async handleFreightAction(state, action, $button, afterWrite = async () => {}) {
    if (!state.open || state.busy || state.freightWriting) return;
    const data = state.data;
    this.captureFreightDraft(state);
    if (action === 'freight-tab' || action === 'packing-review') {
      state.freightRequest = (state.freightRequest || 0) + 1; state.freightLoading = false;
      state.freightTab = action === 'packing-review' ? 'packing' : $button.attr('data-freight-tab');
      state.freightView = null; state.freightReturnView = null; state.freightError = '';
      this.renderFreightWorkspace(state);
      if (state.freightTab === 'packing' && !state.packingSources) return this.handleFreightAction(state, 'packing-sources-refresh', $button, afterWrite);
      return;
    }
    if (action === 'freight-back') {
      state.freightRequest = (state.freightRequest || 0) + 1; state.freightLoading = false;
      if (state.freightView?.kind === 'payment-preview') {
        state.paymentPreview = null;
        state.freightView = { kind: 'payment-adopt', candidate_id: state.freightDraft?.candidate_id || '' };
      } else state.freightView = state.freightView?.kind === 'evidence' ? state.freightReturnView || null : null;
      state.freightReturnView = null; state.freightError = ''; return this.renderFreightWorkspace(state);
    }
    if (action === 'freight-source') return this.openSettlementSource((data.candidates || []).find(row => row.id === $button.attr('data-id'))?.expense);
    if (action === 'freight-evidence-source') return this.openSettlementSource(state.freightEvidenceRow?.source);
    if (action === 'freight-evidence') {
      state.freightReturnView = state.freightView || null; state.freightView = { kind: 'evidence' }; state.freightEvidenceRow = null;
      return this.readFreightWorkspace(state, 'freight_line_evidence', { line_id: $button.attr('data-line') }, result => { state.freightEvidenceRow = result.evidence; });
    }
    if (action === 'packing-sources-refresh') {
      state.packingPreview = null; state.packingSelected = null;
      return this.readFreightWorkspace(state, 'list_shipment_packing_sources', {}, result => { state.packingSources = result.sources || []; state.packingVersion = state.versionName; });
    }
    if (action === 'packing-source-preview') {
      const source = (state.packingSources || []).find(row => row.id === $button.attr('data-id'));
      if (!source) throw new Error(ocTranslate("来源已变化，请刷新来源列表"));
      state.packingSelected = source.id; state.packingPreview = null;
      return this.readFreightWorkspace(state, 'preview_shipment_packing_source', { source_id: source.id, source_revision: source.revision }, result => { if (!result.preview) throw new Error(ocTranslate("未取得有效装箱预览")); state.packingPreview = result.preview; });
    }
    if (data.historical) throw new Error(ocTranslate("历史版本仅供追溯，请返回当前调整草稿处理。"));
    if (!this.isBatchSettlementCurrent(state)) throw new Error(ocTranslate("当前批次或版本已变化，请重新打开本票资料。"));
    if (action === 'payment-rule-match') {
      await this.ensurePaymentWriteSession(state);
      state.freightLoading = true; state.freightError = ''; this.renderFreightWorkspace(state);
      try {
        const result = await this.settlementApi('run_payment_rule_matching', { batch_name: state.batchName, version_name: state.versionName });
        if (!result?.ok) throw new Error(result?.message || '规则匹配未完成');
        if (this.isBatchSettlementCurrent(state)) state.data = { ...state.data, matching: result.matching || state.data.matching,
          payment_candidates: result.payment_candidates || result.candidates || state.data.payment_candidates,
          candidates: result.payment_candidates || result.candidates || state.data.candidates };
      } catch (error) { state.freightError = error.message || '规则匹配失败'; throw error; }
      finally { state.freightLoading = false; if (state.open) this.renderFreightWorkspace(state); }
      return;
    }
    if (action === 'payment-ai-open') {
      state.paymentAiOpen = true; state.paymentAiExpandPending = false;
      state.freightDraft = { ...(state.freightDraft || {}), ai_limit: Math.min(50, Number(data.payment_matching?.limit) || 30) };
      return this.renderFreightWorkspace(state);
    }
    if (action === 'payment-ai-more') { state.paymentAiOpen = true; state.paymentAiExpandPending = true; return this.renderFreightWorkspace(state); }
    if (action === 'payment-ai-run' || action === 'payment-ai-more-confirm') {
      const previous = data.payment_matching || {};
      const hints = Object.fromEntries(['waybill','supplier','project','date','description'].map(key => [key, String(state.freightDraft?.[key] || '').trim()]));
      const limit = Math.max(1, Math.min(50, Number(state.freightDraft?.ai_limit) || Number(previous.limit) || 30));
      const offset = action === 'payment-ai-more-confirm' ? (Number(previous.offset) || 0) + (Number(previous.limit) || limit) : 0;
      await this.ensurePaymentWriteSession(state);
      state.freightLoading = true; state.freightError = ''; this.renderFreightWorkspace(state);
      try {
        const result = await this.settlementApi('start_payment_ai_matching', { batch_name: state.batchName, version_name: state.versionName, hints: JSON.stringify(hints), offset, limit });
        if (!result?.ok) throw new Error(result?.message || 'AI 匹配未启动');
        if (this.isBatchSettlementCurrent(state)) {
          state.data = { ...state.data, payment_matching: result.payment_matching || {} };
          state.paymentAiExpandPending = false;
          if (['queued', 'running'].includes(state.data.payment_matching.status)) {
            clearTimeout(state.timer);
            state.timer = setTimeout(() => this.loadBatchSettlementDialog(state), 3000);
          }
        }
      } catch (error) { state.freightError = error.message || 'AI 匹配失败'; throw error; }
      finally { state.freightLoading = false; if (state.open) this.renderFreightWorkspace(state); }
      return;
    }
    if (action === 'payment-split-add') {
      const rows = state.freightDraft?.payment_rows || [];
      const used = new Set(rows.map(row => row.logical_fee_key)); const option = this.paymentFeeOptions(data).find(([key]) => !used.has(key)) || this.paymentFeeOptions(data)[0];
      rows.push({ source_line_id: 'approval_total', logical_fee_key: option[0], amount: '', currency: rows[0]?.currency || 'RMB', amount_status: 'ACTUAL', replace_claim_ids: [] });
      return this.renderFreightWorkspace(state);
    }
    if (action === 'payment-split-remove') {
      const index = Number($button.attr('data-row-index')); if ((state.freightDraft?.payment_rows || []).length <= 1) throw new Error(ocTranslate("请至少保留一条费用"));
      state.freightDraft.payment_rows.splice(index, 1); return this.renderFreightWorkspace(state);
    }
    if (action === 'payment-review') {
      const candidate = (data.payment_candidates || data.candidates || []).find(row => row.id === $button.attr('data-id'));
      if (!candidate) throw new Error(ocTranslate("付款候选已变化，请刷新"));
      state.freightView = { kind: 'payment-adopt', candidate_id: candidate.id }; state.paymentPreview = null;
      state.freightDraft = { candidate_id: candidate.id, payment_rows: this.paymentInitialRows(candidate, data),
        attachments: (candidate.attachments || []).map(row => ({ ...row, selected: row.selected ?? row.default_selected })), reason: '', negative_confirmed: false };
      return this.renderFreightWorkspace(state);
    }
    if (action === 'payment-preview') {
      const draft = state.freightDraft || {}; const rows = this.validatePaymentDraft(state);
      const candidateId = draft.candidate_id || state.freightView?.candidate_id;
      const candidate = (data.payment_candidates || data.candidates || []).find(row => row.id === candidateId);
      if (!candidate) throw new Error(ocTranslate("付款候选已变化，请刷新"));
      const currentKeys = [...new Set(rows.map(item => item.logical_fee_key).filter(Boolean))];
      const attachmentSelections = (draft.attachments || []).map(row => {
        const mappedKeys = (row.logical_fee_keys || []).filter(key => currentKeys.includes(key));
        return { document_id: row.document_id, selected: !!row.selected,
          logical_fee_keys: row.selected ? (mappedKeys.length ? mappedKeys : currentKeys) : [] };
      });
      const args = { batch_name: state.batchName, version_name: state.versionName, candidate_id: candidate.id,
        candidate_revision: candidate.revision, selections: JSON.stringify(rows), reason: String(draft.reason || '').trim(),
        negative_confirmed: draft.negative_confirmed === true, attachment_selections: JSON.stringify(attachmentSelections) };
      await this.ensurePaymentWriteSession(state);
      const result = await this.settlementWrite(state, () => this.settlementApi('preview_payment_adoption', args));
      if (!result?.ok || !result.preview) throw new Error(result?.message || '未生成有效费用预览');
      state.paymentPreview = result.preview; state.freightView = { kind: 'payment-preview' }; return this.renderFreightWorkspace(state);
    }
    if (action === 'payment-confirm') {
      const preview = state.paymentPreview;
      if (!preview?.preview_id || !preview?.revision) throw new Error(ocTranslate("付款预览已失效，请重新预览"));
      await this.ensurePaymentWriteSession(state);
      return this.writeFreightWorkspace(state, 'confirm_payment_adoption', { batch_name: state.batchName, preview_id: preview.preview_id,
        revision: preview.revision, edit_token: this.detailState?.editToken || null,
        expected_modified: this.detailState?.expectedModified || null }, afterWrite);
    }
    if (action === 'payment-amend') {
      const claim = (data.payment_claims || []).find(row => row.id === $button.attr('data-claim'));
      const amendAction = $button.attr('data-payment-action');
      if (!claim || !(claim.available_actions || []).includes(amendAction)) throw new Error(ocTranslate("该费用不允许当前更正，请刷新"));
      state.freightView = { kind: 'payment-amend', claim_id: claim.id, expected_revision: claim.revision, action: amendAction };
      state.freightDraft = { amount: claim.amount, currency: claim.currency, amount_status: claim.amount_status || 'ACTUAL', logical_fee_key: claim.logical_fee_key, reason: '' };
      return this.renderFreightWorkspace(state);
    }
    if (action === 'payment-amend-save') {
      const view = state.freightView || {}; const draft = state.freightDraft || {}; const reason = String(draft.reason || '').trim();
      if (!reason) throw new Error(ocTranslate("请填写更正原因"));
      if (view.action === 'amount' && Number(draft.amount) < 0 && draft.negative_confirmed !== true) throw new Error(ocTranslate("请先确认负数冲抵／折扣"));
      const editKeys = { amount:['amount'], currency:['currency','amount_status'], status:['amount_status'], reclassify:['logical_fee_key'], revoke:[] };
      const edits = Object.fromEntries((editKeys[view.action] || []).map(key => [key, draft[key]]));
      if (view.action === 'amount' && Number(draft.amount) < 0) edits.negative_confirmed = draft.negative_confirmed === true;
      await this.ensurePaymentWriteSession(state);
      return this.writeFreightWorkspace(state, 'amend_payment_claim', { batch_name: state.batchName, version_name: state.versionName,
        claim_id: view.claim_id, expected_revision: view.expected_revision, action: view.action, reason, edits: JSON.stringify(edits),
        edit_token: this.detailState?.editToken || null, expected_modified: this.detailState?.expectedModified || null }, afterWrite);
    }
    if (action === 'payment-reject' || action === 'payment-reopen') {
      const source = action === 'payment-reopen' ? data.payment_rejected_candidates || [] : data.payment_candidates || data.candidates || [];
      const candidate = source.find(row => row.id === $button.attr('data-id'));
      if (!candidate) throw new Error(ocTranslate("付款候选已变化，请刷新"));
      state.freightView = { kind: 'payment-decision', action: action === 'payment-reopen' ? 'reopen' : 'reject', candidate_id: candidate.id,
        revision: candidate.revision, label: candidate.expense?.approval_no || candidate.expense?.title || '' };
      state.freightDraft = { reason: '' }; return this.renderFreightWorkspace(state);
    }
    if (action === 'payment-decision-save') {
      const view = state.freightView || {}; const reason = String(state.freightDraft?.reason || '').trim();
      if (!reason) throw new Error(ocTranslate("请填写核对依据"));
      const method = view.action === 'reopen' ? 'reopen_payment_candidate' : 'reject_payment_candidate';
      await this.ensurePaymentWriteSession(state);
      return this.writeFreightWorkspace(state, method, { batch_name: state.batchName, version_name: state.versionName,
        candidate_id: view.candidate_id, revision: view.revision, reason,
        edit_token: this.detailState?.editToken || null, expected_modified: this.detailState?.expectedModified || null }, afterWrite);
    }
    if (action === 'retry-matching') return this.startBatchSettlementMatching(state, true);
    if (action === 'search') { state.freightTab = 'freight'; state.freightView = { kind: 'search' }; state.freightDraft = { query: '', reason: '' }; state.freightSearch = {}; return this.renderFreightWorkspace(state); }
    if (['freight-search-run', 'freight-search-next', 'freight-search-previous'].includes(action)) {
      const search = state.freightSearch ||= {};
      if (action === 'freight-search-run') { search.pages = []; search.after = null; search.query = String(state.freightDraft.query || '').trim(); }
      if (!search.query) throw new Error(ocTranslate("请输入审批号、单据号或原单关键词"));
      if (action === 'freight-search-next') { (search.pages ||= []).push(search.after); search.after = search.next_cursor; }
      if (action === 'freight-search-previous') search.after = search.pages.pop() ?? null;
      return this.readFreightWorkspace(state, 'find_expenses', { query: search.query, after: search.after }, result => { Object.assign(search, result, { loaded: true }); });
    }
    if (action === 'freight-search-choose') {
      const reason = String(state.freightDraft.reason || '').trim();
      if (!reason) throw new Error(ocTranslate("请填写人工关联依据，再选择候选"));
      const result = await this.settlementWrite(state, () => this.settlementApi('prepare_manual_candidate', { batch_name: state.batchName, expense_id: $button.attr('data-id'), reason }));
      if (!this.isBatchSettlementCurrent(state)) return;
      if (!result?.ok || !result.candidate) throw new Error(result?.message || '候选准备失败');
      data.candidates = [...(data.candidates || []).filter(row => row.id !== result.candidate.id), result.candidate];
      state.freightView = { kind: 'adopt' }; state.freightDraft = { candidate_id: result.candidate.id, line_ids: [], negative_confirmed: false };
      return this.renderFreightWorkspace(state);
    }
    if (action === 'packing-source-confirm') {
      if (!this.canAdoptFreightPacking(state)) throw new Error(ocTranslate("此预览不可采用，请重新核对来源"));
      return this.writeFreightWorkspace(state, 'confirm_shipment_packing_source', { batch_name: state.batchName, version_name: state.versionName, preview_id: state.packingPreview.id, revision: state.packingPreview.revision, replace_all: 1 }, afterWrite);
    }
    if (['freight-amount', 'freight-replace', 'freight-revoke'].includes(action)) {
      const claim = (data.freight?.claims || []).find(row => row.id === $button.attr('data-claim'));
      if (!claim) throw new Error(ocTranslate("费用记录已变化，请刷新后重试"));
      state.freightTab = 'freight'; state.freightView = { kind: action.replace('freight-', ''), claim_id: claim.id, expected_revision: data.freight.revision };
      state.freightDraft = { amount: String(claim.applied_amount ?? claim.amount), reason: '', line_ids: [], candidate_id: '', negative_confirmed: false };
      state.freightMessage = ''; return this.renderFreightWorkspace(state);
    }
    if (['freight-review', 'freight-reject', 'packing-checks'].includes(action)) {
      const candidate = (data.candidates || []).find(row => row.id === $button.attr('data-id'));
      if (action !== 'packing-checks' && !candidate) throw new Error(ocTranslate("来源候选已变化，请刷新"));
      state.freightTab = 'freight'; state.freightView = { kind: action === 'packing-checks' ? 'checks' : action === 'freight-review' ? 'adopt' : 'reject' };
      state.freightDraft = { candidate_id: candidate?.id, line_ids: [], check_ids: [], reason: '', negative_confirmed: false }; state.freightMessage = '';
      return this.renderFreightWorkspace(state);
    }
    if (action !== 'freight-save') return;
    const view = state.freightView || {};
    const draft = state.freightDraft || {};
    const reason = String(draft.reason || '').trim();
    const args = { batch_name: state.batchName, version_name: state.versionName };
    if (view.kind !== 'adopt' && !reason) throw new Error(ocTranslate("请填写核对依据或更正原因"));
    if (view.kind === 'checks') {
      const selections = (data.packing_checks || []).filter(row => (draft.check_ids || []).includes(row.name)).map(row => ({ item_name: row.name, expected_item_hash: row.revision }));
      if (!selections.length) throw new Error(ocTranslate("请选择核对项并填写依据"));
      return this.writeFreightWorkspace(state, 'resolve_freight_packing_checks', { ...args, selections: JSON.stringify(selections), reason }, afterWrite);
    }
    const { candidate, lines } = this.freightSelectedLines(state);
    if (view.kind === 'reject') return this.writeFreightWorkspace(state, 'reject_freight_candidate', { ...args, candidate_id: candidate.id, revision: candidate.revision, reason }, afterWrite);
    if (['replace','adopt'].includes(view.kind)) {
      if (!candidate || !lines.length) throw new Error(ocTranslate("请选择本票费用明细"));
      if (candidate.expense?.approved === false || lines.some(row => !row.available || row.adopted)) throw new Error(ocTranslate("所选明细不可采用，请刷新后复核"));
      Object.assign(args, { candidate_id: candidate.id, candidate_revision: candidate.revision, line_ids: JSON.stringify(lines.map(row => row.id)) });
    }
    if (view.kind === 'amount') {
      if (String(draft.amount ?? '').trim() === '' || !Number.isFinite(Number(draft.amount))) throw new Error(ocTranslate("请填写有效金额，可填 0"));
      args.amount = String(draft.amount).trim();
    }
    if (this.freightNeedsNegative(state) && draft.negative_confirmed !== true) throw new Error(ocTranslate("请先确认已核对负数冲抵／折扣"));
    if (['amount','replace','adopt'].includes(view.kind)) args.negative_confirmed = this.freightNeedsNegative(state) && draft.negative_confirmed === true;
    if (view.kind === 'adopt') return this.writeFreightWorkspace(state, 'confirm_freight_lines', args, afterWrite);
    if (!['amount','replace','revoke'].includes(view.kind)) return;
    return this.writeFreightWorkspace(state, 'amend_freight_claim', { ...args, claim_id: view.claim_id, expected_revision: view.expected_revision, action: view.kind, reason }, afterWrite);
  }

  openFreightReview(batchName, versionName, candidate, freight = {}, onComplete = async () => {}) {
    const state=this.settlementDialog("核对本票运费明细",[
      {fieldtype:"Check",fieldname:"negative_confirmed",label:ocTranslate("已核对负数冲抵／折扣")},
      {fieldtype:"Small Text",fieldname:"reason",label:ocTranslate("更正原因（替换已有费用时必填）")}]);
    const rows=candidate.lines || [];
    this.settlementBody(state, `<p>${ocTranslate("支付审批 {0}；只采用勾选的本票明细，装箱资料保持独立。", [this.escape(candidate.expense?.approval_no || "")])}</p>
      ${rows.map(r => `<label class="ocw-settlement-search-row"><input type="checkbox" data-freight-line="${this.escape(r.id)}" ${r.available ? "" : "disabled"} ${r.available && !r.adopted ? "checked" : ""}>
        <span>${this.escape(r.amount)} ${this.escape(r.currency)} · ${this.escape(r.waybill || r.label)}<small>${this.escape(this.freightEvidence(r.evidence))} ${r.adopted ? ocTranslate(" · 已采用") : ""}</small></span></label>`).join("")}
      ${(freight.claims || []).length ? `<details><summary>${ocTranslate("更正已有费用（仅勾选要替换的旧明细）")}</summary>${freight.claims.map(r => `<label class="ocw-settlement-search-row"><input type="checkbox" data-freight-replace="${this.escape(r.id)}"><span>${this.escape(r.amount)} ${this.escape(r.currency)} · ${this.escape(r.approval_no)}</span></label>`).join("")}</details>` : ""}`);
    this.settlementActions(state,`<button class="ocw-primary-btn" data-settlement-action="confirm">${ocTranslate("确认采用所选费用")}</button>`);
    this.settlementEvents(state,async action=>{
      if(action!=="confirm")return;
      const selected=state.dialog.$wrapper.find('[data-freight-line]:checked').map((_,el)=>el.getAttribute('data-freight-line')).get();
      const replaced=state.dialog.$wrapper.find('[data-freight-replace]:checked').map((_,el)=>el.getAttribute('data-freight-replace')).get();
      if(!selected.length)throw new Error(ocTranslate("请选择本票费用明细"));
      const result=await this.settlementWrite(state,()=>this.settlementApi('confirm_freight_lines',{batch_name:batchName,version_name:versionName,
        candidate_id:candidate.id,candidate_revision:candidate.revision,line_ids:JSON.stringify(selected),replace_claim_ids:JSON.stringify(replaced),expected_revision:freight.revision,
        reason:String(state.dialog.get_value('reason')||''),negative_confirmed:!!state.dialog.get_value('negative_confirmed')}));
      this.settlementActions(state,'');this.settlementBody(state,`<p>${this.escape(result.message || (result.status==='queued'?ocTranslate("编辑结束后处理已确认的费用采用"):ocTranslate("费用采用已保存，请重新试算；装箱资料未随费用切换。")))}</p>`);
      await this.refreshSettlementBatch(batchName);await onComplete();
    });
  }

  async openFreightPackingReview(batchName, versionName, candidate, onComplete=async()=>{}) {
    const state=this.settlementDialog('本票装箱变更对比',[{fieldtype:'Check',fieldname:'complete',label:ocTranslate("已核实来源为本票完整清单，并采用完整清单替换")}]);
    const detailContext=this.settlementDetailContext();
    const result=await this.settlementApi('preview_freight_packing',{batch_name:batchName,version_name:versionName,candidate_id:candidate.id,candidate_revision:candidate.revision});
    if(!state.open || detailContext!==this.settlementDetailContext())return;
    const preview=result.preview;if(!preview)throw new Error(ocTranslate("未取得有效装箱预览"));
    state.dialog.set_df_property('complete','hidden',!preview.complete);
    this.settlementBody(state,`<p>${this.escape(preview.message)}</p><p>${ocTranslate("付款单的计费重量不直接覆盖毛重。物料编码不同时请核对映射，商品采购价不会跨物料沿用。")}</p>
      <details open><summary>${ocTranslate("当前采用物料")}</summary>${(preview.current_items||[]).map(r=>`<p>${ocTranslate("{0} {1} · {2} {3} · 毛重 {4} kg", [this.escape(r.material_code), this.escape(r.product_name), this.escape(r.quantity), this.escape(r.unit), this.escape(r.gross_weight_kg??ocTranslate("待补"))])}</p>`).join('')}</details>
      <details><summary>${ocTranslate("国际物流原始明细")}</summary>${(preview.original_goods||[]).map(r=>`<p>${this.escape(r.material_code)} ${this.escape(r.product_name)} ${this.escape(r.quantity)} ${this.escape(r.unit)}</p>`).join('')||ocTranslate("原始明细待核对")}</details>
      ${(preview.goods||[]).map((r,index)=>{const matches=(preview.current_items||[]).filter(i=>i.material_code===r.material_code && i.unit===r.unit);return `<div class="ocw-settlement-source"><label><input type="checkbox" data-packing-line="${index}" checked> ${this.escape(r.material_code)} ${this.escape(r.product_name)} · ${this.escape(r.quantity)} ${this.escape(r.unit)}</label>
        <small>${this.escape(this.freightEvidence(r.evidence))}</small><select data-packing-target="${index}"><option value="">${ocTranslate("新增物料行")}</option>${(preview.current_items||[]).map(i=>`<option value="${this.escape(i.name)}" ${matches.length===1 && matches[0].name===i.name?'selected':''}>${ocTranslate("更新：{0} {1}（{2} {3}）", [this.escape(i.material_code), this.escape(i.product_name), this.escape(i.quantity), this.escape(i.unit)])}</option>`).join('')}</select></div>`;}).join('')}
      ${(preview.texts||[]).map(t=>`<details><summary>${this.escape(this.freightEvidence(t.evidence))}</summary><pre style="white-space:pre-wrap">${this.escape(t.text)}</pre></details>`).join('')}`);
    if(!preview.approved || preview.invalid || !(preview.goods||[]).length)return;
    this.settlementActions(state,`<button class="ocw-primary-btn" data-settlement-action="confirm">${ocTranslate("仅采用所选装箱变更")}</button>`);
    this.settlementEvents(state,async action=>{
      if(action!=='confirm')return;
      if(detailContext!==this.settlementDetailContext())throw new Error(ocTranslate("当前版本已变化，请重新预览"));
      const selections=state.dialog.$wrapper.find('[data-packing-line]:checked').map((_,el)=>{const index=Number(el.getAttribute('data-packing-line'));return {line_key:preview.goods[index].line_key,item_name:state.dialog.$wrapper.find(`[data-packing-target="${index}"]`).val()||null};}).get();
      const saved=await this.settlementWrite(state,()=>this.settlementApi('confirm_freight_packing',{batch_name:batchName,version_name:versionName,preview_id:preview.id,revision:preview.revision,selections:JSON.stringify(selections),complete_confirmed:!!state.dialog.get_value('complete')}));
      this.settlementActions(state,'');this.settlementBody(state,`<p>${this.escape(saved.message || (saved.status==='queued'?ocTranslate("装箱采用已排队"):ocTranslate("装箱变更已保存，费用未改变，请核对重量及商品价格后重新试算。")))}</p>`);
      await this.refreshSettlementBatch(batchName);await onComplete();
    });
  }

  renderFreightHistory(state,data) {
    const job=data.job || {};
    this.settlementBody(state, `<p>${ocTranslate("覆盖采购、运营、BU、月结、报销等支付来源，按运单费用明细查找。拒绝和撤销的单据不进入可采用候选；审批通过表示结算审批通过。")}</p>
      ${this.renderSettlementHealth(data)}
      <p>${ocTranslate("本轮状态：{0} · 已处理 {1} / {2} · 失败 {3}", [this.escape(job.status || ocTranslate("尚未启动")), this.escape(job.processed_count || 0), this.escape(job.item_count || 0), this.escape(job.failed_count || 0)])}</p>
      <div class="ocw-settlement-toolbar">${["running","queued"].includes(job.status)?`<button class="ocw-outline-btn" data-settlement-action="pause">${ocTranslate("暂停")}</button>`: `<button class="ocw-primary-btn" data-settlement-action="start">${ocTranslate("更新历史来源与本票候选")}</button>`}
      ${["paused","partial","failed"].includes(job.status)?`<button class="ocw-outline-btn" data-settlement-action="retry">${ocTranslate("继续／重试")}</button>`:''}
      <button class="ocw-outline-btn" data-settlement-action="refresh">${ocTranslate("刷新")}</button><button class="ocw-outline-btn" data-settlement-action="ai-match">${ocTranslate("DeepSeek 分析未解决／冲突票")}</button></div>
      <p>${ocTranslate("已找到候选物流 {0} 票 · 已采用费用 {1} 票 · 未匹配 {2} 票。月结单可覆盖多票；费用和装箱均在对应批次内分别核对。", [this.escape(data.shipments?.length || 0), this.escape(data.counts?.confirmed || 0), this.escape(data.counts?.unmatched || 0)])}</p>
      <details><summary>${ocTranslate("上游2026历史覆盖（最后同步保存的清单）")}</summary>${(data.health?.health?.financial_coverage||[]).map(r=>`<p>${ocTranslate("{0}：已完成 {1}/{2} 个时间段 · 已归档 {3} 条 · 失败 {4} {5}", [this.escape(r.template_name), r.completed_windows, r.windows, r.processed_count??0, r.failed_windows, r.last_error?this.escape(r.last_error):''])}</p>`).join('')||ocTranslate("尚未保存覆盖清单，不能据此判断历史单据为零。")}</details>
      <div class="ocw-settlement-table-wrap"><table class="ocw-settlement-table"><thead><tr><th>${ocTranslate("支付流程")}</th><th>${ocTranslate("本地来源")}</th><th>${ocTranslate("有效来源")}</th><th>${ocTranslate("匹配物流票数")}</th><th>${ocTranslate("待归档附件")}</th></tr></thead><tbody>${(data.coverage||[]).map(r=>`<tr><td>${this.escape(r.template_name)}</td><td>${r.local_sources}</td><td>${r.qualified}</td><td>${r.matched_shipments}</td><td>${r.missing_attachments}</td></tr>`).join('')}</tbody></table></div>
      <div class="ocw-settlement-table-wrap"><table class="ocw-settlement-table"><thead><tr><th>${ocTranslate("国际物流")}</th><th>${ocTranslate("候选支付单数")}</th><th>${ocTranslate("核对入口")}</th></tr></thead><tbody>${(data.shipments||[]).map(r=>`<tr><td>${this.escape(r.logistics.approval_no || r.logistics.instance)}</td><td>${r.candidate_count}</td><td>${r.batch?`<a href="/app/overseas-cost-workbench?batch=${encodeURIComponent(r.batch)}&screen=detail&tab=documents">${ocTranslate("打开本票资料与费用")}</a>`:ocTranslate("批次待建立／冲突待处理")}</td></tr>`).join('')}</tbody></table></div>
      ${(data.failures||[]).map(r=>`<p>${this.escape(r.source_id)}：${this.escape(r.error)}</p>`).join('')}`);
  }


  async openFreightEvidence(batchName,versionName,lineId) {
    const state=this.settlementDialog('本票账单行证据');
    const result=await this.settlementApi('freight_line_evidence',{batch_name:batchName,version_name:versionName,line_id:lineId});
    if(!state.open)return;
    const row=result.evidence;
    this.settlementBody(state,`<p>${ocTranslate("支付审批：{0}", [this.escape(row.source?.approval_no || '')])}</p><p>${this.escape(this.freightEvidence(row.evidence))}</p>
      <p>${ocTranslate("本票运单：{0}", [this.escape(row.waybill || row.approval_no || ocTranslate("原单明确关联"))])}</p><p><b>${this.escape(row.amount)} ${this.escape(row.currency)}</b> · ${this.escape(ocTranslate(row.label))}</p>
      <p>${ocTranslate("账单计费重量：{0}（不直接覆盖装箱毛重）", [this.escape(row.billing_weight ?? ocTranslate("未提供"))])}</p><pre style="white-space:pre-wrap">${this.escape(row.cargo_text || ocTranslate("本行未提供物料明细"))}</pre>
      <small>${ocTranslate("本地归档证据；只展示本票行，来源快照 {0}", [this.escape(row.source_snapshot)])}</small>`);
    this.settlementActions(state,`<button class="ocw-outline-btn" data-settlement-action="source">${ocTranslate("打开支付原单")}</button>`);
    this.settlementEvents(state,async action=>{if(action==='source')this.openSettlementSource(row.source);});
  }

  openFreightPackingChecks(batchName,versionName,items,onComplete) {
    const state=this.settlementDialog('核对保留的装箱重量与体积',[{fieldtype:'Small Text',fieldname:'reason',label:ocTranslate("核对依据"),reqd:1}]);
    this.settlementBody(state,`<p>${ocTranslate("物料或数量已变化。请核实以下保留值仍适用于本票；需要修改时先在物料表补充，再重新打开此窗口。")}</p>${items.map(row=>`<label class="ocw-settlement-search-row"><input type="checkbox" data-packing-check="${this.escape(row.name)}"><span>${this.escape(row.material_code)} ${this.escape(row.product_name)} · ${this.escape(row.quantity)} ${this.escape(row.unit)}<small>${ocTranslate("毛重 {0} kg · 体积 {1} m³", [this.escape(row.gross_weight_kg ?? ocTranslate("待补")), this.escape(row.volume_m3 ?? ocTranslate("待补"))])}</small></span></label>`).join('')}`);
    this.settlementActions(state,`<button class="ocw-primary-btn" data-settlement-action="confirm">${ocTranslate("确认所选装箱资料适用")}</button>`);
    this.settlementEvents(state,async action=>{
      if(action!=='confirm')return;
      const reason=String(state.dialog.get_value('reason') || '').trim();
      const selections=state.dialog.$wrapper.find('[data-packing-check]:checked').map((_,el)=>{const row=items.find(i=>i.name===el.getAttribute('data-packing-check'));return {item_name:row.name,expected_item_hash:row.revision};}).get();
      if(!reason || !selections.length)throw new Error(ocTranslate("请选择核对项并填写依据"));
      await this.settlementWrite(state,()=>this.settlementApi('resolve_freight_packing_checks',{batch_name:batchName,version_name:versionName,selections:JSON.stringify(selections),reason}));
      state.dialog.hide();await this.refreshSettlementBatch(batchName);await onComplete();
    });
  }
