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
