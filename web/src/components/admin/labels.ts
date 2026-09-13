/** Loop 阶段与状态的展示映射。 */

export const PHASE_LABEL: Record<string, string> = {
  human_in_loop: "人在环中",
  human_on_loop: "人在环上",
  human_out_of_loop: "人在环外",
};

export const PHASE_DESC: Record<string, string> = {
  human_in_loop: "所有自动产物需人工审核后生效，人是 100% 决策者",
  human_on_loop: "高置信度自动生效，低置信度推审核队列，人是审核者",
  human_out_of_loop: "圈定范围内全自动运行，人只设边界与目标，人是监督者",
};

export function phaseLabel(phase?: string): string {
  return PHASE_LABEL[phase || ""] || phase || "未知";
}

export function phaseBadgeClass(phase?: string): string {
  if (phase === "human_out_of_loop") return "badgeGreen";
  if (phase === "human_on_loop") return "badgeAmber";
  return "badgeBlue";
}

export const LOOP_STAGES = [
  { key: "execute", name: "Execute", desc: "按当前 Skills/Hooks/Rules 回答并记录 trace" },
  { key: "observe", name: "Observe", desc: "收集显式/隐式/自动反馈信号" },
  { key: "reflect", name: "Reflect", desc: "分析 bad case 根因（检索/意图/生成/缺口）" },
  { key: "adapt", name: "Adapt", desc: "生成 Skill/Hook/Rule 更新进入审核" },
  { key: "deploy", name: "Deploy", desc: "灰度发布、回测后全量，回到 Execute" },
];
