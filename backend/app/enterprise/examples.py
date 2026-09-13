"""Editable workbench inputs, not benchmark answers or prewritten histories."""
def examples():
    cases = [
        ("onboarding", "请为新同事办理入职，按当前岗位和人员类型准备申请。",
         {"name": "新同事", "role": "后端工程师", "employment": "正式", "manager": "团队负责人"}, [],
         "所有员工需要邮箱和设备申请。正式研发员工还需仓库申请；外包员工改为外包审核。缺负责人先询问，不建单。已有申请不重复。",
         [("mailbox", "IT", False), ("device", "行政", False), ("repository", "IT", True), ("contractor_review", "人事", True)]),
        ("access", "请帮我申请项目知识库访问权限。",
         {"name": "项目同事", "resource": "项目知识库", "manager": "团队负责人"}, [],
         "权限需要负责人和资源名称，缺信息先询问。信息齐全提交资源权限申请，等待审批，不直接开通。已有申请不重复。",
         [("standard_access", "IT", True)]),
        ("expense", "请核对这次差旅报销材料并办理申请。",
         {"name": "出差同事", "expense_type": "差旅", "manager": "团队负责人"}, ["invoice", "itinerary"],
         "差旅报销需要invoice、business_purpose、itinerary。缺材料先询问，不提交；齐全后创建expense_review，等待财务审批，不付款。已有申请不重复。",
         [("expense_review", "财务", True)]),
    ]
    return [{"id": domain, "input": {"question": question, "domain": domain, "environment": {
        "employee": employee, "materials": materials, "requests": [], "policy": {
            "id": f"workbench-{domain}", "version": 1, "status": "active", "content": policy,
            "request_types": [{"kind": k, "department": d, "approval_required": a} for k, d, a in types]}}}}
        for domain, question, employee, materials, policy, types in cases]
