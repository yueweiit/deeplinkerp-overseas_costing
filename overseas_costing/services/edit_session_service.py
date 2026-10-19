"""单批次写操作的短时编辑租约与乐观并发校验。"""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


from datetime import datetime, timedelta

try:
    import frappe
    from frappe.utils import get_datetime, now_datetime
except Exception:  # pragma: no cover - 纯函数测试时保持可导入
    frappe = None

    def get_datetime(value):
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value))

    def now_datetime():
        return datetime.now()


LEASE_MINUTES = 5


def lock_is_active(batch: dict, now: datetime | None = None) -> bool:
    expires_at = batch.get("edit_lock_expires_at")
    return bool(expires_at and get_datetime(expires_at) > (now or now_datetime()))


def assert_editable(
    batch: dict,
    user: str,
    token: str,
    expected_modified: str | None,
    now: datetime | None = None,
) -> None:
    if not lock_is_active(batch, now=now):
        raise PermissionError(_("编辑会话已过期，请重新进入编辑。"))
    if batch.get("edit_lock_owner") != user or batch.get("edit_lock_token") != token:
        raise PermissionError(_("当前批次正在被{0}编辑。").format(batch.get('edit_lock_owner') or '其他用户'))
    if expected_modified and get_datetime(batch.get("modified")) != get_datetime(expected_modified):
        raise RuntimeError("批次数据已被更新，请刷新后重新确认本次修改。")


def _lock_row(batch_name: str) -> dict:
    if frappe is None:
        raise RuntimeError("当前未连接 Frappe。")
    rows = frappe.db.sql(
        """
        select name, modified, edit_lock_owner, edit_lock_token,
               edit_lock_acquired_at, edit_lock_expires_at
          from `tabOverseas Cost Batch`
         where name = %s
         for update
        """,
        (batch_name,),
        as_dict=True,
    )
    if not rows:
        raise ValueError(_("未找到批次：{0}").format(batch_name))
    return rows[0]


def acquire_edit_session(batch_name: str) -> dict:
    row = _lock_row(batch_name)
    user = frappe.session.user
    now = now_datetime()
    if lock_is_active(row, now=now) and row.get("edit_lock_owner") != user:
        return {
            "ok": False,
            "locked_by": row.get("edit_lock_owner"),
            "expires_at": row.get("edit_lock_expires_at"),
            "message": f"当前批次正在被{row.get('edit_lock_owner') or '其他用户'}编辑。",
        }
    token = frappe.generate_hash(length=32)
    expires_at = now + timedelta(minutes=LEASE_MINUTES)
    frappe.db.set_value(
        "Overseas Cost Batch",
        row["name"],
        {
            "edit_lock_owner": user,
            "edit_lock_token": token,
            "edit_lock_acquired_at": now,
            "edit_lock_expires_at": expires_at,
        },
        update_modified=False,
    )
    return {
        "ok": True,
        "edit_token": token,
        "locked_by": user,
        "expires_at": expires_at,
        "modified": row.get("modified"),
    }


def renew_edit_session(batch_name: str, edit_token: str) -> dict:
    row = _lock_row(batch_name)
    assert_editable(row, frappe.session.user, edit_token, expected_modified=None)
    expires_at = now_datetime() + timedelta(minutes=LEASE_MINUTES)
    frappe.db.set_value(
        "Overseas Cost Batch",
        row["name"],
        "edit_lock_expires_at",
        expires_at,
        update_modified=False,
    )
    return {"ok": True, "expires_at": expires_at}


def release_edit_session(batch_name: str, edit_token: str) -> dict:
    row = _lock_row(batch_name)
    if row.get("edit_lock_owner") == frappe.session.user and row.get("edit_lock_token") == edit_token:
        frappe.db.set_value(
            "Overseas Cost Batch",
            row["name"],
            {
                "edit_lock_owner": "",
                "edit_lock_token": "",
                "edit_lock_acquired_at": None,
                "edit_lock_expires_at": None,
            },
            update_modified=False,
        )
    return {"ok": True}


def assert_batch_write(
    batch_name: str,
    *,
    edit_token: str | None,
    expected_modified: str | None,
) -> dict:
    """对已解析到批次的写操作进行统一校验。"""

    if expected_modified in (None, ""):
        raise RuntimeError("缺少数据版本，请刷新批次详情后重新修改。")
    row = _lock_row(batch_name)
    assert_editable(
        row,
        frappe.session.user,
        str(edit_token or ""),
        expected_modified,
    )
    return row
