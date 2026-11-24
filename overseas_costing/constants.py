"""
中文用途：后端全局常量定义文件。

后续所有状态枚举、版本类型、运输方式、分摊方式尽量统一从这里取值，
避免业务代码里散落硬编码字符串。
"""

TRANSPORT_MODES = ("SEA", "AIR", "EXPRESS")
TRANSPORT_MODE_LABELS = {
    "SEA": "海运",
    "AIR": "空运",
    "EXPRESS": "快递",
}

BUSINESS_TYPES = (
    "SEA_STANDARD",
    "SEA_DDP",
    "AIR_DDP",
    "AIR_STANDARD",
    "EXPRESS",
)
BUSINESS_TYPE_LABELS = {
    "SEA_STANDARD": "海运正报正清",
    "SEA_DDP": "海运 DDP（双清包税）",
    "AIR_DDP": "空运 DDP（双清包税）",
    "AIR_STANDARD": "正常空运",
    "EXPRESS": "快递",
}

BATCH_STATUSES = (
    "Draft",
    "Imported",
    "Dirty",
    "Calculated",
    "Confirmed",
    "Writeback Failed",
    "Written Back",
)

BATCH_SOURCE_TYPES = (
    "excel",
    "oa_logistics",
    "erp_purchase",
    "manual",
    "attachment_parse",
)

BATCH_CONFIRM_STATUSES = ("Pending", "Partially Confirmed", "Confirmed")
BATCH_WRITEBACK_STATUSES = ("Not Started", "Pending", "Failed", "Success")

VERSION_TYPES = ("Estimated", "Actual", "Adjustment")
VERSION_SOURCE_TYPES = ("Import", "Clone", "Manual")

ALLOCATION_BASES = ("goods_value", "gross_weight", "volume", "chargeable_weight")

AUDIT_ACTION_TYPES = (
    "IMPORT",
    "EDIT",
    "BATCH_EDIT",
    "RECALCULATE",
    "CREATE_VERSION",
    "SWITCH_VERSION",
    "UPLOAD_ATTACHMENT",
    "WRITEBACK",
    "REVIEW_RETURN",
    "REVIEW_REPLY",
    "REVIEW_RESUBMIT",
    "REVIEW_RESOLVE",
)

USAGE_ACTION_TYPES = (
    "PAGE_VIEW",
    "BATCH_VIEW",
    "DINGTALK_PULL",
    "EXCEL_IMPORT",
    "FILE_PARSE",
    "RECALCULATE",
    "CONFIRM_RESULT",
    "PREVIEW_ERP",
    "PUSH_ERP",
    "EXPORT",
    "DATA_CHECK",
    "ATTACHMENT_VIEW",
    "OTHER",
)

USAGE_STATUSES = ("Success", "Failed")
