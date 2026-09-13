"""种子演示数据：合并部门、迁移误归文件、每部门模拟文档/待审核单/badcase/初始 Skill。

用法：python -m scripts.seed_demo_data

功能：
1. 合并"后勤处"与"后勤与安全保卫部"为一个部门（保留 dept_hqaq）。
2. 把教务处（dept_jwc）现有误归文件迁到后勤与安全保卫部（dept_hqaq）。
3. 为每个部门生成 3 份模拟文档，分别处于 active / review / draft 三种状态并入库。
4. 为每个部门生成 1 张待审核单（人工审核 Loop 的起点）。
5. 为每个部门生成若干 badcase（反馈 + trace），总结反思出 rubric 规则写入该部门初始 Skill。
6. 依据各部门独特规则，为每个部门生成 1 个初始 Skill（含 unique_rules + rubric_rules）。

幂等：重复执行会跳过已存在的数据。
"""
from __future__ import annotations

import asyncio
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.deps import build_container


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# 部门合并映射：from_dept -> to_dept（幂等；被合并部门删除后该迁移即无副作用）
# 注意：教务处(dept_jwc)不再整体迁移——其正常教务文档必须保留在教务处，
#       原"暴雨天气"等误归文件已一次性清理，不再参与自动迁移，避免误伤。
MIGRATE_MAP = {
    "dept_hqc": "dept_hqaq",  # 后勤处 合并进 后勤与安全保卫部
}
DELETE_DEPTS = ["dept_hqc"]


# =====================================================================
# 各部门模拟数据：skill / docs / badcases / review_qa
# =====================================================================
DEPT_DATA: list[dict[str, Any]] = [
    {
        "dept_id": "dept_jwc",
        "skill": {
            "name": "选课退课学籍查询",
            "keywords": ["选课", "退课", "退费", "学籍", "学分", "休学", "转专业", "辅修"],
            "unique_rules": [
                "教务处制度以学期校历周次为准，退课截止第8周、退费按剩余教学周比例",
                "回答必须引用具体条款编号，并与当前学期校历对照",
            ],
        },
        "docs": [
            {
                "title": "本科生选课管理办法",
                "status": "active",
                "content": "# 本科生选课管理办法\n\n## 第一章 总则\n\n第一条 为规范本科生选课管理、保障教学秩序，制定本办法。\n\n第二条 学生应在每学期第16至18周，通过教务系统完成下学期课程选课。\n\n## 第二章 退课与改选\n\n第三条 学生可在开学后第1至第8周内申请退课，逾期不予受理。\n\n第四条 退课后学费按剩余教学周比例退还，具体比例由财务处核定。",
            },
            {
                "title": "本科生退课及退费实施细则",
                "status": "review",
                "content": "# 本科生退课及退费实施细则\n\n第一条 退课以教务系统提交时间为准，审核通过后立即生效。\n\n第二条 开学第1至4周退课，退费比例为100%；第5至6周为60%；第7至8周为30%。\n\n第三条 退费将在退课审核通过后15个工作日内原路退回。",
            },
            {
                "title": "本科生学籍异动办理流程",
                "status": "draft",
                "content": "# 本科生学籍异动办理流程\n\n第一条 休学须由本人申请、家长签字、辅导员审核后报教务处批准。\n\n第二条 复学须在学期开学前两周内提出申请，并提交相关证明材料。\n\n第三条 转专业每年春季学期受理一次，按接收学院考核结果择优录取。",
            },
        ],
        "badcases": [
            {"query": "退课截止到第几周？", "bad_answer": "退课截止到第10周。", "signal": "correction",
             "rule": "退课截止时间必须对照当前学期校历，回答为第8周而非第10周"},
            {"query": "退课能退学费吗？", "bad_answer": "不能退费。", "signal": "down",
             "rule": "回答退课问题必须同时说明退费比例（按剩余教学周 100%/60%/30%）"},
            {"query": "研究生退课截止时间？", "bad_answer": "本科生第8周截止。", "signal": "correction",
             "rule": "本科生与研究生规定不可混用，需按研究生院条款回答"},
        ],
        "review_qa": [
            {"question": "本科生退课最晚可以到第几周？", "expected": "开学后第8周", "answer": "本科生可在开学后第1至第8周内申请退课，逾期不予受理。"},
            {"question": "开学第6周退课能退多少学费？", "expected": "60%", "answer": "开学第5至6周退课，退费比例为60%。"},
            {"question": "转专业每年什么时候受理？", "expected": "每年春季学期一次", "answer": "转专业每年春季学期受理一次，按接收学院考核结果择优录取。"},
        ],
    },
    {
        "dept_id": "dept_xsc",
        "skill": {
            "name": "奖助学金与宿舍请假查询",
            "keywords": ["奖学金", "助学金", "宿舍", "调换", "请假", "处分", "综测", "勤工助学"],
            "unique_rules": [
                "学生处负责奖助贷、宿舍、请假处分；申请材料与截止日期以学生处通知为准",
                "请假天数分级对应不同审批权限（辅导员/学院/学生处）",
            ],
        },
        "docs": [
            {
                "title": "本科生奖学金评定办法",
                "status": "active",
                "content": "# 本科生奖学金评定办法\n\n第一条 奖学金评定依据学业成绩与综合素质测评综合排名。\n\n第二条 申请材料包括成绩单、综测证明、获奖证书复印件，缺一不可。\n\n第三条 评定结果公示5个工作日，无异议后发放。",
            },
            {
                "title": "学生宿舍管理与调换规定",
                "status": "review",
                "content": "# 学生宿舍管理与调换规定\n\n第一条 宿舍调换须提交书面申请，经辅导员与宿管中心审核。\n\n第二条 调换申请在每学期开学后两周内集中受理。\n\n第三条 违规用电等行为将给予警告及以上处分。",
            },
            {
                "title": "学生请假及考勤管理规定",
                "status": "draft",
                "content": "# 学生请假及考勤管理规定\n\n第一条 请假1天由辅导员审批，2至7天由学院审批，7天以上报学生处审批。\n\n第二条 病假须附医院诊断证明，事假须说明事由。\n\n第三条 未经批准擅自离校按旷课处理。",
            },
        ],
        "badcases": [
            {"query": "申请奖学金要什么材料？", "bad_answer": "填个表就行。", "signal": "down",
             "rule": "回答奖学金申请必须列出完整材料清单（成绩单/综测证明/获奖证书）"},
            {"query": "宿舍怎么调换？", "bad_answer": "直接搬过去就行。", "signal": "correction",
             "rule": "宿舍调换必须说明申请渠道（辅导员+宿管中心）与受理时限（开学后两周）"},
            {"query": "请假5天找谁批？", "bad_answer": "辅导员批就行。", "signal": "correction",
             "rule": "请假天数分级对应审批权限，5天应由学院审批"},
        ],
        "review_qa": [
            {"question": "奖学金评定主要依据什么？", "expected": "学业成绩与综测排名", "answer": "奖学金评定依据学业成绩与综合素质测评综合排名。"},
            {"question": "请假2至7天由谁审批？", "expected": "学院", "answer": "请假1天由辅导员审批，2至7天由学院审批，7天以上报学生处审批。"},
            {"question": "宿舍调换申请何时集中受理？", "expected": "开学后两周内", "answer": "宿舍调换申请在每学期开学后两周内集中受理。"},
        ],
    },
    {
        "dept_id": "dept_cwc",
        "skill": {
            "name": "缴费退费与报销查询",
            "keywords": ["缴费", "退费", "学费", "报销", "发票", "收费标准", "到账", "退款"],
            "unique_rules": [
                "涉及费用金额、退费比例、报销时限必须以财务处标准为准，金额精确到分",
                "报销须说明所需票据、审批流程与时限",
            ],
        },
        "docs": [
            {
                "title": "学生收费管理办法",
                "status": "active",
                "content": "# 学生收费管理办法\n\n第一条 学费按学年收取，标准以学校公示为准。\n\n第二条 学生可通过统一支付平台或银行代扣方式缴费。\n\n第三条 逾期未缴费且未办理缓缴手续的，按学校规定处理。",
            },
            {
                "title": "经费报销与票据管理规定",
                "status": "review",
                "content": "# 经费报销与票据管理规定\n\n第一条 报销须提供合法合规发票，抬头为单位全称。\n\n第二条 报销申请应在事项完成后30日内提交。\n\n第三条 差旅报销须附审批单与行程证明。",
            },
            {
                "title": "学生退费办理流程",
                "status": "draft",
                "content": "# 学生退费办理流程\n\n第一条 退费申请经相关部门审核后，由财务处统一办理。\n\n第二条 退费款项将在审核通过后15个工作日内原路退回。\n\n第三条 退费进度可通过财务系统查询。",
            },
        ],
        "badcases": [
            {"query": "学费一年多少钱？", "bad_answer": "大概一万多吧。", "signal": "down",
             "rule": "涉及金额必须给出具体数字与依据条款，不得使用模糊表述"},
            {"query": "报销要什么发票？", "bad_answer": "随便开张发票。", "signal": "correction",
             "rule": "报销须说明合法合规发票、抬头要求与30日内时限"},
            {"query": "退费多久到账？", "bad_answer": "马上到账。", "signal": "correction",
             "rule": "退费必须说明15个工作日内原路退回的到账周期"},
        ],
        "review_qa": [
            {"question": "报销申请应在事项完成后多久内提交？", "expected": "30日内", "answer": "报销申请应在事项完成后30日内提交。"},
            {"question": "退费款项多久原路退回？", "expected": "15个工作日内", "answer": "退费款项将在审核通过后15个工作日内原路退回。"},
            {"question": "学费可以通过哪些方式缴纳？", "expected": "统一支付平台或银行代扣", "answer": "学生可通过统一支付平台或银行代扣方式缴费。"},
        ],
    },
    {
        "dept_id": "dept_rsc",
        "skill": {
            "name": "职称评聘与人事手续查询",
            "keywords": ["职称", "评聘", "引进", "合同", "考勤", "请假", "社保", "入职", "离职"],
            "unique_rules": [
                "人事处负责教职工职称评聘、入职离职、合同社保，按教职工身份区分适用条款",
                "教职工请假/考勤规定与学生不同，不得混淆",
            ],
        },
        "docs": [
            {
                "title": "教职工职称评聘管理办法",
                "status": "active",
                "content": "# 教职工职称评聘管理办法\n\n第一条 职称评聘按助理级、中级、副高级、正高级分级申报。\n\n第二条 申报须满足学历、年限、业绩等基本条件。\n\n第三条 评聘结果经评审委员会表决后公示。",
            },
            {
                "title": "教职工入职离职办理流程",
                "status": "review",
                "content": "# 教职工入职离职办理流程\n\n第一条 入职须提交学历学位证明、体检报告等材料。\n\n第二条 离职须提前30日提交书面申请并办理工作交接。\n\n第三条 社保关系在离职后按规定办理转移。",
            },
            {
                "title": "教职工考勤与请销假规定",
                "status": "draft",
                "content": "# 教职工考勤与请销假规定\n\n第一条 教职工请假须经所在部门负责人审批。\n\n第二条 病假须附医院证明，事假须说明事由。\n\n第三条 连续旷工按学校人事制度处理。",
            },
        ],
        "badcases": [
            {"query": "评副高要什么条件？", "bad_answer": "年限到了就行。", "signal": "down",
             "rule": "职称评聘必须区分申报级别并列出学历/年限/业绩条件"},
            {"query": "离职要提前多久申请？", "bad_answer": "随时能走。", "signal": "correction",
             "rule": "离职须说明提前30日书面申请与工作交接"},
            {"query": "教职工请假5天找谁批？", "bad_answer": "辅导员批。", "signal": "correction",
             "rule": "教职工请假由部门负责人审批，与学生请假权限不同"},
        ],
        "review_qa": [
            {"question": "职称评聘分哪几个级别？", "expected": "助理级/中级/副高级/正高级", "answer": "职称评聘按助理级、中级、副高级、正高级分级申报。"},
            {"question": "离职须提前多久提交申请？", "expected": "30日", "answer": "离职须提前30日提交书面申请并办理工作交接。"},
            {"question": "入职需要提交哪些材料？", "expected": "学历学位证明、体检报告等", "answer": "入职须提交学历学位证明、体检报告等材料。"},
        ],
    },
    {
        "dept_id": "dept_yjsy",
        "skill": {
            "name": "研究生培养与学位查询",
            "keywords": ["研究生", "学位", "论文", "答辩", "开题", "中期", "盲审", "导师", "培养方案"],
            "unique_rules": [
                "研究生院制度针对硕博研究生，学位论文各环节（开题/中期/预答辩/答辩）有独立时限",
                "研究生培养与学位规定不得与本科规定混用",
            ],
        },
        "docs": [
            {
                "title": "研究生培养方案与学分规定",
                "status": "active",
                "content": "# 研究生培养方案与学分规定\n\n第一条 硕士研究生总学分不少于28学分，博士研究生不少于20学分。\n\n第二条 学位课不及格须重修，重修仍不及格取消学位申请资格。\n\n第三条 研究生须在导师指导下制定个人培养计划。",
            },
            {
                "title": "研究生学位论文工作实施细则",
                "status": "review",
                "content": "# 研究生学位论文工作实施细则\n\n第一条 学位论文须依次完成开题、中期检查、预答辩、盲审与正式答辩。\n\n第二条 开题应在第二学期末前完成，中期检查在第三学期完成。\n\n第三条 盲审不通过者不得进入答辩环节。",
            },
            {
                "title": "研究生开题与中期考核办法",
                "status": "draft",
                "content": "# 研究生开题与中期考核办法\n\n第一条 开题报告经导师同意后提交学院审核。\n\n第二条 中期考核重点检查论文进展与实验数据完整性。\n\n第三条 考核不合格者须限期整改并重新考核。",
            },
        ],
        "badcases": [
            {"query": "研究生毕业要多少学分？", "bad_answer": "和本科一样20分。", "signal": "correction",
             "rule": "研究生学分要求（硕28/博20）不得与本科混淆"},
            {"query": "论文开题什么时候完成？", "bad_answer": "第三学期。", "signal": "correction",
             "rule": "开题应在第二学期末前完成，中期在第三学期"},
            {"query": "盲审不过能答辩吗？", "bad_answer": "可以。", "signal": "down",
             "rule": "盲审不通过者不得进入答辩环节"},
        ],
        "review_qa": [
            {"question": "硕士学位论文要经过哪些环节？", "expected": "开题/中期/预答辩/盲审/答辩", "answer": "学位论文须依次完成开题、中期检查、预答辩、盲审与正式答辩。"},
            {"question": "硕士研究生总学分要求是多少？", "expected": "不少于28学分", "answer": "硕士研究生总学分不少于28学分，博士研究生不少于20学分。"},
            {"question": "开题报告应何时完成？", "expected": "第二学期末前", "answer": "开题应在第二学期末前完成。"},
        ],
    },
    {
        "dept_id": "dept_zfxy",
        "skill": {
            "name": "中法合作办学事务查询",
            "keywords": ["中法", "法语", "留学", "交换", "双学位", "赴法", "行李寄存", "开题", "心理测评"],
            "unique_rules": [
                "中法学院为中外合作办学，涉及法语授课、赴法交流、双学位等特殊规定",
                "中法学院课程与本部课程不可混用",
            ],
        },
        "docs": [
            {
                "title": "中法学院学生管理办法",
                "status": "active",
                "content": "# 中法学院学生管理办法\n\n第一条 学院实行中外联合培养，核心课程以法语授课。\n\n第二条 学生须修满双方认可学分方可获得双学位。\n\n第三条 赴法交流须达到规定语言水平与学分要求。",
            },
            {
                "title": "赴法交流项目选派办法",
                "status": "review",
                "content": "# 赴法交流项目选派办法\n\n第一条 选派依据学业成绩、法语水平与综合表现择优录取。\n\n第二条 交流期间学分按协议互认，须完成对方院校规定课程。\n\n第三条 交流费用按项目协议执行，部分由学校资助。",
            },
            {
                "title": "中法学院宿舍与行李寄存规定",
                "status": "draft",
                "content": "# 中法学院宿舍与行李寄存规定\n\n第一条 寒暑假离校可申请行李寄存，须登记并张贴标签。\n\n第二条 寄存行李须自行打包，贵重物品随身携带。\n\n第三条 逾期未领取的行李按学院规定处理。",
            },
        ],
        "badcases": [
            {"query": "中法学院要学法语吗？", "bad_answer": "不用。", "signal": "down",
             "rule": "中法学院核心课程以法语授课，回答须体现法语要求"},
            {"query": "赴法交流要什么条件？", "bad_answer": "交钱就能去。", "signal": "correction",
             "rule": "赴法交流须说明语言水平、学分与综合表现要求"},
            {"query": "中法学院宿舍规定和本部一样吗？", "bad_answer": "一样。", "signal": "correction",
             "rule": "中法学院宿舍/行李寄存等后勤事务以学院通知为准，不与本部混用"},
        ],
        "review_qa": [
            {"question": "中法学院实行怎样的培养模式？", "expected": "中外联合培养，核心课程法语授课", "answer": "学院实行中外联合培养，核心课程以法语授课。"},
            {"question": "如何获得双学位？", "expected": "修满双方认可学分", "answer": "学生须修满双方认可学分方可获得双学位。"},
            {"question": "寒暑假离校行李如何处理？", "expected": "可申请寄存并登记贴标签", "answer": "寒暑假离校可申请行李寄存，须登记并张贴标签。"},
        ],
    },
    {
        "dept_id": "dept_hqaq",
        "skill": {
            "name": "后勤服务与安全应急查询",
            "keywords": ["后勤", "食堂", "宿舍维修", "报修", "台风", "暴雨", "防汛", "应急", "安全", "停电"],
            "unique_rules": [
                "后勤与安全保卫部负责校园后勤保障与安全应急，应急电话与处置流程必须准确",
                "台风/暴雨等气象预警须给出具体分级响应措施",
            ],
        },
        "docs": [
            {
                "title": "校园防汛应急响应预案",
                "status": "active",
                "content": "# 校园防汛应急响应预案\n\n第一条 暴雨橙色及以上预警时启动应急响应，及时发布安全提示。\n\n第二条 师生应关注雨情预警，非必要不外出，避开低洼积水区域。\n\n第三条 遇公寓漏水、积水、设施损毁、电路故障等险情，切勿自行处置，第一时间拨打校园应急电话0571-28881110。",
            },
            {
                "title": "学生公寓管理与报修办法",
                "status": "review",
                "content": "# 学生公寓管理与报修办法\n\n第一条 公寓设施故障可通过线上报修平台或电话报修。\n\n第二条 报修后维修人员将在1个工作日内响应。\n\n第三条 人为损坏的设施由责任人承担维修费用。",
            },
            {
                "title": "校园安全突发事件处置指引",
                "status": "draft",
                "content": "# 校园安全突发事件处置指引\n\n第一条 发生火灾、台风、地震等突发事件应听从统一指挥有序疏散。\n\n第二条 发现安全隐患应及时向安全保卫部报告。\n\n第三条 夜间突发情况可拨打校园24小时值班电话。",
            },
        ],
        "badcases": [
            {"query": "暴雨红色预警怎么办？", "bad_answer": "该干嘛干嘛。", "signal": "down",
             "rule": "气象预警须给出具体响应措施：非必要不外出、避开积水区域"},
            {"query": "公寓漏水打什么电话？", "bad_answer": "找宿管阿姨。", "signal": "correction",
             "rule": "安全应急类回答必须给出校园应急电话0571-28881110"},
            {"query": "宿舍灯坏了怎么报修？", "bad_answer": "自己修。", "signal": "correction",
             "rule": "报修须说明线上报修平台/电话渠道与1个工作日响应时限"},
        ],
        "review_qa": [
            {"question": "暴雨橙色预警时学校启动什么？", "expected": "应急响应并发布安全提示", "answer": "暴雨橙色及以上预警时启动应急响应，及时发布安全提示。"},
            {"question": "公寓漏水应如何处理？", "expected": "切勿自行处置，拨打0571-28881110", "answer": "遇公寓漏水、积水、设施损毁、电路故障等险情，切勿自行处置，第一时间拨打校园应急电话0571-28881110。"},
            {"question": "报修后多久响应？", "expected": "1个工作日内", "answer": "报修后维修人员将在1个工作日内响应。"},
        ],
    },
]


# =====================================================================
async def migrate_departments(container) -> None:
    store = container.store
    for from_dept, to_dept in MIGRATE_MAP.items():
        moved = 0
        # documents
        for d in await store.list_documents(dept_id=from_dept):
            d["dept_id"] = to_dept
            await store.upsert("documents", d)
            moved += 1
        # chunks
        for c in await store.list_all_chunks(dept_id=from_dept):
            c["dept_id"] = to_dept
            await store.upsert("chunks", c)
        # doc_relations
        for r in await store.list_relations():
            changed = False
            if r.get("from_dept") == from_dept:
                r["from_dept"] = to_dept
                changed = True
            if r.get("to_dept") == from_dept:
                r["to_dept"] = to_dept
                changed = True
            if changed:
                await store.upsert("doc_relations", r)
        # review orders
        for o in await store.list_review_orders(dept_id=from_dept):
            o["dept_id"] = to_dept
            await store.upsert("review_orders", o)
        # test questions
        for q in await store.list_test_questions(dept_id=from_dept):
            q["dept_id"] = to_dept
            await store.upsert("test_questions", q)
        print(f"[迁移] {from_dept} -> {to_dept}: 文档 {moved} 份（含 chunks/关联/审核单/题库）")

    for dept_id in DELETE_DEPTS:
        if await store.get_department(dept_id) is not None:
            await store.delete("departments", dept_id)
            await store.delete("dept_memory", dept_id)
            print(f"[合并] 已删除冗余部门 {dept_id}")


async def seed_docs(container) -> None:
    store = container.store
    tmp_dir = Path(tempfile.mkdtemp(prefix="wenshu_seed_"))
    try:
        for item in DEPT_DATA:
            dept_id = item["dept_id"]
            existing = {d.get("title") for d in await store.list_documents(dept_id=dept_id)}
            for spec in item["docs"]:
                title = spec["title"]
                if title in existing:
                    print(f"[跳过] 已存在 {dept_id}《{title}》")
                    continue
                fp = tmp_dir / f"{title}.md"
                fp.write_text(spec["content"], encoding="utf-8")
                try:
                    doc = await container.indexer.ingest(str(fp), dept_id=dept_id, uploaded_by="seed_demo")
                    # 设置不同状态 + 记录 pipeline 阶段
                    await store.update_document(doc["_id"], {
                        "status": spec["status"],
                        "pipeline_stages": [
                            {"key": "upload", "name": "上传", "done": True},
                            {"key": "parse", "name": "格式解析", "done": True},
                            {"key": "clean", "name": "清洗", "done": True},
                            {"key": "chunk", "name": "语义切片", "done": True, "detail": f"{doc.get('chunk_count', 0)} 切片"},
                            {"key": "metadata", "name": "元数据提取", "done": True, "detail": doc.get("doc_type", "other")},
                            {"key": "vectorize", "name": "向量化", "done": doc.get("vector_status") == "ready"},
                            {"key": "index", "name": "索引构建", "done": doc.get("vector_status") == "ready"},
                            {"key": "relations", "name": "跨部门关联挖掘", "done": True, "detail": "0 关联"},
                        ],
                    })
                    print(f"[文档] {dept_id}《{title}》(status={spec['status']}, {doc.get('chunk_count', 0)} chunks)")
                except Exception as exc:  # noqa: BLE001
                    print(f"[失败] {dept_id}《{title}》: {exc}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def seed_review_orders(container) -> None:
    store = container.store
    for item in DEPT_DATA:
        dept_id = item["dept_id"]
        order_id = f"review_{dept_id}_seed"
        if await store.get_review_order(order_id) is not None:
            print(f"[跳过] 审核单已存在 {order_id}")
            continue
        qa_pairs = [
            {
                "question": q["question"],
                "expected": q["expected"],
                "answer": q["answer"],
                "citations": [],
                "confidence": 0.8,
                "verdict": None,
                "correct": None,
                "correction": "",
            }
            for q in item["review_qa"]
        ]
        order = {
            "_id": order_id,
            "dept_id": dept_id,
            "doc_id": "",
            "doc_title": item["skill"]["name"] + "（模拟审核单）",
            "status": "pending",
            "qa_pairs": qa_pairs,
            "total": len(qa_pairs),
            "correct": 0,
            "accuracy": None,
            "created_at": _now(),
            "reviewed_at": None,
            "reviewed_by": None,
        }
        await store.insert_review_order(order)
        print(f"[审核单] {dept_id} 生成待审核单 {order_id} ({len(qa_pairs)} 题)")


async def seed_badcases_and_skills(container) -> None:
    store = container.store
    for item in DEPT_DATA:
        dept_id = item["dept_id"]
        skill_spec = item["skill"]

        # 1) badcase：写入 feedback + traces（供 Loop Reflect）
        #    演示 badcase 每次重跑都恢复为"未消费"，保证"手动触发 Loop"始终有反馈可观察
        for i, bc in enumerate(item["badcases"]):
            fb_id = f"fb_{dept_id}_{i}"
            await store.upsert("feedback", {
                "_id": fb_id,
                "session_id": "",
                "user_id": "student",
                "query": bc["query"],
                "answer": bc["bad_answer"],
                "kind": "explicit",
                "signal": bc["signal"],
                "detail": {"root_cause": "retrieval" if bc["signal"] == "down" else "generation", "rule": bc["rule"], "dept_id": dept_id},
                "consumed": False,
                "created_at": _now(),
            })
            trace_id = f"trace_{dept_id}_bad_{i}"
            if await store.get("traces", trace_id) is None:
                await store.upsert("traces", {
                    "_id": trace_id,
                    "session_id": "",
                    "user_id": "student",
                    "query": bc["query"],
                    "answer": bc["bad_answer"],
                    "intent": {"type": "regulation_consult", "depts": [dept_id]},
                    "verification": {"passed": False, "score": 0.4, "issues": [bc["rule"]]},
                    "latency_ms": 1200,
                    "success": False,
                    "created_at": _now(),
                })

        # 1.5) good case：正确回答的 👍 反馈（用于计算回答采纳率，标记已消费、不进入 Loop 队列）
        for g_i, gq in enumerate(item["review_qa"][:1]):
            await store.upsert("feedback", {
                "_id": f"fb_{dept_id}_up_{g_i}",
                "session_id": "",
                "user_id": "student",
                "query": gq["question"],
                "answer": gq["answer"],
                "kind": "explicit",
                "signal": "up",
                "detail": {},
                "consumed": True,
                "created_at": _now(),
            })

        # 2) 初始 skill：unique_rules（部门独特规则）+ rubric_rules（初始为空，运行 Loop 后由 Reflect 沉淀）
        skill_id = f"skill_{dept_id}_seed"
        skill = {
            "_id": skill_id,
            "name": skill_spec["name"],
            "dept_id": dept_id,
            "scope": "department",
            "trigger": {
                "intent_patterns": skill_spec["keywords"],
                "entities_required": ["matter"],
                "confidence_threshold": 0.75,
            },
            "action": {
                "type": "workflow",
                "steps": [
                    {"step": 1, "action": "extract_entity", "params": {"entity": "matter"}},
                    {"step": 2, "action": "retrieve", "params": {"query": "{matter} 相关制度", "top_k": 5}},
                    {"step": 3, "action": "generate", "params": {"template": "department"}},
                ],
            },
            "unique_rules": skill_spec["unique_rules"],
            "rubric_rules": [],
            "metrics": {"trigger_count": 0, "success_rate": 0.0, "avg_latency_ms": 0, "last_triggered": ""},
            "version": 1,
            "status": "active",
            "auto_generated": False,
            "created_by": "seed",
            "created_at": _now(),
        }
        await store.upsert_skill(skill)
        print(f"[badcase] {dept_id} 已准备 {len(item['badcases'])} 条未消费反馈 + {len(item['badcases'])} 条失败 trace + 1 条 👍 反馈")
        print(f"[Skill] {dept_id} 初始技能《{skill_spec['name']}》：{len(skill_spec['unique_rules'])} 条独特规则（rubric 规则运行 Loop 后沉淀）")


async def main() -> None:
    settings = get_settings()
    container = build_container(settings)
    if container.mongo is not None:
        await container.mongo.connect()
    if hasattr(container.session_store, "connect"):
        try:
            await container.session_store.connect()
        except Exception:
            pass

    print("== 1/4 合并部门 + 迁移误归文件 ==")
    await migrate_departments(container)

    print("\n== 2/4 每部门生成 3 份模拟文档（active/review/draft） ==")
    await seed_docs(container)

    print("\n== 3/4 每部门生成待审核单 ==")
    await seed_review_orders(container)

    print("\n== 4/4 生成 badcase 并反思出 rubric 规则写入初始 Skill ==")
    await seed_badcases_and_skills(container)

    print("\n完成。")
    if container.mongo is not None:
        await container.mongo.close()


if __name__ == "__main__":
    asyncio.run(main())
