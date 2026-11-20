"""Expected AI errors stay inside the workbench, with privacy-safe diagnostics."""
from functools import wraps
import json
import secrets

from .material_ai_source_dependencies import SourceEligibilityError


def retry_on_deadlock(function):
    for attempt in range(3):
        try:
            return function()
        except Exception as error:
            if type(error).__name__ != 'QueryDeadlockError' or attempt == 2:
                raise
            import frappe
            # Re-enter checks and locks on a fresh transaction snapshot.
            frappe.db.rollback()


def source_review_endpoint(stage):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            try:
                result = retry_on_deadlock(lambda: function(*args, **kwargs))
                if isinstance(result, dict) and result.get('ok') is False and not result.get('error'):
                    code = result.get('code') or 'REVIEW_REQUIRED'
                    result = {**result, 'code': code, 'retryable': False, 'error': {
                        'code': code, 'reason': result.get('message') or '资料或任务状态已变化，请刷新。',
                        'stage': stage, 'scope': '当前批次', 'next_action': '刷新资料或预览后重试。',
                        'retryable': False, 'source_id': '', 'approval_no': '', 'source_label': ''}}
                return result
            except Exception as error:
                import frappe
                frappe.db.rollback()
                trace_id = secrets.token_hex(8)
                source = error if isinstance(error, SourceEligibilityError) else None
                code = getattr(source, 'code', 'REVIEW_REQUIRED')
                reason = str(error) if isinstance(error, ValueError) else '分析服务暂时不可用，请提供记录编号以便排查。'
                action = '刷新资料，核对状态或补齐归档后重新分析。' if source else '刷新资料或预览后重试。'
                name = type(error).__name__
                if isinstance(error, PermissionError) or name in {'PermissionError', 'AuthenticationError', 'SessionExpired'}:
                    code = 'PERMISSION_DENIED'
                    reason = '当前登录或资料访问权限不可用。'
                    action = '请重新登录；如仍无法访问，请核对该批次权限。'
                elif not isinstance(error, ValueError):
                    code = 'AI_INTERNAL_ERROR'
                    action = f'请稍后重试；若仍失败，请提供记录编号 {trace_id}。'
                detail = {'code': code, 'reason': reason[:1000], 'stage': stage,
                          'scope': '当前批次', 'next_action': action, 'retryable': False,
                          'source_id': getattr(source, 'source_id', ''),
                          'approval_no': getattr(source, 'approval_no', ''),
                          'source_label': getattr(source, 'source_label', ''), 'trace_id': trace_id}
                # No exception text/traceback, attachment text, URL, credentials or request body in logs.
                frappe.logger('overseas_costing').warning(json.dumps({
                    'event': 'material_ai_request_failed', 'trace_id': trace_id, 'stage': stage,
                    'batch': str(kwargs.get('batch_name') or (args[0] if args else ''))[:200],
                    'source_id': detail['source_id'], 'approval_no': detail['approval_no'],
                    'code': code, 'exception_type': name}, ensure_ascii=False))
                return {'ok': False, 'code': code, 'message': detail['reason'], 'retryable': False,
                        'error': detail, 'trace_id': trace_id}
        return wrapped
    return decorate
