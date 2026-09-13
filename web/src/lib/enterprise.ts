import { getToken } from "./api";

export async function enterpriseApi<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api/v1/enterprise${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${getToken()}` },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : JSON.stringify(value.detail));
  return value.data as T;
}

export const labels: Record<string, string> = {
  onboarding: "入职办理", access: "权限申请", expense: "报销检查", planning: "准备内部评测", reflecting: "复盘与提出变更",
  proposed: "候选已冻结", validation: "验证对照", holdout: "保留任务验收", canary: "模拟任务灰度", active: "工作台已启用",
  rejected: "未通过", rolled_back: "已回滚", inconclusive: "证据不足，保持原版本", failed: "执行失败", superseded: "基线已变化",
  maintenance: "维护建议", no_change: "无需修改", accumulating: "积累任务中", create: "新增", revise: "修订", select: "调整适用说明",
  retire: "淘汰", retired: "已替换或淘汰", queued: "排队中", running: "执行中", completed: "本轮处理结束",
  waiting_approval: "等待审批", submitted: "已提交", needs_information: "需要补充材料", blocked: "办理受阻",
  possible_error: "疑似执行错误", preference: "表达偏好", none: "无相关反馈", budget_exhausted: "执行预算耗尽",
  missing_result: "未交付结果", incomplete_response: "模型响应不完整", monitoring: "启用后观察",
};
