from overseas_costing.tests.test_workbench_frontend_state import _fee_workspace_result


def test_approval_column_stays_last_with_auxiliary_columns():
    result = _fee_workspace_result('''
    const w = Object.create(Harness.prototype);
    w.detailState = {batchName: 'B'};
    const normal = w.materialFeeGridColumns().map(column => column.field);
    w.ensureMaterialFeeState().showAuxiliary = true;
    const auxiliary = w.materialFeeGridColumns().map(column => column.field);
    console.log(JSON.stringify({normal, auxiliary}));
    ''')
    for fields in result.values():
        assert fields[:4] == ['__group_select', 'row_no', 'material_code', 'product_name']
        assert fields[-1] == 'source_doc_no'
        assert '__actions' not in fields
        assert fields.count('source_doc_no') == 1
        assert 'gross_weight_kg' in fields
        assert 'chargeable_weight_kg' not in fields
    assert result['auxiliary'][-2] == 'source_file_name'


def test_normal_and_ai_rows_identify_cells_by_field_after_reordering():
    result = _fee_workspace_result('''
    const w = Object.create(Harness.prototype);
    w.detailState = {batchName: 'B'};
    w.escape = value => String(value ?? '').replace(/"/g, '&quot;');
    w.formatValue = value => String(value ?? '');
    const columns = w.materialFeeGridColumns().slice(0, 4);
    const item = {name:'I', stable_line_key:'L1', row_no:1, material_code:'FL000429',
      product_name:'很长的物料名称，需要通过悬停查看完整内容'};
    const normal = w.renderMaterialFeeGridRow(item, columns, 0);
    const ai = w.renderMaterialReplacementGridRow({...item,
      __aiReplacement:{proposalId:'P',rowIndex:0}}, columns, 0);
    console.log(JSON.stringify({normal, ai}));
    ''')
    for html in result.values():
        for field in ['__group_select', 'row_no', 'material_code', 'product_name']:
            assert f'data-mf-grid-field="{field}"' in html
    assert 'title="很长的物料名称，需要通过悬停查看完整内容"' in result['normal']
    assert 'data-mf-ai-edit="1"' in result['ai']
